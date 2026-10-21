"""OMNI-06 posted registry changes, results and schedules remain distinct."""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from .acquisition import Deferred
from .identities import digest, instant, time_interval
from .store import encode

VERSION = "omni-clinical-1"


def normalize_study(study):
    protocol = study["protocolSection"]
    identity = protocol["identificationModule"]
    trial_id = identity["nctId"]
    if not re.fullmatch(r"NCT\d{8}", trial_id):
        raise ValueError("invalid trial ID")
    status = protocol.get("statusModule", {})
    dates = {name: time_interval(status.get(name, {}).get("date")) for name in
             ("primaryCompletionDateStruct", "completionDateStruct", "lastUpdatePostDateStruct", "resultsFirstPostDateStruct")}
    result_section = study.get("resultsSection")
    return {"contract": VERSION, "trial_id": trial_id, "title": identity.get("briefTitle"),
            "acronym": identity.get("acronym"), "phase": protocol.get("designModule", {}).get("phases"),
            "status": status.get("overallStatus"), "dates": dates,
            "has_results": study.get("hasResults") if type(study.get("hasResults")) is bool else None,
            "results_hash": digest(result_section) if result_section is not None else None,
            "protocol_hash": digest(protocol),
            "source": "https://clinicaltrials.gov/study/" + trial_id,
            "regulatory_approval": "not_established", "signal_eligible": False}


def apply_studies(store, studies, *, query, observed_at, data_timestamp=None, complete=True, manage_transaction=True):
    """New query/trial is a seed. Absent records never imply termination."""
    instant(observed_at)
    query_id = digest(query)
    normalized = [normalize_study(s) for s in studies]
    if len({s["trial_id"] for s in normalized}) != len(normalized):
        raise ValueError("duplicate trial in one page batch")
    changes, seeded, unchanged = [], 0, 0
    try:
        if manage_transaction: store.conn.execute("BEGIN IMMEDIATE")
        elif not store.conn.in_transaction: raise ValueError("outer transaction required")
        for current in normalized:
            revision = digest(current)
            head = store.conn.execute("SELECT revision_id FROM trial_heads WHERE query_id=? AND trial_id=?", (query_id,current["trial_id"])).fetchone()
            first = observed_at
            if head:
                previous_row = store.conn.execute("SELECT payload,first_observed_at FROM trial_snapshots WHERE query_id=? AND trial_id=? AND revision_id=?", (query_id,current["trial_id"],head[0])).fetchone()
                previous=json.loads(previous_row[0]); first=previous_row[1]
                if head[0] == revision:
                    unchanged += 1; continue
                posted = current["dates"]["lastUpdatePostDateStruct"]
                result_posted = current["dates"]["resultsFirstPostDateStruct"]
                fields=[]
                if current["protocol_hash"] != previous["protocol_hash"]:
                    fields.append("posted_registry_change")
                if current["results_hash"] != previous["results_hash"] or current["has_results"] != previous["has_results"]:
                    fields.append("results_change")
                for kind in fields:
                    time = result_posted if kind == "results_change" and previous["results_hash"] is None else posted
                    reasons=[]
                    if time["precision"] not in ("date", "timestamp"):
                        reasons.append("posting_time_unknown")
                    elif time["start"][:10] > observed_at[:10]:
                        reasons.append("future_posting_time")
                    if not complete:
                        reasons.append("coverage_gap")
                    changes.append({"change_id":"trial-change:"+digest([query_id,current["trial_id"],head[0],revision,kind]),
                        "kind":kind,"trial_id":current["trial_id"],"before_revision":head[0],"after_revision":revision,
                        "source_posted_time":time,"observed_at":observed_at,"data_timestamp":data_timestamp,
                        "review_status":"review_required","reason_codes":reasons,
                        "clinical_outcome":"not_established","regulatory_approval":"not_established","signal_eligible":False})
            else:
                seeded += 1
            store.conn.execute("INSERT OR IGNORE INTO trial_snapshots VALUES(?,?,?,?,?)", (query_id,current["trial_id"],revision,first,encode(current)))
            store.conn.execute("INSERT OR REPLACE INTO trial_heads VALUES(?,?,?)", (query_id,current["trial_id"],revision))
        for change in changes:
            store.conn.execute("INSERT OR IGNORE INTO trial_changes VALUES(?,?,?,?)", (change["change_id"], query_id, observed_at, encode(change)))
        if manage_transaction: store.conn.commit()
    except BaseException:
        if manage_transaction: store.conn.rollback()
        raise
    return {"contract":VERSION,"query_id":query_id,"seeded":seeded,"unchanged":unchanged,
            "changes":changes,"complete":complete,"missing_records_mean":"unknown_not_terminated"}


