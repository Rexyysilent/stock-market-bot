"""OMNI-07 explicit local review and evidence-backed companion focus.

The companion contract avoids rewriting the historical daily-brief focus schema.
Only a current validation bound to exact inputs can select an event card.
"""
from __future__ import annotations

import json

from .identities import digest, instant
from .store import encode
from .validator import validate
from .lifecycle import lifecycle_state, validate_current
from .receipts import transaction
from .clinical_scope import dependency_references

VERSION = "omni-review-2"


def event_packet(store, event_id, revision_id):
    row=store.conn.execute("SELECT payload,decision,first_observed_at FROM events WHERE event_id=? AND revision_id=?", (event_id,revision_id)).fetchone()
    if not row: raise ValueError("unknown event revision")
    packet_row=store.conn.execute("SELECT p.payload FROM event_packets p JOIN event_packet_heads h "
        "ON p.event_id=h.event_id AND p.revision_id=h.revision_id AND p.packet_hash=h.packet_hash "
        "WHERE p.event_id=? AND p.revision_id=?",(event_id,revision_id)).fetchone()
    event=json.loads(packet_row[0] if packet_row else row[0]); documents=[]; scope_documents=[]
    refs={(c["support"]["evidence_id"],c["support"]["revision_id"]) for c in event["claims"]}
    for eid, revision in sorted(refs):
        evidence=store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?", (eid,revision)).fetchone()
        if not evidence: raise ValueError("event evidence missing")
        documents.append(json.loads(evidence[0]))
    scope_refs=dependency_references(event)
    for eid, revision in sorted(scope_refs-refs):
        evidence=store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?",(eid,revision)).fetchone()
        if not evidence: raise ValueError("clinical definition evidence missing")
        scope_documents.append(json.loads(evidence[0]))
    alternate=[]
    for eid, revision in store.conn.execute("SELECT evidence_id,evidence_revision FROM event_evidence WHERE event_id=? AND revision_id=?",(event_id,revision_id)):
        if (eid,revision) in refs|scope_refs: continue
        evidence=store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?",(eid,revision)).fetchone()
        if evidence:
            d=json.loads(evidence[0])
            alternate.append({k:d[k] for k in ("evidence_id","revision_id","url","origin_cluster_id","source_policy_id")})
    return {"event":event,"documents":documents,"scope_documents":scope_documents,"alternate_evidence_paths":alternate,
            "decision":json.loads(row[1]),"first_observed_at":row[2],
            "lifecycle":lifecycle_state(store,event,documents+scope_documents)}


def record_review(store, event_id, revision_id, registry, policies, *, now, reviewer, action, note=""):
    """Called only for an explicit operator decision; tests use synthetic reviewers."""
    if action not in ("approve","reject") or not reviewer.strip() or len(reviewer)>120:
        raise ValueError("explicit reviewer and approve/reject decision required")
    with transaction(store):
        packet=event_packet(store,event_id,revision_id)
        current=validate_current(store,packet,registry,policies,now=now)
        previous=store.conn.execute("SELECT review_id FROM reviews WHERE event_id=? ORDER BY rowid DESC LIMIT 1",(event_id,)).fetchone()
        review={"decision":action,"reviewer":reviewer,"reviewed_at":now,"input_hash":current["input_hash"],
                "current_state_hash":current["current_state_hash"],"resolved_change_refs":current["lifecycle"]["change_refs"],
                "supersedes_review":previous[0] if previous else None,"note":note[:2000],
                "authentication":"operator_assertion_not_authenticated_by_software"}
        checked=validate_current(store,packet,registry,policies,now=now,review=review)
        if action=="approve" and checked["editorial_status"]!="eligible":
            raise ValueError("mandatory gates still fail: "+", ".join(checked["reason_codes"]))
        review_id="review:"+digest([event_id,revision_id,review])
        store.conn.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?)", (review_id,event_id,revision_id,encode(review)))
        store.conn.execute("UPDATE outbox SET state='acknowledged',acknowledged_at=? WHERE event_id=? AND revision_id=?",(now,event_id,revision_id))
    return {"review_id":review_id,"review":review,"validation":checked}


def materiality(event):
    claims=event["claims"]
    if any(c["predicate"]=="primary_endpoint_status" or (c["predicate"]=="regulatory_stage" and c["value"] in ("approved","clinical_hold")) for c in claims):
        return "critical"
    if any(c["predicate"] in ("financing_stage","contract_stage") for c in claims):
        return "high"
    return "routine"


