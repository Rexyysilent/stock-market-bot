"""Exact structured-field support, immutable candidates and bounded batch helpers."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from ..identities import digest, instant, time_interval

VERSION = "omni-vertical-candidate-1"
MAX_ROWS = 5000


def nonempty(value, *, maximum=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and not any(ord(c) < 32 for c in value)


def decimal_string(value, *, signed=True):
    """Keep finite exact decimal spelling; binary floating-point input abstains."""
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite() or (not signed and number < 0):
        return None
    return value


def pointer_value(payload, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("structured proof needs a JSON pointer")
    current = payload
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9]\d*", part):
                raise ValueError("invalid array pointer")
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError("pointer traverses scalar")
    return current


def escape_pointer(name):
    return str(name).replace("~", "~0").replace("/", "~1")


def atom(payload, pointer, *, normalized=None):
    raw = pointer_value(payload, pointer)
    return {"value": deepcopy(raw) if normalized is None else normalized,
            "support": {"kind": "json_pointer", "snapshot_hash": digest(payload),
                        "pointer": pointer, "raw_value": deepcopy(raw)}}


def check_atom(payload, value):
    try:
        support = value["support"]
        return (support.get("kind") == "json_pointer" and support.get("snapshot_hash") == digest(payload)
                and support.get("raw_value") == pointer_value(payload, support["pointer"]))
    except (KeyError, IndexError, ValueError, TypeError):
        return False


def candidate(*, source_id, native_id, observed_at, source_payload, facts,
              source_policy_fingerprint, contract_hash, identity=None, times=None,
              lineage=None, reasons=(), evidence_kind="unverified_local_input"):
    instant(observed_at)
    if not all(nonempty(x) for x in (source_id, native_id, source_policy_fingerprint, contract_hash)):
        raise ValueError("candidate identity, policy and contract required")
    if evidence_kind not in ("synthetic", "unverified_local_input", "historical_replay", "prospective"):
        raise ValueError("unsupported evidence kind")
    row = {"contract": VERSION, "source_id": source_id, "native_id": native_id,
           "candidate_id": "vertical:" + digest([source_id, native_id]),
           "source_payload_hash": digest(source_payload), "source_policy_fingerprint": source_policy_fingerprint,
           "adapter_contract_hash": contract_hash, "facts": deepcopy(facts), "identity": identity or {},
           "times": times or {}, "lineage": lineage or {}, "reason_codes": sorted(set(reasons)),
           "evidence_kind": evidence_kind, "human_review_status": "pending", "signal_eligible": False}
    row["revision_id"] = digest(row)
    row["observed_at"] = observed_at
    row["retrieved_at"] = observed_at
    return row


def check_candidate(row):
    base = {k: v for k, v in row.items() if k not in ("revision_id", "observed_at", "retrieved_at")}
    return (row.get("contract") == VERSION and row.get("revision_id") == digest(base)
            and row.get("human_review_status") == "pending" and row.get("signal_eligible") is False
            and row.get("candidate_id") == "vertical:" + digest([row.get("source_id"), row.get("native_id")])
            and instant(row["observed_at"]) <= instant(row["retrieved_at"]))


def revision_transition(previous, current):
    if not check_candidate(previous) or not check_candidate(current) or previous["candidate_id"] != current["candidate_id"]:
        raise ValueError("incompatible vertical revisions")
    changed = previous["revision_id"] != current["revision_id"]
    return {"changed": changed, "append_revision": changed, "old_observation_preserved": True,
            "re_review_required": changed, "previous_revision": previous["revision_id"],
            "current_revision": current["revision_id"], "withdrawal_inferred": False}


def batch(source_id, *, rows=(), gaps=(), continuation=None, query_hash=None,
          request_counts=None, index_set_complete=False, page_receipts=()):
    return {"contract": "omni-vertical-batch-1", "source_id": source_id,
            "rows": list(rows), "gaps": sorted(set(gaps)), "continuation": continuation,
            "query_hash": query_hash, "request_counts": dict(request_counts or {}),
            "page_receipts": list(page_receipts), "acquired_index_set_complete": index_set_complete is True,
            "feed_interval_complete": False, "correction_surveillance_complete": False,
            "complete": False, "automatic_activation": False, "signal_eligible": False}


def disabled_batch(source_id, *, reason="adapter_disabled", query_hash=None):
    return batch(source_id, gaps=[reason], query_hash=query_hash,
                 request_counts={"attempt": 0, "metadata": 0, "document": 0})


def checkpoint(query_hash, offset, seen_ids, *, page_hashes=()):
    return {"contract": "omni-vertical-checkpoint-1", "query_hash": query_hash, "offset": offset,
            "seen_ids": sorted(set(seen_ids)), "page_hashes": sorted(set(page_hashes))}


def resume_checkpoint(value, query_hash):
    if value is None:
        return 0, set(), set()
    if (not isinstance(value, dict) or value.get("contract") != "omni-vertical-checkpoint-1"
            or value.get("query_hash") != query_hash or type(value.get("offset")) is not int
            or value["offset"] < 0 or not isinstance(value.get("seen_ids"), list)
            or len(value["seen_ids"]) > 60000 or any(not nonempty(x) for x in value["seen_ids"])
            or not isinstance(value.get("page_hashes"), list) or len(value["page_hashes"]) > 1000):
        raise ValueError("checkpoint_query_or_shape_mismatch")
    return value["offset"], set(value["seen_ids"]), set(value["page_hashes"])


def source_time(raw):
    interval = time_interval(raw)
    return {**interval, "timezone_basis": "explicit_source_offset" if interval["precision"] == "timestamp" else None,
            "timezone": "explicit" if interval["precision"] == "timestamp" else "unknown",
            "normalized_instant": interval["start"] if interval["precision"] == "timestamp" else None}


def mapping_visibility(mapping, *, known_by, on):
    """A source's effective date never backdates when a crosswalk was learned."""
    try:
        known = instant(mapping["known_from"])
        cutoff = instant(known_by)
        date.fromisoformat(on)
        low, high = mapping.get("valid_from"), mapping.get("valid_to")
        for value in (low, high):
            if value is not None:
                date.fromisoformat(value)
        if low is not None and high is not None and low >= high:
            raise ValueError("invalid mapping interval")
        if known > cutoff:
            return {"visible": False, "reason": "mapping_not_yet_known"}
        if (low is not None and on < low) or (high is not None and on >= high):
            return {"visible": False, "reason": "mapping_not_valid_on_date"}
        return {"visible": True, "reason": None}
    except (KeyError, ValueError, TypeError):
        return {"visible": False, "reason": "mapping_time_unverified"}