def collect_studies(client, *, query, previous_data_timestamp=None, continuation=None, continuation_version=None, max_pages=3):
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError("invalid page budget")
    version=json.loads(client.get("https://clinicaltrials.gov/api/v2/version", content_types=("application/json",)))
    data_timestamp=version.get("dataTimestamp")
    if not data_timestamp:
        raise ValueError("registry version missing data timestamp")
    if previous_data_timestamp == data_timestamp and not continuation:
        return {"studies":[],"unchanged_source":True,"data_timestamp":data_timestamp,"complete":True,"continuation":None,"gaps":[]}
    notices=[]
    if continuation and continuation_version!=data_timestamp:
        continuation=None; notices.append("registry_snapshot_changed_restart")
    studies, gaps, token, seen = [], [], continuation, {continuation} if continuation else set()
    for _ in range(max_pages):
        params={"format":"json","pageSize":100,"query.term":query}
        if token: params["pageToken"]=token
        try:
            page=json.loads(client.get("https://clinicaltrials.gov/api/v2/studies?"+urlencode(params), content_types=("application/json",)))
            if not isinstance(page.get("studies"),list): raise ValueError("study page missing records")
            studies.extend(page["studies"])
            token=page.get("nextPageToken")
            if not token: break
            if token in seen: raise ValueError("repeated page token")
            seen.add(token)
        except (Deferred, ValueError) as exc:
            gaps.append(str(exc)); break
    # A page token is usable only for the dataset version that created it.
    # Discard a mixed batch; the next command starts a fresh bounded scan.
    try:
        end_version=json.loads(client.get("https://clinicaltrials.gov/api/v2/version",content_types=("application/json",))).get("dataTimestamp")
        if end_version!=data_timestamp:
            studies=[]; token=None; gaps.append("registry_changed_during_scan")
    except (Deferred,ValueError) as exc:
        studies=[]; token=continuation; gaps.append("registry_version_unverified:"+str(exc))
    unique={}
    for study in studies:
        trial_id=study.get("protocolSection",{}).get("identificationModule",{}).get("nctId")
        if not trial_id or (trial_id in unique and digest(unique[trial_id])!=digest(study)):
            studies=[]; token=None; gaps.append("registry_page_identity_conflict"); break
        unique[trial_id]=study
    else: studies=list(unique.values())
    if token: gaps.append("registry_page_budget_deferred")
    return {"studies":studies,"unchanged_source":False,"data_timestamp":data_timestamp,
            "complete":not gaps and not token,"continuation":token,"gaps":sorted(set(gaps)),"notices":notices}


def watch_studies(store, client, *, query, now, max_pages=3, fail_at=None):
    """Commit trial snapshots, changes, cursor and version in one transaction."""
    key="clinical:"+digest(query); cursor=store.cursor(key)
    versions={r[0]:r[1] for r in store.conn.execute("SELECT key,value FROM metadata WHERE key IN (?,?)",(key,key+":paging"))}
    page=collect_studies(client,query=query,previous_data_timestamp=versions.get(key),
                         continuation=cursor["continuation"],continuation_version=versions.get(key+":paging"),max_pages=max_pages)
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        result=apply_studies(store,page["studies"],query=query,observed_at=now,data_timestamp=page["data_timestamp"],
                             complete=page["complete"],manage_transaction=False)
        if fail_at=="after_trials": raise RuntimeError("injected crash after trial snapshots")
        store.commit_cycle(cycle_id="clinical:"+digest([query,now,page]),source_id=key,observed_at=now,
            documents=[],events=[],decisions=[],complete=page["complete"],continuation=page["continuation"],
            gaps=page["gaps"],metrics={"page_hash":digest(page),"requests":dict(client.counts)},manage_transaction=False)
        store.conn.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)",(key+":paging",page["data_timestamp"]))
        if page["complete"]:
            store.conn.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)",(key,page["data_timestamp"]))
        store.conn.commit()
    except BaseException:
        store.conn.rollback(); raise
    result["coverage"]={k:v for k,v in page.items() if k!="studies"}
    return result