def card(packet, decision, registry):
    event=packet["event"]
    subjects=sorted({s for c in event["claims"] for s in c["subject_ids"]})
    scope=event.get("scope_id")
    # Asset/issuer relationships are contextual roles, not inferred subjects.
    related_roles=[r for r in registry.data["relationships"] if r["to_id"]==scope]
    return {"event_id":event["event_id"],"event_revision":event["event_revision"],
            "family":event["family"],"scope_id":scope,"subject_ids":subjects,"context_roles":related_roles,
            "evidence_kinds":sorted({d["evidence_kind"] for d in packet["documents"]}),
            "materiality":materiality(event),"evidence_grade":decision["evidence_grade"],
            "claims":[{"predicate":c["predicate"],"value":c["value"],"reporting_party":c["reporting_party"],
                       "evidence_id":c["support"]["evidence_id"],"revision_id":c["support"]["revision_id"],
                       "start":c["support"]["start"],"end":c["support"]["end"],
                       "units":c.get("units"),"qualifiers":c.get("qualifiers",[])} for c in event["claims"]],
            "evidence":[{"evidence_id":d["evidence_id"],"revision_id":d["revision_id"],"url":d["url"],"published":d["publication"]} for d in packet["documents"]],
            "alternate_evidence_paths":packet["alternate_evidence_paths"],
            "corrections":decision["lifecycle"]["change_refs"],"current_state_hash":decision["current_state_hash"],
            "assertion_origin_units":decision["assertion_origin_units"],"independence_assessed":False,
            "assertion_origins":sorted({r["origin_id"] for r in decision["origin_resolution"]}),
            "clinical_context":event.get("clinical_context"),"scoped_amounts":event.get("scoped_amounts",[]),
            "first_observed_at":packet["first_observed_at"],
            "reviewer":decision["reviewer"],"reviewed_at":decision["reviewed_at"],
            "signal_eligible":False,"coverage_tier":"reviewed_event_only","market_reaction":None}


def build_brief(store, registry, policies, *, now, mode="shadow", legacy_focus=None, limit=5):
    from editorial_focus import select_focus_with_events
    if mode not in ("off","shadow","reviewed") or not 1<=limit<=5:
        raise ValueError("invalid event briefing mode/limit")
    instant(now)
    candidates=[]; withheld=[]; checked=[]; queue_deferred=0
    if mode!="off":
        # Review the latest observed revision of each event; a correction must
        # invalidate an older approval instead of silently republishing it.
        rows=store.conn.execute("SELECT event_id,revision_id,changed_at FROM event_heads ORDER BY changed_at DESC,event_id LIMIT 500").fetchall()
        queue_deferred=max(0,store.conn.execute("SELECT count(*) FROM event_heads").fetchone()[0]-len(rows))
        for eid, rev, changed_at in rows:
            if instant(changed_at)>instant(now): continue
            packet=event_packet(store,eid,rev)
            review=next((value for r in store.conn.execute("SELECT payload FROM reviews WHERE event_id=? AND revision_id=? ORDER BY rowid DESC",(eid,rev))
                if instant((value:=json.loads(r[0]))["reviewed_at"])<=instant(now)),None)
            if review and instant(review["reviewed_at"]) < instant(changed_at):
                review=None
            decision=validate_current(store,packet,registry,policies,now=now,review=review)
            checked.append((packet,decision))
        # Current contradictory outcomes for a known trial require resolution
        # even if each source independently has a human approval. Mirrors do
        # not vote an outcome into existence.
        outcomes={}
        for packet,decision in checked:
            event=packet["event"]
            if event["family"]!="clinical_result" or set(decision["reason_codes"])-{"review_required"}: continue
            for claim in event["claims"]:
                if claim["predicate"]=="primary_endpoint_status" and claim.get("scope_id"):
                    outcomes.setdefault(claim["scope_id"],set()).add(claim["value"])
        for packet,decision in checked:
            event=packet["event"]; eid,rev=event["event_id"],event["event_revision"]
            if len(outcomes.get(event.get("scope_id"),()))>1:
                decision=dict(decision,editorial_status="review_required",evidence_grade="contested",
                              reason_codes=sorted(set(decision["reason_codes"]+["cross_source_outcome_conflict"])))
            if decision["editorial_status"]=="eligible" and mode=="reviewed":
                candidates.append(card(packet,decision,registry))
            else:
                withheld.append({"event_id":eid,"revision_id":rev,"reason_codes":decision["reason_codes"] or ["shadow_only"]})
    deduplicated={}
    for item in candidates:
        semantic=sorted(digest({k:c.get(k) for k in ("predicate","value","units","qualifiers")}) for c in item["claims"])
        key=digest([item["assertion_origins"],item["family"],item["scope_id"],semantic])
        if key in deduplicated:
            prior=deduplicated[key]
            prior.setdefault("same_assertion_event_ids",[prior["event_id"]]).append(item["event_id"])
            prior["alternate_evidence_paths"].extend(item["evidence"]+item["alternate_evidence_paths"])
        else:
            deduplicated[key]=item
    candidates=list(deduplicated.values())
    ranks={"critical":0,"high":1,"routine":2,"unknown":3}
    candidates.sort(key=lambda c:(ranks[c["materiality"]],c["event_id"],c["event_revision"]))
    chosen=candidates[:limit]
    return {"contract":VERSION,"generated_at":now,"mode":mode,"cards":chosen,
            "focus":select_focus_with_events(legacy_focus,chosen,mode=mode),
            "withheld":withheld,"eligible_before_display_cap":len(candidates),
            "budget_deferred":max(0,len(candidates)-limit),"signal_eligible":False,
            "evaluation_budget_deferred":queue_deferred,
            "external_distribution":"not_authorized","market_reaction_required":False}