def validate_crosswalk(mapping, *, snapshots, registry, known_by, on):
    """Validate an explicit edge; never name-match, infer a parent or approve it."""
    required = ("from_id", "to_id", "role", "source_native_id", "source_vintage", "known_from")
    reasons = []
    if any(not nonempty(mapping.get(k)) for k in required):
        reasons.append("mapping_fields_missing")
    if mapping.get("role") not in {"operates", "directly_owns", "applicant_for", "licensee_for", "contractor_for", "parent_of", "unit_of", "crosswalks_to"}:
        reasons.append("mapping_role_unknown")
    if mapping.get("from_id") not in registry.entities or mapping.get("to_id") not in registry.entities:
        reasons.append("mapping_entity_unresolved")
    visible = mapping_visibility(mapping, known_by=known_by, on=on)
    if not visible["visible"]:
        reasons.append(visible["reason"])
    proofs = mapping.get("proofs", {})
    for name in (*required, *(k for k in ("valid_from", "valid_to") if mapping.get(k) is not None)):
        proof = proofs.get(name) if isinstance(proofs, dict) else None
        snapshot = snapshots.get(proof.get("support", {}).get("snapshot_hash")) if isinstance(proof, dict) else None
        if snapshot is None or not check_atom(snapshot, proof) or proof.get("value") != mapping.get(name):
            reasons.append("mapping_proof_missing:" + name)
    stake = mapping.get("direct_stake_percent")
    if stake is not None:
        parsed = decimal_string(stake, signed=False)
        if parsed is None or Decimal(parsed) > 100 or mapping.get("role") != "directly_owns":
            reasons.append("invalid_direct_stake")
        proof = proofs.get("direct_stake_percent", {})
        snapshot = snapshots.get(proof.get("support", {}).get("snapshot_hash"))
        if snapshot is None or not check_atom(snapshot, proof) or proof.get("value") != stake:
            reasons.append("mapping_proof_missing:direct_stake_percent")
    # A structurally valid mapping is still a candidate for an operator's review.
    return {"structurally_valid": not reasons, "mapping_visible": visible["visible"] and not reasons,
            "reason_codes": sorted(set(reasons)), "human_review_status": "pending", "signal_eligible": False}


