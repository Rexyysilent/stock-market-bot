"""OMNI-04 mandatory gates. Ranking or reviewer approval cannot bypass failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .classifier import predicates, subject_issuers, sentences, HISTORICAL, FUTURE
from .evidence import check_document, check_span, source_decision
from .identities import digest, instant
from .amounts import (CONTRACT_AMOUNTS, SCOPED_PREDICATE, SCOPED_QUALIFIERS,
                     parse_amount_sentence, amount_projection)
from .clinical_numerics import (PREDICATE as NUMERIC_PREDICATE, QUALIFIERS as NUMERIC_QUALIFIERS,
                               parse_sentence, phase_compatible, result_projection)
from .clinical_tables import parse_tables
from .clinical_scope import binding_documents

VERSION = "omni-validator-5"


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
    numeric_cache = {}
    amount_cache = {}
    for claim in claims:
        support = claim.get("support", {})
        doc = docs.get(support.get("evidence_id"))
        if not doc or not check_span(doc, support):
            reasons.add("unsupported_claim")
            continue
        numeric_claim = claim.get("predicate") == NUMERIC_PREDICATE
        amount_claim = claim.get("predicate") == SCOPED_PREDICATE
        if numeric_claim:
            # Parse the original complete sentence again. A cropped proof span
            # must not erase a competing endpoint, negation or unknown trial.
            cache_key = (doc["evidence_id"], doc["revision_id"])
            if cache_key not in numeric_cache:
                table = parse_tables(doc, registry, known_by=now)
                numeric_cache[cache_key] = {
                    (start, end): parse_sentence(doc, start, end, registry, known_by=now)["results"]
                    for start, end, _ in sentences(doc["text"])
                    if not any(start < hi and end > lo for lo, hi in table["blocks"])}
                numeric_cache[cache_key].update({
                    (r["support"]["start"], r["support"]["end"]): [r] for r in table["results"]})
            expected = numeric_cache[cache_key].get((support["start"], support["end"]), [])
            result = claim.get("numeric_result")
            if (not isinstance(result, dict) or result not in expected
                    or claim.get("value") != result.get("result_id")
                    or claim.get("scope_id") != result.get("scope", {}).get("trial_id", {}).get("value")
                    or event.get("family") != "clinical_result"
                    or claim.get("units") is not None or claim.get("qualifiers") != NUMERIC_QUALIFIERS):
                reasons.add("unsupported_clinical_numeric_result")
                continue
            if result.get("scope_binding"):
                definition_docs = binding_documents(result["scope_binding"], registry)
                if not definition_docs:
                    reasons.add("clinical_scope_evidence_missing")
                for definition_doc in definition_docs:
                    current_policy = policies.get(definition_doc["source_policy_id"])
                    for purpose in ("local_analysis", "review_display", *(('commercial_display',) if audience == "commercial" else ())):
                        reason = source_decision(current_policy, definition_doc["url"], purpose)
                        if reason:
                            reasons.add(reason)
                    if not current_policy or digest(current_policy) != definition_doc["source_policy_fingerprint"]:
                        reasons.add("clinical_scope_policy_snapshot_mismatch")
        elif amount_claim:
            cache_key = (doc["evidence_id"], doc["revision_id"])
            if cache_key not in amount_cache:
                amount_cache[cache_key] = {(start, end): parse_amount_sentence(doc, start, end)["atoms"]
                    for start, end, _ in sentences(doc["text"])}
            expected = amount_cache[cache_key].get((support["start"], support["end"]), [])
            amount = claim.get("amount_atom")
            role = amount.get("role") if isinstance(amount, dict) else None
            family = ("financing" if isinstance(role, dict) and
                role.get("value") in ("financing_authority", "gross_proceeds", "net_proceeds") else "contract")
            if (not isinstance(amount, dict) or amount not in expected or claim.get("value") != amount.get("amount_id")
                    or event.get("family") != family or claim.get("units") is not None
                    or claim.get("numeric_result") is not None or claim.get("qualifiers") != SCOPED_QUALIFIERS):
                reasons.add("unsupported_scoped_amount")
                continue
        elif (claim.get("predicate"), claim.get("value")) not in predicates(support["text"]):
            reasons.add("unsupported_claim")
            continue
        if (claim.get("historical") != bool(HISTORICAL.search(support["text"]))
                or claim.get("future") != bool(FUTURE.search(support["text"]))):
            reasons.add("claim_qualifier_mismatch")
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
            elif claim["predicate"] == "phase":
                registered_phase = registry.entities[scope].get("phase")
                if getattr(registry, "clinical_catalog", None) is not None:
                    bindings = [c["numeric_result"]["scope_binding"] for c in claims
                        if c.get("predicate") == NUMERIC_PREDICATE and isinstance(c.get("numeric_result"), dict)
                        and c["numeric_result"].get("scope_binding") and c.get("scope_id") == scope
                        and c.get("support", {}).get("evidence_id") == doc["evidence_id"]]
                    phases = {b["phase"]["value"] for b in bindings}
                    if len(phases) != 1:
                        reasons.add("clinical_scope_phase_version_unavailable")
                    registered_phase = next(iter(phases)) if len(phases) == 1 else None
                if not phase_compatible(claim["value"], registered_phase):
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
        elif claim.get("numeric_result") is not None and not numeric_claim:
            reasons.add("numeric_extraction_not_supported")
        if claim.get("amount_atom") is not None and not amount_claim:
            reasons.add("unsupported_scoped_amount")
        if amount_claim and claim.get("future") and claim["amount_atom"]["role"]["value"] in (
                "obligation", "gross_proceeds", "net_proceeds"):
            reasons.add("planned_not_actual")
        if claim.get("value") in ("not_established", "approval_not_established", "regulatory_lead"):
            reasons.add("event_stage_not_established")
        key = (scope, claim["predicate"], claim["numeric_result"]["analysis_scope_id"] if numeric_claim else
               claim["amount_atom"]["amount_scope_id"] if amount_claim else None)
        outcomes.setdefault(key, set()).add(claim["value"])
        supporting.append(claim["claim_id"])
    if any(len(values) > 1 for values in outcomes.values()):
        reasons.add("contradiction_unresolved")
    if event.get("family") == "clinical_result":
        try:
            expected_results = result_projection(claims)
        except (KeyError, TypeError, ValueError):
            expected_results = None
            reasons.add("unsupported_clinical_numeric_result")
        if event.get("clinical_context", {}).get("numerical_results") != expected_results:
            reasons.add("numeric_context_not_supported")
    try:
        if event.get("scoped_amounts") != amount_projection(claims):
            reasons.add("amount_context_not_supported")
    except (KeyError, TypeError, ValueError):
        reasons.add("amount_context_not_supported")
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
            "assertion_origin_units": len({d["origin_cluster_id"] for d in docs.values()}),
            "independent_origins": None, "independence_assessed": False,
            "reviewer": review.get("reviewer") if approved else None,
            "reviewed_at": review.get("reviewed_at") if approved else None}
