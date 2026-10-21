"""Bounded issuer/FDA feeds; entry metadata is never a validated body claim."""
import hashlib

from email.utils import parsedate_to_datetime
from datetime import timezone
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from .acquisition import MAX_BYTES, Deferred, parse_atom, extract_document_text
from .evidence import public_url, source_decision, document
from .identities import instant, digest
from .runner import analyze


def parse_feed(raw, *, url, cap=100, route_id=None):
    if type(cap) is not int or not 1 <= cap <= 100: raise ValueError("feed cap must be 1..100")
    if route_id:
        from .route_contracts import route_plan
        contract=route_plan(route_id)
        if contract["format"]!="rss" or contract["url"]!=url: raise ValueError("feed route contract mismatch")
    if len(raw)>MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe or oversized feed")
    root=ET.fromstring(raw)
    if root.tag=="{http://www.w3.org/2005/Atom}feed":
        return parse_atom(raw,base_url=url,result_cap=cap)
    if root.tag!="rss": raise ValueError("unsupported feed")
    items=root.findall("./channel/item"); rows=[]; gaps=[]; identities={}
    if not items: gaps.append("empty_feed_inventory")
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
        native=item.findtext("guid")
        if route_id and not native:
            gaps.append("stable_native_id_missing"); continue
        native=native or original_link
        row={"native_id":native,"title":title,"url":link,"source_link":original_link,
             "published":published,"source_date_raw":item.findtext("pubDate"),
             "time_semantics":"feed_publication_not_proven_first_availability", "signal_eligible":False}
        if native in identities:
            gaps.append("conflicting_duplicate_native_id" if identities[native]!=row else "duplicate_native_id")
            continue
        identities[native]=row; rows.append(row)
    return {"rows":rows,"gaps":sorted(set(gaps)),"result_cap_reached":len(items)>=cap,
            "retention":"unknown","stable_id_longitudinal_validation":"pending","items_received":len(items)}


def feed_query_plan(feed_url):
    return {"url":feed_url}


def watch_feed(store, client, registry, *, feed_url, now, hydrate=False, route_id=None, policies=None,
               target_family=None, measurement_proposal=None, measurement_arm=None, current_coverage=None,
               instruments=None, allow_synthetic_measurement=False):
    if route_id is None:
        from .route_contracts import ROUTES
        route_id=next((key for key,contract in ROUTES.items() if contract.get("url")==feed_url),None)
    source_id=client.policy["policy_id"]
    from .collection_context import prepare_scope
    query_plan=feed_query_plan(feed_url)
    family,measurement_metrics=prepare_scope(store,registry,policies or {source_id:client.policy},
        source_id=source_id,route_id=route_id or "explicit-policy-feed",query=query_plan,now=now,
        default_family=client.policy.get("source_class","unknown"),target_family=target_family,
        measurement_proposal=measurement_proposal,measurement_arm=measurement_arm,current_coverage=current_coverage,
        instruments=instruments,allow_synthetic_measurement=allow_synthetic_measurement)
    current=store.cursor(source_id); previous=current["watermark"]
    gaps=[]; documents=[]; events=[]; decisions=[]; leads=[]; newest=None; observations=[]
    if route_id:
        from .route_contracts import check_route
        check_route(client.policy,route_id,feed_url,store=store,now=now)
    try:
        raw=client.get(feed_url)
        result=parse_feed(raw,url=feed_url,route_id=route_id)
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
    from .coverage import observation_from_rows
    if "raw" in locals():
        observations=[observation_from_rows(rows,source_id=source_id,route_id=route_id or "explicit-policy-feed",
            family=family,acquired_at=now,window_start=now,window_end=now,
            policy_hash=digest(client.policy),raw_response_hash=hashlib.sha256(raw).hexdigest(),query=query_plan,
            gaps=result["gaps"],capped=result["result_cap_reached"],end_of_results=False)]
    proof={"contract":"omni-feed-traversal-1","source_id":source_id,"previous_watermark":previous,
           "watermark":newest,"started_from_head":True,"gaps":sorted(set(gaps)),"pages":[{
           "offset":0,"raw_sha256":hashlib.sha256(raw).hexdigest() if "raw" in locals() else "",
           "native_ids":[r["native_id"] for r in rows],"publication_times":stamps,"response_complete":not result.get("gaps")} ]}
    proof["proof_hash"]=digest(proof)
    from .coverage import feed_observation_window
    window_start,window_end=feed_observation_window(store,source_id=source_id,route_id=route_id or "explicit-policy-feed",
        family=family,query_hash=digest(query_plan),policy_hash=digest(client.policy),proof=proof,now=now)
    for observation in observations:
        observation.update(window_start=window_start,window_end=window_end,
            capped=result["result_cap_reached"] and not (reached and not gaps),end_of_results=reached and not gaps)
        observation["request_context"]={"previous_watermark":previous,"page_result_cap_reached":result["result_cap_reached"]}
        observation["request_context_hash"]=digest(observation["request_context"])
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
            found,checked,new_leads=analyze(doc,registry,policies or {source_id:client.policy},now=now)
            leads.extend(new_leads)
            documents.append(doc); events.extend(found); decisions.extend(checked)
            row["hydration_status"]="parsed" if parsed["body_complete"] else "content_incomplete"
        except (Deferred, ValueError, PermissionError) as exc: row["hydration_status"]=str(exc)
    receipt=store.commit_cycle(cycle_id="cycle:"+digest([source_id,now,rows]),source_id=source_id,observed_at=now,
        documents=documents,events=events,decisions=decisions,candidates=rows,leads=leads,watermark=newest,registry=registry,
        complete=not gaps,initial_seed=not previous,gaps=gaps,coverage_proof=proof,coverage_observations=observations,
        metrics={"requests":dict(client.counts),"request_counts":dict(client.counts),"metadata_candidates":len(rows),
                 "hydrated":len(documents),"result_cap_reached":result["result_cap_reached"],**measurement_metrics})
    return {**receipt,"coverage_gaps":gaps,"candidates":len(rows),"external_delivery":False}
