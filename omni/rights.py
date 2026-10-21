"""Exact field/purpose rights records; no inferred legal approval or audience grant."""
from __future__ import annotations

from .attestations import validate_attestation
from .evidence import source_decision
from .identities import digest, instant
from .lifecycle import implementation_hash, reference_key, resolve_reference
from .receipts import get_receipt, list_receipts, put_receipt

VERSION = "omni-field-rights-1"
FIELDS = {"source.url", "source.title", "source.native_id", "source.text", "source.times",
          "claim.value", "claim.scope", "claim.numeric", "claim.amount", "review.identity",
          "review.decision", "coverage", "correction.history"}
PURPOSES = {"review_display", "local_export", "commercial_display", "model_processing"}
TRANSFORMS = {"canonical_metadata", "derived_summary", "exact_excerpt", "full_text", "sanitized_history"}


def _shape(record):
    if record.get("contract") != VERSION or record.get("field") not in FIELDS or record.get("purpose") not in PURPOSES:
        raise ValueError("unknown field/purpose rights contract")
    reference_key(record.get("source_ref"))
    for key in ("audience", "rightsholder", "attribution", "retention_rule", "policy_hash", "implementation_hash"):
        if not isinstance(record.get(key), str) or not record[key].strip() or len(record[key]) > 1000:
            raise ValueError("rights scope/provenance missing: "+key)
    if record.get("decision") not in {"approved", "denied"} or record.get("transformation") not in TRANSFORMS:
        raise ValueError("rights decision/transformation invalid")
    expected = "exact_excerpt" if record["field"]=="source.text" else (
        "derived_summary" if record["field"].startswith("claim.") else
        "sanitized_history" if record["field"] in {"coverage","correction.history","review.decision"} else "canonical_metadata")
    if record["transformation"] not in ({"exact_excerpt","full_text"} if record["field"]=="source.text" else {expected}):
        raise ValueError("rights transformation does not authorize this field projection")
    if record.get("evidence_kind") not in {"operator_evidence", "synthetic"}:
        raise ValueError("rights require a real record or explicitly synthetic exercise")
    if not instant(record["effective_at"]) < instant(record["expires_at"]):
        raise ValueError("bounded rights effective/expiry interval required")
    if record["field"] == "source.text":
        if type(record.get("max_characters")) is not int or not 0 <= record["max_characters"] <= 500000:
            raise ValueError("explicit excerpt/full-text character limit required")
    return record


def validate_rights(store, receipt_id, policies, *, now, allow_synthetic=False):
    envelope=get_receipt(store, receipt_id, kind="field_rights")
    record=_shape(envelope["payload"])
    if record["implementation_hash"] != implementation_hash() or record["policy_hash"] != digest(policies):
        raise ValueError("rights version changed")
    if not instant(record["effective_at"]) <= instant(now) < instant(record["expires_at"]):
        raise ValueError("rights not effective or expired")
    if instant(envelope["observed_at"]) > instant(now):
        raise ValueError("rights receipt from future")
    source=resolve_reference(store, record["source_ref"])
    if source["kind"] != "document":
        raise ValueError("field rights require exact retained source revision")
    doc=source["document"]
    policy=policies.get(doc["source_policy_id"])
    if not policy or doc["source_policy_fingerprint"] != digest(policy):
        raise ValueError("source policy changed since evidence capture")
    # Local export has its own field grant plus the baseline permitted review
    # purpose. It grants neither commercial display nor external distribution.
    purpose="review_display" if record["purpose"]=="local_export" else record["purpose"]
    reason=source_decision(policy,doc["url"],purpose)
    if reason:
        raise ValueError("source-purpose rights unavailable: "+reason)
    if record["evidence_kind"]=="synthetic" and not allow_synthetic:
        raise ValueError("synthetic rights cannot authorize actual use")
    body={k:v for k,v in record.items() if k!="attestation_ref"}
    att=validate_attestation(store,record.get("attestation_ref"),purpose="field_rights",subject_hash=digest(body),
        bindings={"policy_hash":record["policy_hash"],"implementation_hash":record["implementation_hash"],
                  "source_policy_id":record["source_ref"]["source_id"]},now=now,allow_synthetic=allow_synthetic)
    if att["payload"]["evidence_kind"] != record["evidence_kind"]:
        raise ValueError("rights/attestation evidence kinds differ")
    for revoked in list_receipts(store,kind="rights_revocation"):
        if revoked["payload"].get("rights_ref")==receipt_id and instant(revoked["observed_at"])<=instant(now):
            raise ValueError("rights revoked")
    return envelope


def record_rights(store, record, policies, *, now, allow_synthetic=False):
    _shape(record)
    # Validate before the immutable write using the same exact checks as readers.
    from .receipts import transaction
    with transaction(store):
        envelope=put_receipt(store,"field_rights",record,observed_at=now,
            refs=[record.get("attestation_ref")],manage_transaction=False)
        validate_rights(store,envelope["receipt_id"],policies,now=now,allow_synthetic=allow_synthetic)
    return envelope


def revoke_rights(store, rights_ref, *, now, reason):
    get_receipt(store,rights_ref,kind="field_rights")
    if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:
        raise ValueError("rights revocation reason required")
    return put_receipt(store,"rights_revocation",{"contract":VERSION,"rights_ref":rights_ref,"reason":reason},
        observed_at=now,refs=[rights_ref])


def field_decision(store, source_ref, field, purpose, audience, policies, *, now, allow_synthetic=False):
    if field not in FIELDS or purpose not in PURPOSES or not audience:
        raise ValueError("explicit supported field, purpose and audience required")
    reference_key(source_ref)
    matching=[]; failures=[]
    for envelope in list_receipts(store,kind="field_rights"):
        p=envelope["payload"]
        if (p.get("source_ref"),p.get("field"),p.get("purpose"),p.get("audience")) != (source_ref,field,purpose,audience):
            continue
        try:
            valid=validate_rights(store,envelope["receipt_id"],policies,now=now,allow_synthetic=allow_synthetic)
            matching.append(valid)
        except (KeyError,ValueError) as exc:
            failures.append(str(exc))
    # Explicit denials remain effective; changing a denied decision requires a
    # separate revocation record, never silently picking a newer approval.
    approved=bool(matching) and all(e["payload"]["decision"]=="approved" for e in matching)
    return {"allowed":approved,"field":field,"purpose":purpose,"audience":audience,
            "rights_refs":[e["receipt_id"] for e in matching],
            "max_characters":min((e["payload"].get("max_characters",0) for e in matching),default=0),
            "reason_codes":[] if approved else sorted(set(failures or ["field_rights_pending_or_denied"])),
            "authentication":"record_validation_not_human_or_legal_authentication"}
