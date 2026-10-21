"""OMNI-09 release evidence. Synthetic repetitions never become prospective proof."""
from __future__ import annotations

import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from .identities import digest, instant

VERSION = "omni-evaluation-3"


@dataclass(frozen=True)
class _VerifiedEvidence:
    """Internal context built by the receipt-backed protocol reader.

    JSON submitted to the standalone evaluator cannot construct this context.
    It is not a signature or external-human authentication mechanism.
    """
    protocol_id: str
    reasons: tuple
    coverage_ready: bool
    timing: dict
    untouched_ready: bool
    real_prospective: bool
    required_strata: tuple
    minimum_unknown: int
    minimum_negative: int


def wilson(successes, total):
    if type(total) is not int or type(successes) is not int or not 0 <= successes <= total:
        raise ValueError("invalid binomial denominator")
    if not total: return {"n":0,"successes":0,"estimate":None,"lower":None,"upper":None}
    p=successes/total; z=1.959963984540054; scale=1+z*z/total
    center=(p+z*z/(2*total))/scale
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/scale
    return {"n":total,"successes":successes,"estimate":p,"lower":max(0,center-half),"upper":min(1,center+half)}


def evaluate(manifest, cases=(), runs=(), gold=(), *, _verified=None):
    """Compute descriptive metrics; release gates need checked store receipts."""
    verified = _verified if isinstance(_verified, _VerifiedEvidence) else None
    operational = bool(verified and verified.real_prospective)
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
        else:
            try:
                if frozen is None or instant(case["observed_at"]) < frozen: reason="before_freeze"
            except (KeyError,ValueError,TypeError): reason="observation_time_unknown"
        if reason: exclusions.append({"event_case_id":key,"reason":reason})
        else: included.append(case); seen.add(key)
    run_ids=set(); sessions=set(); run_cycles=set(); excluded_runs=[]
    try: session_zone=ZoneInfo(manifest.get('session_timezone','America/New_York'))
    except (ZoneInfoNotFoundError,TypeError,ValueError):
        session_zone=None; manifest_errors.append('invalid_session_timezone')
    for run in runs:
        reason='run_evidence_incomplete'
        if (run.get("execution_mode")=="scheduled" and run.get("eligible") is True
                and run.get("receipt") and run.get('scheduler_receipt') and run.get("session") and run.get("run_id")
                and all(run.get(key)==manifest.get(key) for key in required[:3])):
            ids=run.get('source_cycle_ids')
            try:
                started,completed=instant(run['started_at']),instant(run['completed_at'])
                actual_session=started.astimezone(session_zone).date().isoformat() if session_zone else None
                valid=(frozen is not None and frozen<=started<=completed and actual_session==run['session'])
            except (KeyError,ValueError,TypeError): valid=False
            reason='run_session_or_time_invalid'
            if valid:
                reason='run_cycles_missing_or_reused'
                if (isinstance(ids,list) and ids and all(isinstance(i,str) and i for i in ids)
                        and len(ids)==len(set(ids)) and not set(ids)&run_cycles and run['run_id'] not in run_ids):
                    sessions.add(actual_session); run_ids.add(run['run_id']); run_cycles.update(ids); reason=None
        if reason: excluded_runs.append({'run_id':run.get('run_id'),'reason':reason})
    critical=sum(c.get("critical_error") is True for c in cases)
    unreviewed=sum(c.get("published") is True and c.get("outside_cohort") is True and not c.get("review_receipt") for c in cases)
    new_issuer=[c for c in included if c.get("outside_cohort") is True and c.get("meaningfully_exercised") is True]
    publications=[c for c in included if c.get("proposed_publication") is True]
    precision=wilson(sum(c["correct"] for c in publications),len(publications))
    manual_reasons=list(manifest_errors)
    if verified is None:
        manual_reasons.append("receipt_backed_protocol_required")
    else:
        manual_reasons.extend(verified.reasons)
    if len(sessions)<10: manual_reasons.append("eligible_scheduled_sessions_below_10")
    if len(new_issuer)<100: manual_reasons.append("meaningful_new_issuer_cases_below_100")
    if critical: manual_reasons.append("critical_leakage_observed")
    if unreviewed: manual_reasons.append("outside_cohort_publication_without_review")
    if not verified or not verified.coverage_ready:
        manual_reasons.append("published_source_coverage_missing")
    family_results=[]
    strata={(c.get("family","unknown"),c.get("source_class","unknown")) for c in publications}
    if verified:
        strata.update(verified.required_strata)
    for family, source in sorted(strata):
        subset=[c for c in publications if c.get("family","unknown")==family and c.get("source_class","unknown")==source]
        stats=wilson(sum(c["correct"] for c in subset),len(subset))
        ready=(len(publications)>=300 and stats["n"]>=100 and stats["estimate"]>=.98 and stats["lower"]>=.95
               and precision["estimate"]>=.98 and precision["lower"]>=.95 and not manual_reasons
               and verified and verified.untouched_ready and operational
               and family!="unknown" and source!="unknown")
        family_results.append({"family":family,"source_class":source,"precision":stats,
                               "status":"eligible_for_activation_review" if ready else "remain_manual"})
    valid_gold=[]; audit_gold=[]; seen_gold=set(); excluded_gold=[]
    inventory=manifest.get('gold_event_ids')
    inventory_valid=(isinstance(inventory,list) and bool(inventory)
                     and all(isinstance(i,str) and i for i in inventory) and len(inventory)==len(set(inventory)))
    expected=set(inventory) if inventory_valid else None
    for row in gold:
        key=row.get("event_id")
        if (key and key not in seen_gold and row.get("prespecified") is True
                and row.get("in_source_scope") is True and type(row.get("detected")) is bool
                and row.get("adjudication_receipt")
                and all(row.get(k)==manifest.get(k) for k in ("corpus_hash","source_scope_hash"))
                and (expected is None or key in expected)):
            seen_gold.add(key)
            if row.get('evaluation_track')=='frozen_corpus_audit':
                audit_gold.append(row); excluded_gold.append({'event_id':key,'reason':'frozen_corpus_not_prospective'}); continue
            try:
                prospective_time=(frozen is not None and frozen<=instant(row['observed_at'])<=instant(row['reviewed_at']))
            except (KeyError,ValueError,TypeError): prospective_time=False
            if (row.get('evaluation_track')=='prospective' and row.get('evidence_kind')=='prospective'
                    and row.get('implementation_hash')==manifest.get('implementation_hash') and prospective_time):
                valid_gold.append(row)
            else: excluded_gold.append({'event_id':key,'reason':'prospective_gold_provenance_missing'})
        else: excluded_gold.append({'event_id':key,'reason':'gold_identity_scope_or_adjudication_invalid'})
    missing_gold=sorted(expected-seen_gold) if expected is not None else []
    non_events=[r for r in valid_gold if r.get("event_present") is False]
    positive_gold=[r for r in valid_gold if r.get("event_present") is not False]
    recall=wilson(sum(r["detected"] for r in positive_gold),len(positive_gold))
    unknown=[r for r in positive_gold if r.get("unknown_issuer") is True]
    unknown_recall=wilson(sum(r["detected"] for r in unknown),len(unknown))
    negative=[r for r in positive_gold if r.get("negative_outcome") is True]
    negative_recall=wilson(sum(r["detected"] for r in negative),len(negative))
    recall_reasons=list(manifest_errors)
    if verified is None:
        recall_reasons.append("prospective_inventory_not_verified")
    else:
        recall_reasons.extend(verified.reasons)
    if not inventory_valid: recall_reasons.append('prespecified_gold_inventory_missing')
    if missing_gold: recall_reasons.append('gold_adjudications_incomplete')
    if recall['n']<100: recall_reasons.append('prospective_gold_below_100')
    if recall['estimate'] is not None and recall['estimate']<.90: recall_reasons.append('overall_recall_below_threshold')
    if not unknown_recall['n'] or unknown_recall['estimate']<.90: recall_reasons.append('unknown_issuer_recall_insufficient')
    if verified and unknown_recall["n"] < verified.minimum_unknown:
        recall_reasons.append("prespecified_unknown_issuer_sample_insufficient")
    if verified and (negative_recall["n"] < verified.minimum_negative
                     or negative_recall["estimate"] is None or negative_recall["estimate"] < .90):
        recall_reasons.append("negative_outcome_recall_insufficient")
    if excluded_gold: recall_reasons.append('gold_records_excluded')
    recall_ready=not recall_reasons
    timing = dict(verified.timing) if verified else {
        "n": 0, "p95_seconds": None, "status": "pending",
        "reason_codes": ["verified_timing_receipts_required"],
        "ignored_caller_latency_rows": sum("latency_seconds" in c or "continuous_source" in c for c in cases)}
    timing_ready=bool(operational and timing.get("status")=="eligible_for_review")
    for row in family_results:
        if not recall_ready or not timing_ready:
            row["status"]="remain_manual"
    return {"contract":VERSION,"manifest_fingerprint":digest(manifest),"automatic_activation":False,
            "manual_pilot":{"status":"eligible_for_review" if not manual_reasons else "pending","reason_codes":manual_reasons,
                            "eligible_scheduled_sessions":len(sessions) if operational else 0,
                            "meaningful_new_issuer_cases":len(new_issuer) if operational else 0,
                            "descriptive_scheduled_sessions":len(sessions),
                            "descriptive_new_issuer_cases":len(new_issuer),"critical_errors":critical},
            "precision":precision,"family_source_gates":family_results,
            "recall":{"status":"eligible_for_review" if recall_ready else "pending","overall":recall,"unknown_issuers":unknown_recall,
                      "negative_outcomes":negative_recall,
                      "non_event_controls":{"n":len(non_events),"false_discoveries":sum(r["detected"] for r in non_events)},
                      "misses":[r["event_id"] for r in positive_gold if not r["detected"]],
                      'n_expected':len(expected) if expected is not None else None,'n_adjudicated':len(seen_gold),
                      'n_unadjudicated':len(missing_gold) if expected is not None else None,
                      'unadjudicated_event_ids':missing_gold,'excluded_gold':excluded_gold,'reason_codes':recall_reasons,
                      'frozen_corpus_audit':{'overall':wilson(sum(r['detected'] for r in audit_gold),len(audit_gold)),
                                             'misses':[r['event_id'] for r in audit_gold if not r['detected']]}},
            "timing":timing,
            "evidence_verification":{"status":"receipt_verified" if verified else "descriptive_inputs_only",
                                     "real_prospective":operational,
                                     "protocol_id":verified.protocol_id if verified else None,
                                     "external_human_identity_authenticated":False},
            "excluded_cases":exclusions,'excluded_runs':excluded_runs,"independence_caveat":"Wilson intervals do not adjust correlated cases; event-unit sampling requires review."}


def dependent_gates(evidence, current_coverage, *, candidates=(), store=None, registry=None, policies=None, now=None):
    """Typed dependency validation; raw dictionaries alone never mature gates."""
    from .gates import dependent_gates as checked_gates
    return checked_gates(evidence,current_coverage,candidates=candidates,store=store,registry=registry,policies=policies,now=now)
