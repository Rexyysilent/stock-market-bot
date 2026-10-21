"""OMNI-08 shadow quality decisions, actual denominators and information timing."""
from __future__ import annotations

import math

from .identities import digest, instant

VERSION = "omni-quant-quality-1"


def quality(observation, *, latest_settled_session, approved_feature_versions=()):
    reasons=[]
    if observation.get("source_session") != latest_settled_session:
        reasons.append("stale_measurement")
    if observation.get("session_complete") is not True:
        reasons.append("partial_session")
    if observation.get("source_complete") is not True:
        reasons.append("source_incomplete")
    if observation.get("feature_version") not in approved_feature_versions:
        reasons.append("feature_not_approved")
    if not observation.get("universe_fingerprint") or not observation.get("pipeline_era"):
        reasons.append("population_not_captured")
    value=observation.get("value")
    if type(value) not in (int,float) or not math.isfinite(value):
        reasons.append("measurement_unavailable")
    if observation.get("kind")=="options":
        # Trading volume/OI alone says nothing about direction or trade intent.
        reasons.append("options_context_only")
        if not observation.get("volume_session") or not observation.get("oi_as_of"):
            reasons.append("options_time_basis_missing")
    if observation.get("kind")=="regime" and any(v is None for v in observation.get("regime_inputs",[])):
        reasons.append("regime_inputs_unknown")
    if observation.get("kind")=="regime" and not observation.get("regime_inputs"):
        reasons.append("regime_inputs_unknown")
    baseline=observation.get("baseline") or {}
    if baseline.get("complete_sessions",0) < baseline.get("required_sessions",float("inf")):
        reasons.append("baseline_immature")
    return {"contract":VERSION,"feature_version":observation.get("feature_version"),
            "input_hash":digest(observation),"measurement_status":"usable" if not reasons else "withheld",
            "reason_codes":sorted(set(reasons)),"signal_eligible":False,"mode":"shadow"}


def readiness(rows, *, minimum=30):
    """Separate eras/features/cohorts and count the actual usable populations."""
    groups={}
    for row in rows:
        key=(row.get("pipeline_era"),row.get("feature_version"),row.get("universe_fingerprint"))
        group=groups.setdefault(key,[]); group.append(row)
    result=[]
    for key, group in sorted(groups.items(),key=lambda item:str(item[0])):
        usable=[r for r in group if r.get("outcome_status")=="measured" and type(r.get("return")) in (int,float) and math.isfinite(r["return"])]
        directional=[r for r in usable if type(r.get("direction")) in (int,float) and r.get("direction") in (-1,1)]
        independent={(r.get("issuer_id"),r.get("source_session"),r.get("event_cluster_id")) for r in usable
                     if r.get("issuer_id") and r.get("source_session") and r.get("event_cluster_id")}
        statuses={status:sum(str(r.get("outcome_status"))==status for r in group) for status in sorted({str(r.get("outcome_status")) for r in group})}
        result.append({"pipeline_era":key[0],"feature_version":key[1],"universe_fingerprint":key[2],
            "n_total":len(group),"n_usable":len(usable),"n_directional":len(directional),
            "n_observed_clusters":len(independent),"effective_n":None,"status_counts":statuses,
            "descriptive_status":"ready" if all(key) and len(usable)>=minimum else "accumulating",
            "directional_status":"ready" if all(key) and len(directional)>=minimum else "accumulating",
            "inference":"withheld","reason":"cluster counts alone do not establish effective sample size"})
    return result


def information_sequence(*, published_at, source_available_at, observed_at, measurement_at):
    times={k:instant(v) if v is not None else None for k,v in
           {"published":published_at,"available":source_available_at,"observed":observed_at,"measurement":measurement_at}.items()}
    reasons=[]
    if times["available"] is None: reasons.append("source_availability_unknown")
    if times["observed"] is None or times["measurement"] is None: reasons.append("information_sequence_unknown")
    elif times["observed"] > times["measurement"]: reasons.append("observed_after_measurement")
    if times["available"] and times["observed"] and times["available"]>times["observed"]: reasons.append("invalid_availability_sequence")
    latency=(times["observed"]-times["available"]).total_seconds() if times["observed"] and times["available"] and times["available"]<=times["observed"] else None
    return {"contract":VERSION,"reason_codes":reasons,"source_available_to_candidate_seconds":latency,
            "hindsight_credit_allowed":not reasons,"signal_eligible":False}