def checked_stakes(stakes):
    parsed = [decimal_string(x, signed=False) for x in stakes]
    valid = bool(stakes) and all(x is not None and Decimal(x) <= 100 for x in parsed)
    if valid and sum(Decimal(x) for x in parsed) > 100:
        valid = False
    return {"accepted": valid, "reason": None if valid else "overlapping_or_inconsistent_stakes",
            "residual_owner_created": False}


def origin_count(paths, *, reviewed_correspondence=(), snapshots=None, store=None, now=None, allow_synthetic=False):
    """Count known assertion origins only through exact, reviewed lineage edges.

    A truthy proof, approval flag, URL or source name cannot merge two paths.
    Structural facts and a version-bound typed operator attestation are checked
    separately; even a valid merge is not a finding of source independence.
    """
    nodes = {p["path_id"]: p for p in paths}
    parents = {x: x for x in nodes}
    def find(x):
        while parents[x] != x:
            x = parents[x]
        return x
    rejected = []
    for edge in reviewed_correspondence:
        left, right = edge.get("from_path"), edge.get("to_path")
        if (left not in nodes or right not in nodes or edge.get("relation") not in ("same_release", "republishes", "derived_from")
                or not nonempty(edge.get("receipt_id"))
                or edge.get("from_revision") != nodes[left].get("revision_id")
                or edge.get("to_revision") != nodes[right].get("revision_id")
                or edge.get("unit_id") != nodes[left].get("unit_id") or edge.get("unit_id") != nodes[right].get("unit_id")
                or edge.get("period") != nodes[left].get("period") or edge.get("period") != nodes[right].get("period")
                or not isinstance(edge.get("proofs"), dict) or store is None or now is None):
            rejected.append("unverified_lineage")
            continue
        supported = True
        for field in ("from_revision", "to_revision", "relation", "unit_id", "period"):
            proof = edge["proofs"].get(field)
            snapshot = (snapshots or {}).get(proof.get("support", {}).get("snapshot_hash")) if isinstance(proof, dict) else None
            if snapshot is None or not check_atom(snapshot, proof) or proof.get("value") != edge.get(field):
                supported = False
        if not supported:
            rejected.append("unverified_lineage")
            continue
        from ..attestations import validate_attestation
        subject = {k: v for k, v in edge.items() if k != "receipt_id"}
        try:
            validate_attestation(store, edge["receipt_id"], purpose="vertical_origin_correspondence",
                                 subject_hash=digest(subject), bindings={"adapter_version": VERSION,
                                 "from_revision": edge["from_revision"], "to_revision": edge["to_revision"]},
                                 now=now, allow_synthetic=allow_synthetic)
        except (ValueError, KeyError, TypeError):
            rejected.append("unverified_lineage_review")
            continue
        parents[find(right)] = find(left)
    return {"assertion_origins": len({find(x) for x in nodes}), "reasons": sorted(set(rejected)),
            "independence_established": False, "human_review_status": "pending"}


def deferred_lead(candidate_id, reason):
    """Persistable backlog intent; absence of hydration is not absence of news."""
    if not nonempty(candidate_id) or not nonempty(reason):
        raise ValueError("invalid deferred lead")
    return {"candidate_id": candidate_id, "candidate_retained": True, "backlog_visible": True,
            "hydration": "pending", "reason": "budget_deferred" if reason.startswith("budget_deferred") else reason,
            "human_review_status": "pending", "signal_eligible": False}


def persist_batch(store, result, *, observed_at, refs=()):
    """Append immutable draft receipts without enabling events or source policy."""
    from ..receipts import put_receipt, transaction
    rows = result.get("rows")
    if not isinstance(rows, list) or any(not check_candidate(row) for row in rows):
        raise ValueError("invalid vertical candidate batch")
    with transaction(store):
        row_refs = []
        for row in rows:
            receipt = put_receipt(store, "vertical_candidate", row, observed_at=observed_at, refs=refs,
                                  manage_transaction=False)
            row_refs.append(receipt["receipt_id"])
        summary = {k: v for k, v in result.items() if k != "rows"}
        summary["candidate_refs"] = row_refs
        return put_receipt(store, "vertical_batch", summary, observed_at=observed_at,
                           refs=list(dict.fromkeys([*refs, *row_refs])), manage_transaction=False)


def load_local_json(path):
    from pathlib import Path
    raw = Path(path).read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError("input exceeds 5 MB")
    return json.loads(raw, parse_float=str, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
