"""Reviewed source-specific retention and bounded polling-interval evidence.

Polling cannot prove that a record never appeared and disappeared between polls.
An actual source retention/change-log contract is a separate supplied attestation,
bound to the source/query/window. No boolean can replace that evidence.
"""
from __future__ import annotations

import json
import re

from .identities import digest, instant
from .receipts import get_receipt, list_receipts, put_receipt
from .prospective import (
    VERSION, TRUST_BOUNDARY, _bindings, _coverage_check, _integer, _review,
    _scope, _text, protocol_state, transaction)

RETENTION_KIND = "prospective_source_retention"
INTERVAL_KIND = "prospective_interval_verification"


def _append(store, kind, payload, *, now, refs):
    receipt_id = "prospective-interval:" + digest([kind, payload])
    try:
        previous = get_receipt(store, receipt_id, kind=kind)
    except (ValueError, KeyError):
        previous = None
    if previous:
        return previous
    return put_receipt(store, kind, payload, observed_at=now, refs=sorted(set(refs)),
                       receipt_id=receipt_id, manage_transaction=False)


def retention_request(store, protocol_id, registry, policies, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    scope = _scope(protocol, record)
    start, end = instant(record["valid_from"]), instant(record["valid_until"])
    if start >= end:
        raise ValueError("source retention validity interval is reversed")
    visibility = record.get("record_visibility")
    if visibility not in ("immutable_feed", "append_only_source_index", "retained_change_log"):
        raise ValueError("explicit source retention/change-log contract required")
    external = record.get("external_reference")
    if (not isinstance(external, dict) or not external.get("reference")
            or not re.fullmatch(r"[a-f0-9]{64}", str(external.get("sha256", "")))):
        raise ValueError("exact source retention contract reference and hash required")
    return {"contract": VERSION, "protocol_id": protocol_id,
            **{k: scope[k] for k in ("source_id", "route_id", "family", "query_hash")},
            "valid_from": record["valid_from"], "valid_until": record["valid_until"],
            "minimum_retention_seconds": _integer(record.get("minimum_retention_seconds"),
                                                   "source minimum retention", high=366 * 86400),
            "record_visibility": visibility,
            "external_reference": {"reference": _text(external["reference"], "retention reference", 2000),
                                   "sha256": external["sha256"]},
            "evidence_kind": protocol["evidence_kind"], "trust_boundary": TRUST_BOUNDARY,
            "continuous_source": False, **_bindings(protocol)}


def import_retention(store, protocol_id, registry, policies, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    subject = retention_request(store, protocol_id, registry, policies, record, now=now)
    _review(store, record["review_receipt_id"], purpose="source_retention_contract",
            subject=subject, protocol=protocol, now=now)
    with transaction(store):
        return _append(store, RETENTION_KIND, dict(subject, review_receipt_id=record["review_receipt_id"]),
                       now=now, refs=[protocol_id, record["review_receipt_id"]])


def _retention(store, receipt_id, protocol, *, now):
    receipt = get_receipt(store, receipt_id, kind=RETENTION_KIND)
    if instant(receipt["observed_at"]) > instant(now):
        raise ValueError("source retention receipt was imported after the report clock")
    payload = receipt["payload"]
    if (payload.get("protocol_id") != protocol["protocol_id"]
            or any(payload.get(k) != v for k, v in _bindings(protocol).items())):
        raise ValueError("source retention protocol/version mismatch")
    subject = {k: v for k, v in payload.items() if k != "review_receipt_id"}
    _review(store, payload["review_receipt_id"], purpose="source_retention_contract",
            subject=subject, protocol=protocol, now=now)
    return payload


def _interval_subject(store, protocol, record, *, now):
    row, _, issues = _coverage_check(store, protocol, record["coverage_receipt_id"],
                                     now=now, require_interval=False)
    scope = _scope(protocol, row)
    if scope["coverage_kind"] != "feed_interval":
        raise ValueError("retention-interval proof applies to a feed polling interval")
    retention = _retention(store, record["retention_receipt_id"], protocol, now=now)
    if any(retention.get(k) != scope[k] for k in ("source_id", "route_id", "family", "query_hash")):
        raise ValueError("source retention contract scope/query mismatch")
    start, end = instant(row["window_start"]), instant(row["window_end"])
    if not instant(retention["valid_from"]) <= start < end <= instant(retention["valid_until"]):
        issues.append("retention_contract_does_not_cover_interval")
    clocks = sorted({start, end} | {instant(get_receipt(store, ref)["payload"]["acquired_at"])
                                   for ref in row["observation_refs"]})
    gap = max(((b - a).total_seconds() for a, b in zip(clocks, clocks[1:])), default=None)
    if gap is None or gap > retention["minimum_retention_seconds"]:
        issues.append("source_retention_shorter_than_poll_gap")
    if gap is None or gap > protocol["poll_interval_seconds"] + 2 * protocol["max_clock_uncertainty_seconds"]:
        issues.append("observed_poll_gap_exceeds_frozen_cadence")
    cycle_proofs = []
    for cycle_id in row["cycle_ids"]:
        cycle = store.conn.execute("SELECT input_hash,health FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
        cycle_proofs.append({"cycle_id": cycle_id, "cycle_input_hash": cycle["input_hash"],
                             "traversal_hash": digest(json.loads(cycle["health"]).get("coverage_proof"))})
    return {"contract": VERSION, "protocol_id": protocol["protocol_id"],
            "coverage_receipt_id": record["coverage_receipt_id"],
            "retention_receipt_id": record["retention_receipt_id"],
            **{k: scope[k] for k in ("source_id", "route_id", "family", "query_hash")},
            "window_start": row["window_start"], "window_end": row["window_end"],
            "native_inventory_hash": row["native_inventory_hash"],
            "observation_refs": row["observation_refs"], "cycle_proofs": cycle_proofs,
            "maximum_observed_poll_gap_seconds": gap,
            "reason_codes": sorted(set(issues)), "continuous_source": False,
            "claim": "bounded polling coverage under the separately reviewed source retention contract",
            "evidence_kind": protocol["evidence_kind"], "trust_boundary": TRUST_BOUNDARY,
            **_bindings(protocol)}


def interval_request(store, protocol_id, registry, policies, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    return _interval_subject(store, protocol, record, now=now)


def import_interval(store, protocol_id, registry, policies, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    subject = _interval_subject(store, protocol, record, now=now)
    if subject["reason_codes"]:
        raise ValueError("source interval cannot be verified: " + ", ".join(subject["reason_codes"]))
    _review(store, record["review_receipt_id"], purpose="source_interval_coverage",
            subject=subject, protocol=protocol, now=now)
    with transaction(store):
        return _append(store, INTERVAL_KIND, dict(subject, review_receipt_id=record["review_receipt_id"]),
                       now=now, refs=[protocol_id, subject["coverage_receipt_id"],
                                      subject["retention_receipt_id"], record["review_receipt_id"]])


def verified_interval(store, protocol, coverage_receipt_id, *, now):
    """Current proof, with expiry/revocation rechecked; historical rows stay."""
    candidates = [r for r in list_receipts(store, kind=INTERVAL_KIND)
                  if r["payload"].get("protocol_id") == protocol["protocol_id"]
                  and r["payload"].get("coverage_receipt_id") == coverage_receipt_id
                  and instant(r["observed_at"]) <= instant(now)]
    for receipt in reversed(candidates):
        payload = receipt["payload"]
        try:
            subject = _interval_subject(store, protocol, payload, now=now)
            if subject["reason_codes"] or subject != {k: v for k, v in payload.items() if k != "review_receipt_id"}:
                continue
            _review(store, payload["review_receipt_id"], purpose="source_interval_coverage",
                    subject=subject, protocol=protocol, now=now)
        except (ValueError, KeyError):
            continue
        return receipt
    raise ValueError("source interval/retention attestation missing, stale or revoked")
