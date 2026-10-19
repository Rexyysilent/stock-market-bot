"""OMNI-04 mandatory gates. Ranking or reviewer approval cannot bypass failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .classifier import predicates, subject_issuers
from .evidence import check_document, check_span, source_decision
from .identities import digest, instant
from .amounts import CONTRACT_AMOUNTS

VERSION = "omni-validator-3"


def validate(event, documents, registry, policies, *, now, audience="internal", review=None,
             max_age_hours=72, require_novel=True, previously_seen_claims=()):
    clock = instant(now)
    reasons, supporting, missing = set(), [], set()
    docs = {}
    for doc in documents:
        try:
            check_document(doc)
        except (ValueError, KeyError, TypeError):
            reasons.add("invalid_evidence")
            continue
        docs[doc["evidence_id"]] = doc
    claims = event.get("claims", [])
    if event.get("acquisition_gaps"):
        reasons.add("filing_documents_incomplete")
    if not claims:
        reasons.add("no_supported_claims")
    if audience not in ("internal", "commercial"):
        reasons.add("audience_not_approved")
    for doc in docs.values():
        try:
            check_document(doc)
        except (ValueError, KeyError, TypeError):
            reasons.add("invalid_evidence")
            continue
        policy = policies.get(doc["source_policy_id"])
        for purpose in ("local_analysis", "review_display", *(('commercial_display',) if audience == "commercial" else ())):
            reason = source_decision(policy, doc["url"], purpose)
            if reason:
                reasons.add(reason)
        if not policy or digest(policy) != doc["source_policy_fingerprint"]:
            reasons.add("policy_snapshot_mismatch")
        if policy and doc["source_class"] != policy.get("source_class"):
            reasons.add("source_class_mismatch")
        if policy and doc["reporting_party"] not in policy.get("reporting_parties", []):
            reasons.add("unverified_reporting_party")
        if doc["source_class"] in ("secondary", "discovery"):
            reasons.add("primary_claim_unverified")
        if not doc["body_complete"] or doc["completeness_reasons"] or not doc["text"].strip():
            reasons.add("content_incomplete")
        publication = doc["publication"]
        precision = publication["precision"]
        if precision == "timestamp":
            published = instant(publication["start"])
        elif precision == "date":
            published = datetime.fromisoformat(publication["start"]).replace(tzinfo=timezone.utc)
        else:
            published = None
            reasons.add("source_time_unknown")
        if published is not None:
            if published > clock or published > instant(doc["first_observed_at"]) + timedelta(minutes=5):
                reasons.add("future_source_time")
            if clock - published > timedelta(hours=max_age_hours):
                reasons.add("outside_recency")
        if instant(doc["first_observed_at"]) > clock:
            reasons.add("future_observation_time")
    outcomes = {}
    for claim in claims:
        support = claim.get("support", {})
        doc = docs.get(support.get("evidence_id"))
        if not doc or not check_span(doc, support):
            reasons.add("unsupported_claim")
            continue
        if (claim.get("predicate"), claim.get("value")) not in predicates(support["text"]):
            reasons.add("unsupported_claim")
            continue
        if claim.get("reporting_party") != doc["reporting_party"] or claim.get("class") != "attributed_report":
            reasons.add("attribution_mismatch")
        resolved = registry.resolve(support["text"], known_by=now)
        subjects = subject_issuers(support["text"], resolved)
        if any(subject not in subjects for subject in claim.get("subject_ids", [])):
            reasons.add("subject_not_supported")
        if any(registry.entities[subject].get("verification") not in ("primary_verified", "snapshot_matched") for subject in claim.get("subject_ids", []) if subject in registry.entities):
            reasons.add("entity_verification_incomplete")
        if resolved["unresolved"]:
            reasons.add("ambiguous_alias")
        scope = claim.get("scope_id")
        if event.get("family") == "clinical_result":
            if not scope or scope not in registry.entities or registry.entities[scope]["kind"] != "trial":
                reasons.add("trial_unresolved")
                missing.add("trial_identity")
            elif claim["predicate"] == "phase" and registry.entities[scope].get("phase") not in (None, claim["value"]):
                reasons.add("trial_phase_mismatch")
            named_trials = {m["entity_id"] for m in resolved["mentions"] if m["kind"] == "trial"}
            if named_trials and named_trials != {scope}:
                reasons.add("trial_mismatch")
            scope_support = claim.get("scope_support")
            if not scope_support or not check_span(doc, scope_support):
                reasons.add("scope_not_supported")
            elif scope not in {m["entity_id"] for m in registry.resolve(scope_support["text"], known_by=now)["mentions"] if m["kind"] == "trial"}:
                reasons.add("scope_not_supported")
            elif scope_support["start"] > support["start"] or "\n" in doc["text"][scope_support["end"]:support["start"]]:
                reasons.add("scope_not_supported")
            if claim.get("historical"):
                reasons.add("historic_result_contamination")
            if claim["predicate"].endswith("endpoint_status") and claim.get("future"):
                reasons.add("planned_not_actual")
            if claim["predicate"] == "primary_endpoint_status" and any(
                p == "analysis_class" and value == "exploratory" for p, value in predicates(support["text"])
            ):
                reasons.add("posthoc_not_primary")
        elif not claim.get("subject_ids"):
            reasons.add("entity_unresolved")
        if claim.get("predicate") in CONTRACT_AMOUNTS:
            if claim.get("units") != "USD":
                reasons.add("unit_mismatch")
            if claim.get("numeric_result") != claim.get("value") or claim.get("qualifiers") != ["stated_contract_amount_not_revenue"]:
                reasons.add("unsupported_claim")
        elif claim.get("numeric_result") is not None:
            reasons.add("numeric_extraction_not_supported")
        if claim.get("value") in ("not_established", "approval_not_established", "regulatory_lead"):
            reasons.add("event_stage_not_established")
        key = (scope, claim["predicate"])
        outcomes.setdefault(key, set()).add(claim["value"])
        supporting.append(claim["claim_id"])
    if any(len(values) > 1 for values in outcomes.values()):
        reasons.add("contradiction_unresolved")
    if require_novel and claims and all(c.get("semantic_id") in previously_seen_claims for c in claims):
        reasons.add("no_material_change")
    # A valid clinical card requires the primary outcome and preserves missing
    # secondary/numeric/overall-survival fields rather than filling from context.
    if event.get("family") == "clinical_result" and not any(c["predicate"] == "primary_endpoint_status" for c in claims):
        missing.add("primary_endpoint_status")
        reasons.add("clinical_result_incomplete")
    payload_hash = digest({"event": event, "documents": documents})
    approved = bool(review and review.get("decision") == "approve" and review.get("reviewer")
                    and review.get("input_hash") == payload_hash)
    if approved:
        try:
            reviewed = instant(review["reviewed_at"])
            approved = reviewed <= clock and all(reviewed >= instant(d["first_observed_at"]) for d in documents)
        except (ValueError, KeyError, TypeError):
            approved = False
    if not approved:
        reasons.add("review_required")
    hard = reasons - {"review_required"}
    denied = {"purpose_not_approved", "source_not_approved", "source_route_not_approved", "untrusted_url", "audience_not_approved", "invalid_evidence"}
    status = "suppressed" if reasons & denied else "review_required" if hard or not approved else "eligible"
    grade = ("contested" if "contradiction_unresolved" in reasons else "lead_only" if hard else "primary_document_supported")
    return {"validator_version": VERSION, "policy_fingerprint": digest(policies),
            "registry_fingerprint": registry.fingerprint(), "input_hash": payload_hash,
            "purpose": "event_review", "audience": audience,
            "editorial_status": status, "signal_status": "ineligible", "signal_eligible": False,
            "signal_exclusion_reasons": ["event_research_only", "family_not_approved"],
            "reason_codes": sorted(reasons), "supporting_claim_ids": sorted(set(supporting)),
            "missing_requirements": sorted(missing), "evidence_grade": grade,
            "independent_origins": len({d["origin_cluster_id"] for d in docs.values()}),
            "reviewer": review.get("reviewer") if approved else None,
            "reviewed_at": review.get("reviewed_at") if approved else None}
