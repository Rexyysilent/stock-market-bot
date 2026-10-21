"""Typed operator evidence. Validation does not authenticate a human or legal approval."""
from __future__ import annotations

import re

from .identities import instant
from .receipts import get_receipt, list_receipts

VERSION = "omni-operator-attestation-1"


def validate_attestation(store, receipt_id, *, purpose, subject_hash, bindings, now, allow_synthetic=False):
    envelope = get_receipt(store, receipt_id, kind="operator_attestation")
    record = envelope["payload"]
    if record.get("schema") != VERSION or record.get("purpose") != purpose:
        raise ValueError("attestation purpose or schema mismatch")
    if record.get("subject_hash") != subject_hash or record.get("decision") != "approved":
        raise ValueError("attestation is not approval of this exact subject")
    if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
        raise ValueError("attestation reviewer missing")
    if record.get("evidence_kind") not in ({"operator_evidence", "synthetic"} if allow_synthetic else {"operator_evidence"}):
        raise ValueError("real operator evidence required")
    reference = record.get("external_reference")
    if (not isinstance(reference, dict) or not isinstance(reference.get("reference"), str)
            or not reference["reference"].strip() or not re.fullmatch(r"[a-f0-9]{64}", str(reference.get("sha256", "")))):
        raise ValueError("external attestation reference/hash missing")
    if not bindings or any(record.get(k) != v or not v for k, v in bindings.items()):
        raise ValueError("attestation version or scope mismatch")
    clock, reviewed, observed = instant(now), instant(record.get("reviewed_at")), instant(envelope["observed_at"])
    if reviewed > observed or observed > clock:
        raise ValueError("attestation time ordering invalid")
    if record.get("expires_at") and instant(record["expires_at"]) <= clock:
        raise ValueError("attestation expired")
    for revoke in list_receipts(store, kind="attestation_revocation"):
        if revoke["payload"].get("attestation_ref") == receipt_id and instant(revoke["observed_at"]) <= clock:
            raise ValueError("attestation revoked")
    return envelope
