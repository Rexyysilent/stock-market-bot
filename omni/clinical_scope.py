"""Versioned clinical definitions and aliases, supplied by an operator.

The default registry contains identity hints, not adjudicated clinical meaning.
This optional catalog binds exact evidence revisions, source policies, a review
receipt and a disclosure-time version. It does not authenticate the reviewer or
approve a source purpose. Pending research records cannot supply a mapping.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re

from .evidence import check_document, check_span, source_decision
from .identities import digest, instant, time_interval

VERSION = "omni-clinical-scope-catalog-1"
BINDING_VERSION = "omni-clinical-scope-binding-1"


def normalized(value):
    return " ".join(value.casefold().split())


def version_payload(row):
    return {k: v for k, v in row.items() if k not in ("revision_id", "review")}


def review_input(row, documents, policies):
    """Hash exact version plus all referenced evidence and policy snapshots."""
    return digest({"version": version_payload(row), "documents": documents, "policies": policies})


def _bounds(value):
    interval = time_interval(value)
    if interval["precision"] not in ("timestamp", "date"):
        raise ValueError("clinical_scope_time_precision_unsupported")
    if interval["precision"] == "timestamp":
        stamp = instant(interval["start"])
        return stamp, stamp
    return tuple(datetime.fromisoformat(interval[k]).replace(tzinfo=timezone.utc) for k in ("start", "end"))


def _atom(value, docs, *, literal=True):
    if not isinstance(value, dict) or set(value) != {"value", "support"}:
        raise ValueError("clinical_scope_atom_invalid")
    if not isinstance(value["value"], str) or not value["value"].strip():
        raise ValueError("clinical_scope_atom_invalid")
    support = value["support"]
    doc = docs.get((support.get("evidence_id"), support.get("revision_id"))) if isinstance(support, dict) else None
    if not doc or not check_span(doc, support):
        raise ValueError("clinical_scope_evidence_missing")
    if literal and normalized(value["value"]) != normalized(support["text"]):
        raise ValueError("clinical_scope_literal_mismatch")
    return value["value"]


def checked_versions(catalog, registry, *, known_by):
    """Return individually usable versions; rejected versions remain reasons.

    A malformed or pending row never lends aliases to a different valid row.
    Every row has its own documents/policies/review to avoid approval reuse when
    another version or source revision is appended to the catalog.
    """
    if not isinstance(catalog, dict) or catalog.get("contract") != VERSION:
        return [], ["clinical_scope_catalog_invalid"]
    clock = instant(known_by)
    rows = catalog.get("trial_versions")
    if not isinstance(rows, list) or len(rows) > 1000:
        return [], ["clinical_scope_catalog_invalid"]
    approved, reasons = [], []
    for row in rows:
        try:
            if not isinstance(row, dict) or row.get("trial_id") not in registry.entities:
                raise ValueError("clinical_scope_trial_unknown")
            if registry.entities[row["trial_id"]].get("kind") != "trial":
                raise ValueError("clinical_scope_trial_unknown")
            if row.get("contract") != BINDING_VERSION or row.get("revision_id") != digest(version_payload(row)):
                raise ValueError("clinical_scope_version_hash_mismatch")
            documents, policies = row.get("documents"), row.get("policies")
            if not isinstance(documents, list) or not documents or not isinstance(policies, dict):
                raise ValueError("clinical_scope_evidence_missing")
            docs = {}
            for doc in documents:
                check_document(doc)
                key = doc["evidence_id"], doc["revision_id"]
                if key in docs:
                    raise ValueError("clinical_scope_evidence_duplicate")
                docs[key] = doc
                policy = policies.get(doc["source_policy_id"])
                if (any(source_decision(policy, doc["url"], purpose) for purpose in ("acquire", "store_text", "local_analysis")) or not policy
                        or digest(policy) != doc["source_policy_fingerprint"]
                        or doc["source_class"] not in ("issuer", "registry", "regulator", "filing")
                        or policy.get("source_class") != doc["source_class"]
                        or doc["reporting_party"] not in policy.get("reporting_parties", [])
                        or not doc["body_complete"] or doc["completeness_reasons"]
                        or instant(doc["first_observed_at"]) > clock or instant(doc["retrieved_at"]) > clock):
                    raise ValueError("clinical_scope_source_not_usable")
                _bounds(doc["publication"]["raw"])
            review = row.get("review") or {}
            if (review.get("decision") != "approve" or not isinstance(review.get("reviewer"), str)
                    or not review["reviewer"].strip() or review.get("input_hash") != review_input(row, documents, policies)
                    or instant(review["reviewed_at"]) > clock
                    or any(instant(review["reviewed_at"]) < instant(d["retrieved_at"]) for d in documents)):
                raise ValueError("clinical_scope_review_required")
            trial = _atom(row.get("trial_identity"), docs, literal=False)
            if (trial != row["trial_id"] or row["trial_identity"]["support"]["text"].upper() != trial.removeprefix("trial:")):
                raise ValueError("clinical_scope_trial_proof_mismatch")
            phase = _atom(row.get("phase"), docs, literal=False)
            source_phase = re.sub(r"\s+", "", row["phase"]["support"]["text"]).upper()
            if phase != source_phase or not re.fullmatch(r"PHASE(?:1|1B|1/2|2|2B|2/3|3|4)", phase):
                raise ValueError("clinical_scope_phase_proof_mismatch")
            start = _atom(row.get("valid_from"), docs)
            lo, _ = _bounds(start)
            end = _atom(row["valid_to"], docs) if row.get("valid_to") else None
            hi = _bounds(end)[0] if end else None
            if hi is not None and hi <= lo:
                raise ValueError("clinical_scope_version_interval_invalid")
            native = row.get("source_native_id")
            if not isinstance(native, str) or not any(d["native_id"] == native for d in documents):
                raise ValueError("clinical_scope_native_version_missing")
            aliases = row.get("trial_aliases")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError("clinical_scope_alias_missing")
            for alias in aliases:
                _atom(alias, docs)
            for collection, key in (("endpoints", "endpoint_id"), ("arms", "arm_id"),
                                    ("populations", "population_id"), ("cohorts", "cohort_id")):
                entries = row.get(collection)
                if not isinstance(entries, list) or (collection != "cohorts" and not entries):
                    raise ValueError("clinical_scope_definition_missing")
                ids = set()
                for entry in entries:
                    if not isinstance(entry.get(key), str) or not entry[key] or entry[key] in ids:
                        raise ValueError("clinical_scope_definition_identity_invalid")
                    ids.add(entry[key])
                    _atom(entry.get("label"), docs)
                    if not isinstance(entry.get("aliases"), list):
                        raise ValueError("clinical_scope_alias_missing")
                    for alias in entry["aliases"]:
                        _atom(alias, docs)
                    if collection != "arms":
                        _atom(entry.get("definition"), docs)
                    if collection == "endpoints" and _atom(entry.get("rank"), docs) not in ("primary", "secondary", "exploratory"):
                        raise ValueError("clinical_scope_endpoint_rank_invalid")
                    if collection == "arms" and _atom(entry.get("role"), docs) not in ("treatment", "comparator"):
                        raise ValueError("clinical_scope_arm_role_invalid")
                    if collection == "populations" and _atom(entry.get("denominator_kind"), docs) not in (
                            "randomized", "evaluable", "treated", "safety", "other"):
                        raise ValueError("clinical_scope_denominator_kind_invalid")
            approved.append(row)
        except (ValueError, KeyError, TypeError, AttributeError):
            # A bounded public reason is sufficient; never echo source content.
            reasons.append("clinical_scope_version_unusable")
    return approved, sorted(set(reasons))


def bind_scope(scope, doc, registry, *, known_by):
    """Resolve explicit scope only. Never fill a missing rank, arm or population."""
    catalog = getattr(registry, "clinical_catalog", None)
    if catalog is None:
        return None, []  # Literal v1 extraction asserts no canonical definition.
    try:
        disclosure_lo, disclosure_hi = _bounds(doc["publication"]["raw"])
    except (ValueError, KeyError, TypeError):
        return None, ["clinical_scope_disclosure_time_unknown"]
    versions, _ = checked_versions(catalog, registry, known_by=known_by)
    trial_id = (scope.get("trial_id") or {}).get("value")
    candidates = []
    for row in versions:
        if row["trial_id"] != trial_id:
            continue
        valid_lo = _bounds(row["valid_from"]["value"])[0]
        valid_hi = _bounds(row["valid_to"]["value"])[0] if row.get("valid_to") else None
        # Date-only availability cannot prove availability earlier on that date.
        available = max(_bounds(d["publication"]["raw"])[1] for d in row["documents"])
        if (valid_lo <= disclosure_lo and (valid_hi is None or disclosure_hi < valid_hi
                or disclosure_hi == valid_hi and disclosure_lo < disclosure_hi)
                and available <= disclosure_lo):
            candidates.append(row)
    if len(candidates) != 1:
        return None, ["clinical_scope_version_ambiguous" if candidates else "clinical_scope_version_unavailable_at_disclosure"]
    row = candidates[0]
    for other in catalog["trial_versions"]:
        if other is row or not isinstance(other, dict) or other.get("trial_id") != trial_id:
            continue
        other_approved = other in versions
        if other_approved:
            earliest_availability = max(_bounds(d["publication"]["raw"])[0] for d in other["documents"])
            if (earliest_availability > disclosure_hi or
                    disclosure_lo < disclosure_hi and earliest_availability == disclosure_hi):
                continue
        # A known competing draft must not silently leave an open-ended old
        # definition in force. Its missing review is a blocker, not permission
        # to choose whichever older version happens to be approved.
        try:
            low = _bounds(other["valid_from"]["value"])[0]
            high = _bounds(other["valid_to"]["value"])[0] if other.get("valid_to") else None
            overlapping = (low <= disclosure_lo or low < disclosure_hi) and (high is None or disclosure_lo < high)
        except (ValueError, KeyError, TypeError):
            overlapping = True
        if overlapping:
            return None, ["clinical_scope_version_ambiguous" if other_approved else "clinical_scope_competing_version_review_required"]
    if doc.get("evidence_kind") != "synthetic" and any(d.get("evidence_kind") == "synthetic" for d in row["documents"]):
        return None, ["clinical_scope_synthetic_mapping_for_real_source"]
    trial_text = normalized(scope["trial_id"]["support"]["text"])
    if trial_text not in {normalized(a["value"]) for a in row["trial_aliases"]} | {trial_id.removeprefix("trial:").casefold()}:
        return None, ["clinical_scope_trial_alias_unmapped"]
    reported = scope.get("reported_phase")
    if reported and reported["value"] != row["phase"]["value"] and (reported["value"], row["phase"]["value"]) not in {
            ("PHASE1B", "PHASE1"), ("PHASE2B", "PHASE2")}:
        return None, ["clinical_scope_trial_phase_mismatch"]
    bindings = {}
    for field, collection, key in (("endpoint", "endpoints", "endpoint_id"),
                                  ("analysis_population", "populations", "population_id"),
                                  ("treatment_arm", "arms", "arm_id"),
                                  ("comparison_arm", "arms", "arm_id"),
                                  ("cohort", "cohorts", "cohort_id")):
        observed = scope.get(field)
        if observed is None:
            bindings[field] = None
            continue
        token = normalized(observed["support"]["text"])
        choices = [e for e in row[collection] if token in
                   {normalized(a["value"]) for a in [e["label"], *e["aliases"]]}]
        if len(choices) != 1:
            return None, ["clinical_scope_" + field + "_unmapped_or_ambiguous"]
        entry = choices[0]
        if field in ("treatment_arm", "comparison_arm") and entry["role"]["value"] != (
                "treatment" if field == "treatment_arm" else "comparator"):
            return None, ["clinical_scope_arm_role_mismatch"]
        if field == "endpoint" and scope.get("endpoint_rank") and scope["endpoint_rank"]["value"] != entry["rank"]["value"]:
            return None, ["clinical_scope_endpoint_rank_mismatch"]
        bindings[field] = {"canonical_id": entry[key], "source_mention": deepcopy(observed["support"]),
                           "definition": deepcopy(entry)}
    return {"contract": BINDING_VERSION, "trial_id": trial_id, "version_revision": row["revision_id"],
            "source_native_id": row["source_native_id"], "phase": deepcopy(row["phase"]),
            "valid_from": deepcopy(row["valid_from"]), "valid_to": deepcopy(row.get("valid_to")),
            "catalog_fingerprint": digest(catalog), "review_input_hash": row["review"]["input_hash"],
            "evidence_sources": [{k: d[k] for k in ("evidence_id", "revision_id", "native_id", "url", "source_policy_id", "source_policy_fingerprint")}
                                 for d in row["documents"]],
            "disclosure_time": deepcopy(doc["publication"]), "bindings": bindings,
            "human_review_status": "pending", "signal_eligible": False}, []


def binding_identity(binding):
    """Identity includes definition/version changes, not a source mention path."""
    if binding is None:
        return None
    return {"version_revision": binding["version_revision"], "trial_id": binding["trial_id"],
            "bindings": {k: v["canonical_id"] if v else None for k, v in binding["bindings"].items()}}


def binding_documents(binding, registry):
    """Resolve archived definition evidence separately from result recency.

    The caller must still validate the binding. This hook supplies dependency
    lineage, purpose checks and review attribution; it grants no source right.
    """
    catalog = getattr(registry, "clinical_catalog", None)
    if not isinstance(binding, dict) or not isinstance(catalog, dict):
        return []
    matches = [r for r in catalog.get("trial_versions", []) if isinstance(r, dict)
               and r.get("trial_id") == binding.get("trial_id")
               and r.get("revision_id") == binding.get("version_revision")]
    return deepcopy(matches[0].get("documents", [])) if len(matches) == 1 else []


def referenced_documents(event, registry):
    """Unique exact definition revisions referenced by an event's claims."""
    found = {}
    for claim in event.get("claims", []):
        result = claim.get("numeric_result")
        if not isinstance(result, dict):
            continue
        for doc in binding_documents(result.get("scope_binding"), registry):
            found[(doc["evidence_id"], doc["revision_id"])] = doc
    return [found[key] for key in sorted(found)]


def dependency_references(event):
    """Exact definition revisions recorded in independently validated bindings."""
    refs=set()
    for claim in event.get("claims", []):
        binding=(claim.get("numeric_result") or {}).get("scope_binding")
        if not binding:
            continue
        for source in binding.get("evidence_sources", []):
            refs.add((source["evidence_id"], source["revision_id"]))
    return refs
