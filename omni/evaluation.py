"""OMNI-09 release evidence. Synthetic repetitions never become prospective proof."""
from __future__ import annotations

import math

from .identities import digest, instant

VERSION = "omni-evaluation-1"


def wilson(successes, total):
    if type(total) is not int or type(successes) is not int or not 0 <= successes <= total:
        raise ValueError("invalid binomial denominator")
    if not total: return {"n":0,"successes":0,"estimate":None,"lower":None,"upper":None}
    p=successes/total; z=1.959963984540054; scale=1+z*z/total
    center=(p+z*z/(2*total))/scale
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/scale
    return {"n":total,"successes":successes,"estimate":p,"lower":max(0,center-half),"upper":min(1,center+half)}


def evaluate(manifest, cases=(), runs=(), gold=()):
    """Return gates, actual denominators and exclusions; never activates a route."""
    required=("implementation_hash","corpus_hash","source_scope_hash","frozen_at")
    manifest_errors=["missing_"+key for key in required if not manifest.get(key)]
    frozen=None
    if manifest.get("frozen_at"):
        try: frozen=instant(manifest["frozen_at"])
        except (ValueError,TypeError): manifest_errors.append("invalid_frozen_at")
    included=[]; exclusions=[]; seen=set()
    for case in cases:
        reason=None
        key=case.get("event_case_id")
        if not key or key in seen: reason="missing_or_duplicate_event_case"
        elif case.get("evidence_kind")!="prospective": reason="not_prospective"
        elif not case.get("adjudicator") or not case.get("adjudication_receipt") or type(case.get("correct")) is not bool: reason="not_adjudicated"
        elif any(case.get(key)!=manifest.get(key) for key in required[:3]): reason="version_mismatch"
        elif case.get("meaningfully_exercised") is not True: reason="not_meaningfully_exercised"
        else:
            try:
                if frozen is None or instant(case["observed_at"]) < frozen: reason="before_freeze"
            except (KeyError,ValueError,TypeError): reason="observation_time_unknown"
        if reason: exclusions.append({"event_case_id":key,"reason":reason})
        else: included.append(case); seen.add(key)
    run_ids=set(); sessions=set()
    for run in runs:
        if (run.get("execution_mode")=="scheduled" and run.get("eligible") is True
                and run.get("receipt") and run.get("session") and run.get("run_id")
                and run.get("implementation_hash")==manifest.get("implementation_hash")):
            try: after_freeze=frozen is not None and instant(run["started_at"])>=frozen
            except (KeyError,ValueError,TypeError): after_freeze=False
            if after_freeze and run["run_id"] not in run_ids:
                sessions.add(run["session"]); run_ids.add(run["run_id"])
    critical=sum(c.get("critical_error") is True for c in cases)
    unreviewed=sum(c.get("published") is True and c.get("outside_cohort") is True and not c.get("review_receipt") for c in cases)
    new_issuer=[c for c in included if c.get("outside_cohort") is True]
    publications=[c for c in included if c.get("proposed_publication") is True]
    precision=wilson(sum(c["correct"] for c in publications),len(publications))
    manual_reasons=list(manifest_errors)
    if len(sessions)<10: manual_reasons.append("eligible_scheduled_sessions_below_10")
    if len(new_issuer)<100: manual_reasons.append("meaningful_new_issuer_cases_below_100")
    if critical: manual_reasons.append("critical_leakage_observed")
    if unreviewed: manual_reasons.append("outside_cohort_publication_without_review")
    if not manifest.get("source_coverage_receipt"): manual_reasons.append("published_source_coverage_missing")
    family_results=[]
    for family, source in sorted({(c.get("family","unknown"),c.get("source_class","unknown")) for c in publications}):
        subset=[c for c in publications if c.get("family","unknown")==family and c.get("source_class","unknown")==source]
        stats=wilson(sum(c["correct"] for c in subset),len(subset))
        ready=(len(publications)>=300 and stats["n"]>=100 and stats["estimate"]>=.98 and stats["lower"]>=.95
               and precision["estimate"]>=.98 and precision["lower"]>=.95 and not manual_reasons
               and manifest.get("untouched_test_receipt") and family!="unknown" and source!="unknown")
        family_results.append({"family":family,"source_class":source,"precision":stats,
                               "status":"eligible_for_activation_review" if ready else "remain_manual"})
    valid_gold=[]; seen_gold=set()
    for row in gold:
        key=row.get("event_id")
        if (key and key not in seen_gold and row.get("prespecified") is True
                and row.get("in_source_scope") is True and type(row.get("detected")) is bool
                and row.get("adjudication_receipt")
                and all(row.get(k)==manifest.get(k) for k in ("corpus_hash","source_scope_hash"))):
            valid_gold.append(row); seen_gold.add(key)
    recall=wilson(sum(r["detected"] for r in valid_gold),len(valid_gold))
    unknown=[r for r in valid_gold if r.get("unknown_issuer") is True]
    unknown_recall=wilson(sum(r["detected"] for r in unknown),len(unknown))
    recall_ready=(not manifest_errors and recall["n"]>=100 and recall["estimate"]>=.90 and unknown_recall["n"]>0 and unknown_recall["estimate"]>=.90)
    latencies=[c["latency_seconds"] for c in included if c.get("continuous_source") is True and type(c.get("latency_seconds")) in (int,float) and math.isfinite(c["latency_seconds"]) and c["latency_seconds"]>=0]
    p95=sorted(latencies)[max(0,math.ceil(.95*len(latencies))-1)] if latencies else None
    interval=manifest.get("poll_interval_seconds",900)
    timing_ready=(type(interval) in (int,float) and math.isfinite(interval) and interval>0 and p95 is not None and p95<=2*interval)
    for row in family_results:
        if not recall_ready or not timing_ready:
            row["status"]="remain_manual"
    return {"contract":VERSION,"manifest_fingerprint":digest(manifest),"automatic_activation":False,
            "manual_pilot":{"status":"eligible_for_review" if not manual_reasons else "pending","reason_codes":manual_reasons,
                            "eligible_scheduled_sessions":len(sessions),"meaningful_new_issuer_cases":len(new_issuer),"critical_errors":critical},
            "precision":precision,"family_source_gates":family_results,
            "recall":{"status":"eligible_for_review" if recall_ready else "pending","overall":recall,"unknown_issuers":unknown_recall,
                      "misses":[r["event_id"] for r in valid_gold if not r["detected"]]},
            "timing":{"n":len(latencies),"p95_seconds":p95,"status":"eligible_for_review" if timing_ready else "pending"},
            "excluded_cases":exclusions,"independence_caveat":"Wilson intervals do not adjust correlated cases; event-unit sampling requires review."}


def dependent_gates(evidence, current_coverage, *, candidates=()):
    """OMNI-10/11/12 are evidence gates, not a claim that tooling creates demand."""
    # Candidate membership is operator input, never a bundled expansion list.
    if not isinstance(candidates, (list, tuple)) or any(not isinstance(t, str) or not t.strip() for t in candidates):
        raise ValueError("expansion candidates must be explicit nonempty strings")
    required={
        "OMNI-10":("stable_architecture_receipt","reviewed_user_value_receipt","vertical_source_policies"),
        "OMNI-11":("wave_a_review_receipt","source_cost_measurements","incremental_yield_measurements","candidate_identity_receipts"),
        "OMNI-12":("reviewed_useful_brief_receipts","field_level_rights_receipt","counsel_review_receipt","customer_feedback_receipt","operating_cost_receipt"),
    }
    result={k:{"status":"ready_for_review" if all(evidence.get(x) for x in needs) else "pending",
               "missing":[x for x in needs if not evidence.get(x)]} for k,needs in required.items()}
    result["wave_b_candidates"]=list(dict.fromkeys(t for t in candidates if t not in current_coverage))
    result["automatic_membership_change"]=False
    return result
