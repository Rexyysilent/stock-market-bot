"""Explicit local event watcher; independent from export and production ledgers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from .acquisition import collect_edgar, Deferred, extract_document_text, filing_documents, parse_atom
from .classifier import extract
from .clinical_numerics import result_projection
from .amounts import amount_projection
from .evidence import document, source_decision
from .identities import digest
from .validator import validate


def analyze(doc, registry, policies, *, now):
    policy = policies.get(doc["source_policy_id"])
    output = extract(doc, registry, policy, known_by=now)
    decisions = [validate(event, [doc], registry, policies, now=now) for event in output["events"]]
    leads=list(output["leads"])
    for event,decision in zip(output["events"],decisions):
        if set(decision["reason_codes"]) & {"entity_unresolved","trial_unresolved","ambiguous_alias"}:
            leads.append({"reason":"entity_resolution_pending","event_id":event["event_id"],"event_revision":event["event_revision"]})
    leads=[dict(lead,lead_id="lead:"+digest([doc["evidence_id"],doc["revision_id"],lead]),
                evidence_id=doc["evidence_id"],evidence_revision=doc["revision_id"],
                evidence_kind=doc["evidence_kind"],signal_eligible=False) for lead in leads]
    return output["events"], decisions, leads


def edgar_query_plan():
    return {"route_id":"sec-current-filings","owner":"exclude","form_filter":"","page_size":100,
            "sampling":"contiguous_head_to_prior_observed_watermark"}


def watch_edgar(store, client, registry, *, now, hydrate=False, max_pages=3, reconcile=False, refresh_documents=False, policies=None,
                target_family=None, measurement_proposal=None, measurement_arm=None, current_coverage=None,
                instruments=None, allow_synthetic_measurement=False):
    """Persist every acquired candidate, including records deferred for hydration."""
    source_id=client.policy["policy_id"]
    from .collection_context import prepare_scope
    query_plan=edgar_query_plan()
    family,measurement_metrics=prepare_scope(store,registry,policies or {source_id:client.policy},
        source_id=source_id,route_id="sec-current-filings",query=query_plan,now=now,default_family="sec_filings",
        target_family=target_family,measurement_proposal=measurement_proposal,measurement_arm=measurement_arm,
        current_coverage=current_coverage,instruments=instruments,allow_synthetic_measurement=allow_synthetic_measurement)
    cursor=store.cursor(source_id)
    if reconcile and not cursor["continuation"]:
        raise ValueError("no queued filing reconciliation")
    acquisition=collect_edgar(client,watermark=cursor["watermark"],max_pages=max_pages,
                              continuation=cursor["continuation"] if reconcile else None)
    from .coverage import observation_from_rows, feed_observation_window
    window_start,window_end=feed_observation_window(store,source_id=source_id,route_id="sec-current-filings",family=family,
        query_hash=digest(query_plan),policy_hash=digest(client.policy),proof=acquisition.get("coverage_proof"),now=now)
    observations = [observation_from_rows(p["rows"], source_id=source_id, route_id="sec-current-filings",
        family=family, acquired_at=now, window_start=window_start, window_end=window_end,
        policy_hash=digest(client.policy), raw_response_hash=p["raw_sha256"],
        query=query_plan, request_context={"offset":p["offset"],"previous_watermark":cursor["watermark"],
            "page_result_cap_reached":p["result_cap_reached"]}, gaps=p["gaps"],
        capped=p["result_cap_reached"] and not acquisition["complete"], end_of_results=acquisition["complete"])
        for p in acquisition.get("page_observations", [])]
    if hydrate:
        combined={row["native_id"]:row for row in store.backlog(source_id)}
        combined.update({row["native_id"]:row for row in acquisition["rows"]})
        acquisition["rows"]=list(combined.values())
    documents,events,decisions,leads,jobs=[],[],[],[],[]
    document_gaps=[]
    for row in acquisition["rows"]:
        previous=store.candidate(source_id,row["native_id"]) or {}
        # Feed entries lack index registrants and filing-date metadata. Preserve
        # those observations (including their separate snapshot/hash) on refresh.
        for key in ("form","cik","submission_url","filed_date","registrants","index_day",
                    "index_metadata","index_metadata_hash","index_observed_at","index_raw_sha256"):
            if key in previous and key not in row: row[key]=previous[key]
        row["hydration_status"]=previous.get("hydration_status","metadata_only")
        if previous.get("document_listing"):
            row["document_listing"]=previous["document_listing"]
        if not re.match(r"^(?:8-K|6-K)(?:/A)?\s+-",row["title"],re.I):
            row["hydration_status"]="not_applicable"
            continue
        if not hydrate:
            continue
        if source_decision(client.policy,row["url"],"store_text") or source_decision(client.policy,row["url"],"local_analysis"):
            row["hydration_status"]="purpose_not_approved"; continue
        try:
            listing=previous.get("document_listing") if not refresh_documents else None
            if not listing or not listing["documents"] or listing["gaps"]:
                listing=filing_documents(client.get(row["url"],kind="document"),filing_url=row["url"],limit=100)
            row["document_listing"]=listing
            row_gaps=list(listing["gaps"]); progress=store.jobs(source_id,row["native_id"])
            active=[]; filing_docs={}
            for item in listing["documents"]:
                job=progress.get(item["url"],dict(item,native_id=row["native_id"],state="pending",attempts=0))
                if refresh_documents: job=dict(job,state="pending",attempts=0)
                if job.get("evidence_id"):
                    cached=store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?",
                        (job["evidence_id"],job["evidence_revision"])).fetchone()
                    if cached: filing_docs[job["url"]]=json.loads(cached[0])
                    else: job=dict(job,state="pending")
                elif job["state"]=="parsed":
                    job=dict(job,state="pending")
                active.append(job)
            pending=sorted((j for j in active if j["state"]!="parsed"),key=lambda j:(j["attempts"],j["url"]))
            for job in pending[:3]:
                attempted_before=client.counts["attempt"]
                try:
                    parsed=extract_document_text(client.get(job["url"],kind="document"))
                    doc=document(policy=client.policy,url=job["url"],native_id=row["native_id"]+"/"+job["url"].rsplit("/",1)[-1],
                        title=row["title"],text=parsed["text"],published=row["published"],observed_at=now,
                        source_class="filing",reporting_party="filing_issuer",body_complete=parsed["body_complete"],
                        completeness_reasons=parsed["gaps"],evidence_kind="prospective",
                        origin_reference=["filing-accession",row["native_id"]])
                    filing_docs[job["url"]]=doc
                    job.update(evidence_id=doc["evidence_id"],evidence_revision=doc["revision_id"])
                    job["state"]="parsed" if parsed["body_complete"] else "content_incomplete"
                except (Deferred,ValueError,PermissionError) as exc:
                    job["state"]=str(exc)
                job["attempts"]+=client.counts["attempt"]-attempted_before
                job["last_attempted_at"]=now
            jobs.extend(active)
            unfinished=[j for j in active if j["state"]!="parsed"]
            row_gaps.extend(j["state"] for j in unfinished if j["state"]!="pending")
            if unfinished: row_gaps.append("document_budget_deferred")
            document_gaps.extend(row_gaps)
            row["hydration_status"]=row_gaps[0] if row_gaps else "parsed"
            docs=list(filing_docs.values())
            found,checked,new_leads=analyze_documents(docs,registry,policies or {source_id:client.policy},now=now,gaps=row_gaps)
            documents.extend(docs); events.extend(found); decisions.extend(checked); leads.extend(new_leads)
        except (Deferred, ValueError, PermissionError) as exc:
            row["hydration_status"]=str(exc); document_gaps.append(str(exc))
    cycle_id="cycle:"+digest([source_id,now,acquisition])
    receipt=store.commit_cycle(cycle_id=cycle_id,source_id=source_id,observed_at=now,
        documents=documents,events=events,decisions=decisions,candidates=acquisition["rows"],document_jobs=jobs,leads=leads,registry=registry,
        watermark=acquisition["watermark"],complete=acquisition["complete"],initial_seed=acquisition["initial_seed"],
        continuation=acquisition["continuation"],gaps=acquisition["gaps"],
        coverage_proof=acquisition.get("coverage_proof"),coverage_observations=observations,
        metrics={"metadata_candidates":len(acquisition["rows"]),"documents":len(documents),
                 "document_gaps":sorted(set(document_gaps)),"request_counts":dict(client.counts),"leads":len(leads),
                 **measurement_metrics})
    return {**receipt,"cycle_id":cycle_id,"coverage":acquisition,"document_gaps":sorted(set(document_gaps)),
            "local_review_items":len(store.pending()),"hydration_backlog":store.backlog_counts(source_id),"external_delivery":False}


def analyze_documents(documents, registry, policies, *, now, gaps=()):
    """Combine current exhibits of one origin before validating contradictions.

    Cached successful exhibits participate in every resumed cycle. Alternate
    support paths remain attached; duplicate semantic claims do not inflate the
    revision or independent-origin count.
    """
    grouped={}; leads=[]
    for doc in documents:
        found,_,new_leads=analyze(doc,registry,policies,now=now)
        leads.extend(new_leads)
        for event in found:
            prior=grouped.get(event["event_id"])
            if prior is None:
                grouped[event["event_id"]]=event
                continue
            known={c["claim_id"] for c in prior["claims"]}
            prior["claims"].extend(c for c in event["claims"] if c["claim_id"] not in known)
            prior["evidence_ids"]=sorted(set(prior["evidence_ids"]+event["evidence_ids"]))
            prior["first_observed_at"]=min(prior["first_observed_at"],event["first_observed_at"])
    decisions=[]
    for event in grouped.values():
        event["scoped_amounts"] = amount_projection(event["claims"])
        if event["family"] == "clinical_result":
            event["clinical_context"]["numerical_results"] = result_projection(event["claims"])
        event["acquisition_gaps"]=sorted(set(gaps))
        meaning=sorted({c["semantic_id"] for c in event["claims"]})
        event["event_revision"]=digest([meaning,event["acquisition_gaps"]] if gaps else meaning)
        relevant=[d for d in documents if d["evidence_id"] in event["evidence_ids"]]
        decisions.append(validate(event,relevant,registry,policies,now=now))
    return list(grouped.values()),decisions,leads
