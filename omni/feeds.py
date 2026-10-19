"""Bounded issuer/FDA feeds; entry metadata is never a validated body claim."""
from email.utils import parsedate_to_datetime
from datetime import timezone
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from .acquisition import MAX_BYTES, Deferred, parse_atom, extract_document_text
from .evidence import public_url, source_decision, document
from .identities import instant, digest
from .runner import analyze


def parse_feed(raw, *, url, cap=100):
    if len(raw)>MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe or oversized feed")
    root=ET.fromstring(raw)
    if root.tag=="{http://www.w3.org/2005/Atom}feed":
        return parse_atom(raw,base_url=url,result_cap=cap)
    if root.tag!="rss": raise ValueError("unsupported feed")
    items=root.findall("./channel/item"); rows=[]; gaps=[]
    for item in items[:cap]:
        link=urljoin(url,item.findtext("link") or "")
        original_link=link
        parsed_link=urlsplit(link)
        if parsed_link.scheme=="http" and parsed_link.hostname==urlsplit(url).hostname:
            # Some official feeds still emit same-host HTTP links. Upgrade the
            # scheme explicitly, preserve provenance, and retain all URL checks.
            link=parsed_link._replace(scheme="https").geturl()
        title=item.findtext("title")
        try:
            stamp=parsedate_to_datetime(item.findtext("pubDate"))
            if stamp.tzinfo is None: raise ValueError("source timezone unknown")
            published=stamp.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            published=None; gaps.append("unusable_source_time")
        if not title or not public_url(link): gaps.append("malformed_feed_entry"); continue
        rows.append({"native_id":item.findtext("guid") or original_link,"title":title,"url":link,
                     "source_link":original_link,"published":published,"signal_eligible":False})
    return {"rows":rows,"gaps":sorted(set(gaps)),"result_cap_reached":len(items)>=cap}


def watch_feed(store, client, registry, *, feed_url, now, hydrate=False):
    source_id=client.policy["policy_id"]
    current=store.cursor(source_id); previous=current["watermark"]
    gaps=[]; documents=[]; events=[]; decisions=[]; leads=[]; newest=None
    try:
        result=parse_feed(client.get(feed_url),url=feed_url)
        rows=result["rows"]; gaps.extend(result["gaps"])
    except (Deferred, ValueError, ET.ParseError) as exc:
        rows=[]; result={"result_cap_reached":False}; gaps.append(str(exc))
    stamps=[r["published"] for r in rows if r["published"]]
    if stamps: newest=max(stamps,key=instant)
    reached=bool(previous and any(instant(s)<=instant(previous) for s in stamps))
    if previous and not reached:
        gaps.append("feed_window_before_watermark")
    if result["result_cap_reached"] and not reached:
        gaps.append("feed_result_cap")
    if not previous: gaps.append("initial_window_only")
    if hydrate:
        combined={r["native_id"]:r for r in store.backlog(source_id)}; combined.update({r["native_id"]:r for r in rows}); rows=list(combined.values())
    for row in rows:
        row["hydration_status"]="metadata_only"
        if not hydrate: continue
        reason=source_decision(client.policy,row["url"],"store_text") or source_decision(client.policy,row["url"],"local_analysis")
        if reason: row["hydration_status"]=reason; continue
        try:
            parsed=extract_document_text(client.get(row["url"],kind="document"))
            doc=document(policy=client.policy,url=row["url"],native_id=row["native_id"],text=parsed["text"],title=row["title"],published=row["published"],observed_at=now,
                source_class=client.policy["source_class"],reporting_party=client.policy["reporting_parties"][0],body_complete=parsed["body_complete"],completeness_reasons=parsed["gaps"],evidence_kind="prospective")
            found,checked,new_leads=analyze(doc,registry,{source_id:client.policy},now=now)
            leads.extend(new_leads)
            documents.append(doc); events.extend(found); decisions.extend(checked)
            row["hydration_status"]="parsed" if parsed["body_complete"] else "content_incomplete"
        except (Deferred, ValueError, PermissionError) as exc: row["hydration_status"]=str(exc)
    receipt=store.commit_cycle(cycle_id="cycle:"+digest([source_id,now,rows]),source_id=source_id,observed_at=now,
        documents=documents,events=events,decisions=decisions,candidates=rows,leads=leads,watermark=newest,
        complete=not gaps,initial_seed=not previous,gaps=gaps,
        metrics={"requests":dict(client.counts),"metadata_candidates":len(rows),"hydrated":len(documents),"result_cap_reached":result["result_cap_reached"]})
    return {**receipt,"coverage_gaps":gaps,"candidates":len(rows),"external_delivery":False}
