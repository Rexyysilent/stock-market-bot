"""Local, rights-filtered neutral previews, exports and searchable revision history."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re

from .identities import digest, instant
from .lifecycle import implementation_hash, reference
from .receipts import get_receipt, list_receipts, put_receipt
from .review import build_brief, event_packet
from .rights import field_decision

VERSION = "omni-reviewed-brief-1"
REQUIRED = ("source.url", "source.times", "claim.value", "claim.scope", "review.decision", "coverage")


def preview_brief(store, registry, policies, *, now, scope_hash, purpose="review_display", audience="local_operator",
                  mode="reviewed", allow_synthetic=False, persist=False):
    if purpose not in {"review_display","local_export"} or not scope_hash:
        raise ValueError("explicit local brief scope and purpose required")
    raw=build_brief(store,registry,policies,now=now,mode=mode)
    cards=[]; withheld=list(raw["withheld"]); rights_refs=set(); packet_bindings=[]
    for card in raw["cards"]:
        packet=event_packet(store,card["event_id"],card["event_revision"])
        docs=packet["documents"]+packet.get("scope_documents",[])
        synthetic=any(d.get("evidence_kind")=="synthetic" for d in docs)
        if synthetic and not allow_synthetic:
            withheld.append({"event_id":card["event_id"],"reason_codes":["synthetic_content_not_actual_brief"]}); continue
        decisions={}
        needed=REQUIRED+(("correction.history",) if card["corrections"] else ())
        for doc in docs:
            for field in set(needed)|{"claim.numeric","claim.amount","review.identity","source.title"}:
                decision=field_decision(store,reference(doc),field,purpose,audience,policies,now=now,allow_synthetic=allow_synthetic)
                decisions[(doc["evidence_id"],field)]=decision
        blocked=[field for field in needed if any(not decisions[(d["evidence_id"],field)]["allowed"] for d in docs)]
        if blocked:
            withheld.append({"event_id":card["event_id"],"reason_codes":["field_rights_pending"],"withheld_fields":sorted(set(blocked))}); continue
        def allowed(field): return all(decisions[(d["evidence_id"],field)]["allowed"] for d in docs)
        for decision in decisions.values():
            if decision["allowed"]: rights_refs.update(decision["rights_refs"])
        claims=[]
        for claim in card["claims"]:
            if claim["predicate"] in {"clinical_numeric_result"} and not allowed("claim.numeric"): continue
            if claim["predicate"] in {"scoped_amount"} and not allowed("claim.amount"): continue
            claims.append({k:claim[k] for k in ("predicate","value","reporting_party","units","qualifiers")})
        if not claims:
            withheld.append({"event_id":card["event_id"],"reason_codes":["all_claim_fields_withheld"]}); continue
        # Revision-insensitive origin + semantic event scope is the countable unit;
        # revisions/copies remain visible in evidence/history, never extra yield.
        unit="reviewed-unit:"+digest([card["assertion_origins"],card["family"],card["scope_id"]])
        safe={"event_id":card["event_id"],"event_revision":card["event_revision"],"event_unit_id":unit,
            "family":card["family"],"scope_id":card["scope_id"],"subject_ids":card["subject_ids"],
            "claims":claims,"source_attribution":[{"source_id":d["source_policy_id"],"url":d["url"],
                "evidence_revision":d["revision_id"],"reporting_party":d["reporting_party"],"publication":d["publication"],
                **({"title":d["title"]} if allowed("source.title") else {})} for d in docs],
            "first_observed_at":card["first_observed_at"],"reviewed_at":card["reviewed_at"],
            "recorded_review":"approved","human_authentication":"not_proved_by_software",
            "ai_domain_labels":"pending_human_review","current_state_hash":card["current_state_hash"],
            "correction_refs":card["corrections"],"coverage_tier":card["coverage_tier"],
            "independence_assessed":False,"signal_eligible":False,
            "evidence_kind":"synthetic" if synthetic else "operator_evidence"}
        if allowed("review.identity"): safe["reviewer"]=card["reviewer"]
        # Proof spans include verbatim source text and belong only in the
        # separately permissioned source.text packet view.
        if allowed("claim.numeric"):
            from .clinical_numerics import semantic
            safe["clinical_context"]=semantic(card["clinical_context"])
        if allowed("claim.amount"):
            from .amounts import amount_semantic
            safe["scoped_amounts"]=amount_semantic(card.get("scoped_amounts",[]))
        cards.append(safe)
        packet_bindings.append({"event_id":card["event_id"],"event_revision":card["event_revision"],
                                "current_state_hash":card["current_state_hash"]})
    synthetic=any(c["evidence_kind"]=="synthetic" for c in cards)
    result={"contract":VERSION,"generated_at":now,"scope_hash":scope_hash,"purpose":purpose,"audience":audience,
        "implementation_hash":implementation_hash(),"policy_hash":digest(policies),"registry_hash":registry.fingerprint(),
        "cards":cards,"withheld":withheld,"rights_refs":sorted(rights_refs),"packet_bindings":packet_bindings,
        "coverage_limitations":["Only explicitly reviewed event cards are included.",
            "A fetched inventory or backfill does not establish continuous coverage or original source availability.",
            "Unacquired, capped, deferred and unknown sources remain coverage debt."],
        "corrections_visible":True,"history_search_command":"python -m omni --db PATH history-search --query EVENT_ID",
        "evidence_kind":"synthetic" if synthetic else "operator_evidence" if cards else "none",
        "ai_domain_labels":"pending_human_review","automatic_activation":False,"signal_eligible":False,
        "external_distribution":"not_authorized","mode":mode,"budget_deferred":raw["budget_deferred"]}
    if persist:
        return put_receipt(store,"reviewed_brief",result,observed_at=now,refs=sorted(rights_refs))
    return result


def validate_brief(store, receipt_id, registry, policies, *, now, purpose=None, audience=None, allow_synthetic=False):
    from .rights import validate_rights
    from .lifecycle import validate_current
    envelope=get_receipt(store,receipt_id,kind="reviewed_brief"); p=envelope["payload"]
    if (p.get("contract")!=VERSION or p.get("implementation_hash")!=implementation_hash()
            or p.get("policy_hash")!=digest(policies) or p.get("registry_hash")!=registry.fingerprint()):
        raise ValueError("brief version changed")
    if purpose and p.get("purpose")!=purpose or audience and p.get("audience")!=audience:
        raise ValueError("brief purpose/audience mismatch")
    if p.get("evidence_kind")=="synthetic" and not allow_synthetic:
        raise ValueError("synthetic brief cannot authorize actual export")
    if instant(envelope["observed_at"])>instant(now): raise ValueError("future brief receipt")
    for ref in p["rights_refs"]: validate_rights(store,ref,policies,now=now,allow_synthetic=allow_synthetic)
    for binding in p["packet_bindings"]:
        packet=event_packet(store,binding["event_id"],binding["event_revision"])
        review=next((value for row in store.conn.execute("SELECT payload FROM reviews WHERE event_id=? AND revision_id=? ORDER BY rowid DESC",
            (binding["event_id"],binding["event_revision"]))
            if instant((value:=json.loads(row[0]))["reviewed_at"])<=instant(now)),None)
        decision=validate_current(store,packet,registry,policies,now=now,review=review)
        if decision["editorial_status"]!="eligible" or binding["current_state_hash"]!=decision["current_state_hash"]:
            raise ValueError("brief source/review state changed; regenerate current preview")
    # Hash integrity alone cannot authorize a caller-fabricated brief envelope.
    # Recompute current permitted cards and compare every emitted field.
    expected=preview_brief(store,registry,policies,now=now,scope_hash=p["scope_hash"],purpose=p["purpose"],
        audience=p["audience"],mode=p["mode"],allow_synthetic=allow_synthetic)
    current={c["event_id"]:c for c in expected["cards"]}
    if len({c["event_id"] for c in p["cards"]})!=len(p["cards"]):
        raise ValueError("duplicate brief cards")
    if any(current.get(c["event_id"])!=c for c in p["cards"]):
        raise ValueError("brief card is not an exact current permitted projection")
    bindings=[{"event_id":c["event_id"],"event_revision":c["event_revision"],"current_state_hash":c["current_state_hash"]} for c in p["cards"]]
    if p["packet_bindings"]!=bindings:
        raise ValueError("brief packet binding inventory mismatch")
    for key in ("coverage_limitations","ai_domain_labels","automatic_activation","signal_eligible","external_distribution"):
        if p.get(key)!=expected[key]: raise ValueError("brief safety/disclosure contract changed")
    derived_kind="synthetic" if any(c["evidence_kind"]=="synthetic" for c in p["cards"]) else "operator_evidence" if p["cards"] else "none"
    if p.get("evidence_kind")!=derived_kind: raise ValueError("brief evidence kind mismatch")
    if p.get("generated_at")!=envelope["observed_at"]:
        raise ValueError("brief generation clock differs from immutable receipt clock")
    expected["generated_at"]=p["generated_at"]
    if p!=expected:
        raise ValueError("brief payload is not the exact permitted projection, including rights and withheld fields")
    return envelope


def _md_text(value):
    # Quote source-controlled Markdown punctuation and HTML before rendering.
    # JSON quoting alone does not neutralize Markdown links or raw HTML.
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(str(value))).replace("\n"," ").replace("\r"," ")


def _markdown(p):
    lines=["# OMNI local reviewed brief", "", "Synthetic demonstration — no pilot or customer credit." if p["evidence_kind"]=="synthetic" else "Local operator preview.",
           "", "AI/domain labels: pending human review. External distribution is not authorized.", ""]
    for card in p["cards"]:
        lines.extend(["## "+_md_text(card["family"]),"", "Event: "+_md_text(card["event_id"]), ""])
        for claim in card["claims"]:
            lines.append("- "+_md_text(json.dumps({k:claim[k] for k in ("predicate","value","reporting_party")},ensure_ascii=False)))
        lines.extend(["", "Source attribution:", ""])
        for source in card["source_attribution"]:
            lines.append("- "+_md_text(source["url"])+" ("+_md_text(source["reporting_party"])+")")
        if card["correction_refs"]: lines.extend(["", "Correction history: "+", ".join(_md_text(r) for r in card["correction_refs"])])
        lines.extend(["", "Coverage: reviewed event only. Signal eligible: false.", ""])
    lines.extend(["## Coverage limitations", ""]+["- "+x for x in p["coverage_limitations"]])
    lines.extend(["", "Withheld cards: "+str(len(p["withheld"])), ""])
    return "\n".join(lines)


def export_brief(store, receipt_id, destination, registry, policies, *, now, audience="local_operator", allow_synthetic=False):
    envelope=validate_brief(store,receipt_id,registry,policies,now=now,purpose="local_export",audience=audience,allow_synthetic=allow_synthetic)
    target=Path(destination)
    if target.suffix not in {".json",".md",".html"} or target.exists():
        raise ValueError("local export requires a new .json/.md/.html path")
    p=envelope["payload"]
    if target.suffix==".json": content=json.dumps(p,indent=2,ensure_ascii=False,allow_nan=False)+"\n"
    elif target.suffix==".md": content=_markdown(p)
    else: content='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>OMNI local reviewed brief</title><body><pre>'+html.escape(_markdown(p))+'</pre></body></html>\n'
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as handle: handle.write(content)
    return put_receipt(store,"brief_export",{"contract":VERSION,"brief_ref":receipt_id,"format":target.suffix[1:],
        "content_hash":digest(content),"bytes":len(content.encode()),"audience":audience,"purpose":"local_export",
        "external_distribution":"not_authorized"},observed_at=now,refs=[receipt_id])


def permitted_documents(store, docs, policies, *, now, audience="local_operator", allow_synthetic=False):
    documents=[]; withheld=[]
    for doc in docs:
        safe={"evidence_id":doc["evidence_id"],"revision_id":doc["revision_id"]}
        for field,keys in (("source.url",("url",)),("source.title",("title",)),("source.native_id",("native_id",)),
                           ("source.times",("publication","first_observed_at","retrieved_at")),("source.text",("text",))):
            decision=field_decision(store,reference(doc),field,"review_display",audience,policies,now=now,allow_synthetic=allow_synthetic)
            if decision["allowed"]:
                for key in keys: safe[key]=doc[key][:decision["max_characters"]] if key=="text" else doc[key]
            else: withheld.append({"evidence_id":doc["evidence_id"],"field":field,"reason_codes":decision["reason_codes"]})
        documents.append(safe)
    return documents,withheld


def permitted_claims(store,event,docs,policies,*,now,audience="local_operator",allow_synthetic=False):
    """Derived historical review claims; never includes proof text or raw event fields."""
    from .clinical_numerics import semantic
    from .amounts import amount_semantic
    def allowed(field):
        return bool(docs) and all(field_decision(store,reference(doc),field,"review_display",audience,policies,
            now=now,allow_synthetic=allow_synthetic)["allowed"] for doc in docs)
    claims=[]; withheld=[]
    for claim in event["claims"]:
        needed=["claim.value","claim.scope"]
        if claim["predicate"]=="clinical_numeric_result": needed.append("claim.numeric")
        if claim["predicate"]=="scoped_amount": needed.append("claim.amount")
        if not all(allowed(field) for field in needed):
            withheld.append({"claim_id":claim["claim_id"],"reason":"claim_field_rights_pending"}); continue
        safe={k:claim.get(k) for k in ("predicate","value","reporting_party","units","qualifiers","claim_id")}
        safe["support_ref"]={k:claim["support"][k] for k in ("evidence_id","revision_id","start","end")}
        if "numeric_result" in claim: safe["numeric_result"]=semantic(claim["numeric_result"])
        if "amount_atom" in claim: safe["amount_atom"]=amount_semantic(claim["amount_atom"])
        claims.append(safe)
    return claims,withheld


def safe_packet(store, event_id, revision_id, policies, *, now, audience="local_operator", allow_synthetic=False):
    packet=event_packet(store,event_id,revision_id)
    documents,withheld=permitted_documents(store,packet["documents"]+packet.get("scope_documents",[]),policies,
        now=now,audience=audience,allow_synthetic=allow_synthetic)
    # Claim supports contain source text. Only the separately rights-filtered brief
    # exposes claim values; this packet command never dumps unfiltered event JSON.
    return {"contract":"omni-safe-packet-1","event_id":event_id,"event_revision":revision_id,
        "documents":documents,"withheld_fields":withheld,"current_state":{
            k:packet["lifecycle"][k] for k in ("state_hash","hard_reason_codes","quarantined_pending_review")},
        "ai_domain_labels":"pending_human_review"}


def search_history(store, policies, *, query, now, audience="local_operator", allow_synthetic=False, limit=100):
    if not isinstance(query,str) or len(query)>250 or type(limit) is not int or not 1<=limit<=1000:
        raise ValueError("bounded history query required")
    matches=[]; withheld=0
    for event_id,revision_id in store.conn.execute("SELECT event_id,revision_id FROM events ORDER BY rowid DESC"):
        if query.lower() not in (event_id+" "+revision_id).lower(): continue
        packet=event_packet(store,event_id,revision_id); docs=packet["documents"]+packet.get("scope_documents",[])
        if not docs or any(not field_decision(store,reference(d),field,"review_display",audience,
            policies,now=now,allow_synthetic=allow_synthetic)["allowed"] for d in docs
            for field in ("correction.history","review.decision","source.times")):
            withheld+=1; continue
        review_rows=store.conn.execute("SELECT review_id,payload FROM reviews WHERE event_id=? AND revision_id=? ORDER BY rowid",(event_id,revision_id)).fetchall()
        matches.append({"event_id":event_id,"event_revision":revision_id,"source_change_refs":packet["lifecycle"]["change_refs"],
            "current":not ("superseded_event_revision" in packet["lifecycle"]["hard_reason_codes"]),
            "reviews":[{"review_id":r[0],"decision":json.loads(r[1])["decision"],"reviewed_at":json.loads(r[1])["reviewed_at"]} for r in review_rows]})
        if len(matches)>=limit: break
    return {"contract":"omni-rights-filtered-history-1","query":query,"items":matches,"withheld":withheld,
        "limit":limit,"raw_source_bodies":False,"ai_domain_labels":"pending_human_review"}
