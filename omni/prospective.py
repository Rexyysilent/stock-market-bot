"""Source-first, future-window evaluation with immutable, referenced receipts.

This module records supplied evidence; it neither creates human decisions nor
authenticates external people. Synthetic workflows remain useful offline tests
and contribute zero observations to a real release gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import re

from .attestations import validate_attestation
from .coverage import cursor_proof, get_coverage
from .identities import ROOT, digest, instant
from .lifecycle import resolve_origin
from .pilot import implementation_hash
from .receipts import get_receipt, list_receipts, put_receipt
from .review import event_packet

VERSION = "omni-prospective-1"
PARTITIONS = ("development", "calibration", "prospective", "untouched")
KINDS = {name: "prospective_" + name for name in (
    "protocol", "coverage", "enumeration", "unit_mapping", "closure_attempt",
    "inventory", "adjudication", "run", "legacy_history", "evaluation")}
MAX_ROWS = 10000
TRUST_BOUNDARY = (
    "Receipt hashes validate local record consistency and supplied attestations; "
    "they do not authenticate humans, establish source rights, or prove that "
    "an external person never inspected held-out data."
)


@contextmanager
def transaction(store):
    """One transaction for a public operation, without committing an outer one."""
    owned = not store.conn.in_transaction
    if owned:
        store.conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        if owned:
            store.conn.commit()
    except BaseException:
        if owned:
            store.conn.rollback()
        raise


def _text(value, name, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(name + " must be a bounded nonempty string")
    return value


def _integer(value, name, low=1, high=MAX_ROWS):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(name + " is outside its integer bounds")
    return value


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(name + " must be an exact SHA256 hash")
    return value


def _unique(values, name, *, empty=False):
    if (not isinstance(values, list) or (not values and not empty)
            or len(values) > MAX_ROWS):
        raise ValueError(name + " must be a bounded list")
    for value in values:
        _text(value, name)
    if len(values) != len(set(values)):
        raise ValueError(name + " contains duplicates")
    return values


def native_reference(value):
    if not isinstance(value, dict):
        raise ValueError("exact native reference required")
    return {key: _text(value.get(key), key) for key in
            ("source_id", "native_id", "revision_id")}


def current_bindings(registry, policies):
    locks = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
             for name in ("requirements.lock", "requirements-ci.lock")}
    return {"implementation_hash": implementation_hash(),
            "policy_hash": digest(policies),
            "source_scope_hash": digest(policies),
            "dependency_locks_hash": digest(locks),
            "registry_fingerprint": registry.fingerprint()}


def _bindings(protocol):
    return {key: protocol[key] for key in (
        "implementation_hash", "policy_hash", "source_scope_hash",
        "registry_fingerprint", "dependency_locks_hash")}


def _origin(store, reference, protocol, *, now):
    return resolve_origin(store, reference, at=now, policy_hash=protocol["policy_hash"],
                          allow_synthetic=protocol["evidence_kind"] == "synthetic")


def _append(store, kind, payload, *, now, refs=()):
    """Content-addressed replay keeps the first import time and immutable bytes."""
    receipt_id = "prospective:" + kind + ":" + digest(payload)
    try:
        previous = get_receipt(store, receipt_id, kind=KINDS[kind])
    except (KeyError, ValueError):
        previous = None
    if previous is not None:
        if previous["payload"] != payload:
            raise ValueError("immutable prospective receipt conflict")
        return previous
    return put_receipt(store, KINDS[kind], payload, observed_at=now,
                       refs=sorted(set(refs)), receipt_id=receipt_id,
                       manage_transaction=False)


def _records(store, protocol_id, kind, *, at=None):
    return [r for r in list_receipts(store, kind=KINDS[kind])
            if r["payload"].get("protocol_id") == protocol_id
            and (at is None or instant(r["observed_at"]) <= instant(at))]


def _review(store, receipt_id, *, purpose, subject, protocol, now):
    return validate_attestation(
        store, receipt_id, purpose=purpose, subject_hash=digest(subject),
        bindings=_bindings(protocol), now=now,
        allow_synthetic=protocol["evidence_kind"] == "synthetic")


def protocol_state(store, protocol_id, registry=None, policies=None,
                   *, require_current=True):
    receipt = get_receipt(store, protocol_id, kind=KINDS["protocol"])
    protocol = receipt["payload"]
    if protocol.get("contract") != VERSION:
        raise ValueError("unsupported prospective protocol")
    expected_id = "prospective:protocol:" + digest({k: v for k, v in protocol.items() if k != "protocol_id"})
    if protocol.get("protocol_id") != protocol_id or protocol_id != expected_id:
        raise ValueError("prospective protocol identity mismatch")
    if instant(receipt["observed_at"]) != instant(protocol["frozen_at"]):
        raise ValueError("protocol freeze must equal its immutable recording clock")
    if not instant(protocol["requested_at"]) <= instant(protocol["frozen_at"]) < instant(protocol["window_start"]) < instant(protocol["window_end"]):
        raise ValueError("recorded protocol request/freeze/window ordering invalid")
    if registry is not None and policies is not None:
        current = current_bindings(registry, policies)
        matches = all(protocol.get(k) == v for k, v in current.items())
        if require_current and not matches:
            raise ValueError("implementation, registry or source policy changed")
    return protocol


def protocol_request(registry, policies, coverage, spec, *, now):
    """Normalize a request before an operator supplies its review attestation."""
    clock = instant(now)
    start, end = instant(spec["window_start"]), instant(spec["window_end"])
    if not clock < start < end:
        raise ValueError("sampling window must be entirely future at freeze")
    if end - start > timedelta(days=366):
        raise ValueError("prospective window exceeds one year")
    partition = spec.get("partition")
    if partition not in PARTITIONS:
        raise ValueError("explicit evaluation partition required")
    evidence_kind = spec.get("evidence_kind")
    if evidence_kind not in ("synthetic", "operator_evidence"):
        raise ValueError("templates and assumptions cannot freeze a protocol")
    _unique(coverage, "captured coverage", empty=True)
    scopes = spec.get("source_scope")
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 64:
        raise ValueError("explicit source/route/family strata required")
    normalized = []
    for raw in scopes:
        row = {key: _text(raw.get(key), key) for key in
               ("source_id", "route_id", "family", "enumeration_contract", "query_hash")}
        _hash(row["query_hash"], "query-plan hash")
        if row["source_id"] not in policies:
            raise ValueError("sampling source has no pinned policy")
        if evidence_kind == "operator_evidence" and any(
                policies[row["source_id"]].get("uses", {}).get(purpose) is not True
                for purpose in ("acquire", "store_text", "local_analysis", "review_display")):
            raise ValueError("real evaluation source-purpose policy remains unapproved")
        if raw.get("coverage_kind") not in ("feed_interval", "acquired_index_set"):
            raise ValueError("sampling frame must distinguish feed from index set")
        if type(raw.get("timing_required", True)) is not bool:
            raise ValueError("timing_required must be boolean")
        row.update(coverage_kind=raw["coverage_kind"],
                   timing_required=raw.get("timing_required", True),
                   source_policy_hash=digest(policies[row["source_id"]]),
                   source_class=policies[row["source_id"]].get("source_class", "unknown"))
        normalized.append(row)
    keys = [(r["source_id"], r["route_id"], r["family"]) for r in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError("repeated source/route/family stratum")
    sampling = spec.get("sampling", {})
    if sampling.get("method") == "census":
        sampling = {"method": "census", "seed": None}
    elif sampling.get("method") == "hash_modulus":
        seed = _text(sampling.get("seed"), "sampling seed")
        modulus = _integer(sampling.get("modulus"), "sampling modulus", high=1000000)
        buckets = sampling.get("buckets")
        if (not isinstance(buckets, list) or not buckets
                or any(type(b) is not int or not 0 <= b < modulus for b in buckets)
                or len(buckets) != len(set(buckets))):
            raise ValueError("sampling buckets must be distinct and in range")
        sampling = {"method": "hash_modulus", "seed": seed,
                    "modulus": modulus, "buckets": sorted(buckets)}
    else:
        raise ValueError("only prespecified census or deterministic hash sampling supported")
    if spec.get("event_unit_rule") != "reviewed_assertion_origin":
        raise ValueError("source-first assertion-origin event units required")
    custodians = _unique(spec.get("custodians", []), "custodians", empty=partition != "untouched")
    poll = _integer(spec.get("poll_interval_seconds", 900), "poll interval", high=900)
    limits = {
        "minimum_timing_per_stratum": _integer(spec.get("minimum_timing_per_stratum", 30), "timing minimum",
                                               low=30 if evidence_kind == "operator_evidence" else 1),
        "minimum_unknown_issuer_gold": _integer(spec.get("minimum_unknown_issuer_gold", 30), "unknown issuer minimum",
                                                low=30 if evidence_kind == "operator_evidence" else 1),
        "minimum_negative_outcome_gold": _integer(spec.get("minimum_negative_outcome_gold", 1), "negative outcome minimum")}
    covered = set()
    unresolved_coverage = []
    for symbol in coverage:
        resolution = registry.resolve("NASDAQ:" + symbol + " NYSE:" + symbol, known_by=now)
        ids = {m["entity_id"] for m in resolution["mentions"] if m["kind"] == "issuer"}
        if len(ids) != 1:
            unresolved_coverage.append(symbol)
        covered.update(ids)
    from .classifier import VERSION as classifier_version
    from .validator import VERSION as validator_version
    from .evaluation import VERSION as evaluation_version
    from .evidence import VERSION as evidence_version
    result = {
        "contract": VERSION, "name": _text(spec.get("name"), "protocol name"),
        "partition": partition, "window_start": spec["window_start"],
        "window_end": spec["window_end"], "requested_at": now,
        "sampling": sampling, "event_unit_rule": "reviewed_assertion_origin",
        "precision_stream_rule": "all_scoped_classifier_event_units",
        "precision_sampling": "census_including_withheld_and_incorrect_proposals",
        "source_scope": sorted(normalized, key=lambda r: tuple(r[k] for k in ("source_id", "route_id", "family"))),
        "poll_interval_seconds": poll, "max_clock_uncertainty_seconds": 1,
        "session_timezone": "America/New_York", "session_rule": "local_run_start_date",
        "captured_coverage": list(coverage), "coverage_fingerprint": digest(coverage),
        "covered_issuer_ids": sorted(covered), "unresolved_coverage": sorted(unresolved_coverage),
        "custodians": custodians, "evidence_kind": evidence_kind,
        "scoring_rubric_hash": _hash(spec.get("scoring_rubric_hash"), "scoring rubric hash"),
        "exclusion_rules_hash": _hash(spec.get("exclusion_rules_hash"), "exclusion rules hash"),
        "stop_rule": _text(spec.get("stop_rule"), "prespecified stop/extension rule", 2000),
        "automatic_activation": False, "human_authentication": TRUST_BOUNDARY,
        "contract_versions": {"classifier": classifier_version, "validator": validator_version,
                              "evaluation": evaluation_version, "evidence": evidence_version},
        "dependency_locks": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                             for name in ("requirements.lock", "requirements-ci.lock")},
        **limits, **current_bindings(registry, policies)}
    return result


def freeze_protocol(store, registry, policies, coverage, spec, *, now):
    """Freeze future rules, never a list of classifier-discovered future cases."""
    if any(key in spec for key in ("event_ids", "gold_event_ids", "classifier_events")):
        raise ValueError("future inventory cannot be defined by classifier discoveries")
    review_id = _text(spec.get("review_receipt_id"), "protocol review receipt")
    prior = [r for r in list_receipts(store, kind=KINDS["protocol"])
             if r["payload"].get("review_receipt_id") == review_id]
    requested_at = spec.get("requested_at", prior[0]["payload"]["requested_at"] if prior else now)
    request = protocol_request(registry, policies, coverage, spec, now=requested_at)
    for previous in prior:
        if request == {k: v for k, v in previous["payload"].items()
                       if k not in ("protocol_id", "review_receipt_id", "frozen_at")}:
            return previous
    if not instant(requested_at) <= instant(now) < instant(spec["window_start"]):
        raise ValueError("actual freeze must follow its request and precede the future window")
    _review(store, review_id, purpose="prospective_protocol", subject=request,
            protocol=request, now=now)
    payload = dict(request, frozen_at=now, review_receipt_id=review_id)
    protocol_id = "prospective:protocol:" + digest(payload)
    payload["protocol_id"] = protocol_id
    with transaction(store):
        try:
            existing = get_receipt(store, protocol_id, kind=KINDS["protocol"])
        except (KeyError, ValueError):
            existing = None
        if existing:
            if existing["payload"] != payload:
                raise ValueError("protocol identity conflict")
            return existing
        return put_receipt(store, KINDS["protocol"], payload, observed_at=now,
                           refs=[review_id], receipt_id=protocol_id,
                           manage_transaction=False)


def _scope(protocol, coverage):
    found = [s for s in protocol["source_scope"] if all(
        s[k] == coverage.get(k) for k in ("source_id", "route_id", "family"))]
    if len(found) != 1:
        raise ValueError("coverage is outside the frozen source/route/family scope")
    return found[0]


def _coverage_check(store, protocol, receipt_id, *, now, require_interval=True):
    envelope = get_receipt(store, receipt_id)
    if instant(envelope["observed_at"]) > instant(now):
        raise ValueError("coverage receipt was imported after the report clock")
    value = get_coverage(store, receipt_id)
    row = dict(value.get("payload", value))
    row.setdefault("receipt_id", receipt_id)
    scope = _scope(protocol, row)
    reasons = []
    start, end = instant(row["window_start"]), instant(row["window_end"])
    if not instant(protocol["window_start"]) <= start < end <= instant(protocol["window_end"]):
        raise ValueError("coverage window is outside frozen sampling window")
    if end > instant(now):
        raise ValueError("future coverage cannot close an interval")
    if row.get("kind") != scope["coverage_kind"]:
        reasons.append("coverage_frame_mismatch")
    if row.get("status") != "complete" or row.get("gaps"):
        reasons.append("partial_source_coverage")
    if row.get("implementation_hash") != protocol["implementation_hash"]:
        reasons.append("coverage_implementation_mismatch")
    if row.get("policy_hash") not in (protocol["policy_hash"], scope["source_policy_hash"]):
        reasons.append("coverage_policy_mismatch")
    cycles = row.get("cycle_ids")
    if not isinstance(cycles, list) or not cycles or len(cycles) != len(set(cycles)):
        reasons.append("source_cycles_missing_or_duplicated")
        cycles = []
    traversal_pages = []
    for cycle_id in cycles:
        cycle = store.conn.execute(
            "SELECT source_id,observed_at,health FROM source_cycles WHERE cycle_id=?",
            (cycle_id,)).fetchone()
        if not cycle:
            reasons.append("unknown_source_cycle")
            continue
        if cycle[0] != scope["source_id"]:
            reasons.append("source_cycle_scope_mismatch")
        if not start <= instant(cycle[1]) <= end:
            reasons.append("source_cycle_outside_coverage")
        health = json.loads(cycle[2])
        if health.get("status") != "complete" or health.get("gaps"):
            reasons.append("source_cycle_incomplete")
        if protocol["evidence_kind"] == "operator_evidence" and scope["coverage_kind"] == "feed_interval":
            proof = health.get("coverage_proof")
            valid = isinstance(proof, dict) and cursor_proof(
                proof, source_id=scope["source_id"], old_watermark=proof.get("previous_watermark"),
                watermark=health.get("watermark_candidate"), observed_at=cycle[1])
            if not valid:
                reasons.append("actual_feed_traversal_proof_missing")
            else:
                traversal_pages.extend(proof["pages"])
    inventory = row.get("native_inventory")
    if not isinstance(inventory, list) or len(inventory) > MAX_ROWS:
        reasons.append("native_inventory_membership_missing")
        inventory = []
    native = []
    for ref in inventory:
        canonical = native_reference(ref)
        if canonical["source_id"] != scope["source_id"]:
            reasons.append("native_inventory_source_mismatch")
        native.append(canonical)
    if len(native) != len({digest(r) for r in native}):
        reasons.append("native_inventory_duplicates")
    if row.get("native_inventory_hash") != digest(native):
        reasons.append("native_inventory_hash_mismatch")
    if type(row.get("received_count")) is not int or row.get("received_count") != len(native):
        reasons.append("native_inventory_count_mismatch")
    if (type(row.get("reported_count")) is not int
            or row.get("reported_count") != len(native)):
        reasons.append("reported_count_unknown_or_incomplete")
    observations = []
    for ref in row.get("observation_refs", []):
        envelope = get_receipt(store, ref, kind="native_inventory_observation")
        observed = envelope["payload"]
        observations.append(observed)
        if not instant(observed["acquired_at"]) <= instant(envelope["observed_at"]) <= instant(now):
            reasons.append("source_observation_import_clock_invalid")
        if observed.get("evidence_kind") == "operator_evidence":
            try:
                validate_attestation(
                    store, observed.get("attestation_ref"), purpose="source_inventory",
                    subject_hash=digest({k: v for k, v in observed.items() if k != "attestation_ref"}),
                    bindings={k: observed[k] for k in ("implementation_hash", "policy_hash")}, now=now)
            except (ValueError, KeyError):
                reasons.append("source_inventory_attestation_stale_or_revoked")
        if observed.get("contract") != scope["enumeration_contract"]:
            reasons.append("enumeration_contract_mismatch")
        if observed.get("query_hash") != scope["query_hash"]:
            reasons.append("enumeration_query_mismatch")
        if protocol["evidence_kind"] == "operator_evidence" and scope["coverage_kind"] == "feed_interval":
            matching = [page for page in traversal_pages if page["raw_sha256"] == observed["raw_response_hash"]]
            native_ids = {r["native_id"] for r in observed["native_inventory"]}
            if not matching or not any(native_ids == set(page["native_ids"]) for page in matching):
                reasons.append("source_observation_not_bound_to_feed_traversal")
        if not re.fullmatch(r"[a-f0-9]{64}", str(observed.get("raw_response_hash", ""))):
            reasons.append("source_snapshot_hash_missing")
        if (observed.get("truncated") is not False or observed.get("capped") is not False
                or observed.get("response_complete") is not True
                or observed.get("end_of_results") is not True
                or observed.get("continuation") is not None):
            reasons.append("source_observation_capped_or_incomplete")
    if not observations:
        reasons.append("source_snapshot_proof_missing")
    kinds = {o.get("evidence_kind") for o in observations}
    if "synthetic" in kinds:
        row["evidence_kind"] = "synthetic"
    elif kinds and kinds <= {"operator_evidence", "collector_observation"}:
        row["evidence_kind"] = "operator_evidence"
    else:
        row["evidence_kind"] = "unverified"
        reasons.append("coverage_evidence_kind_unverified")
    if protocol["evidence_kind"] == "operator_evidence" and row.get("evidence_kind") != "operator_evidence":
        reasons.append("coverage_not_operator_evidence")
    if require_interval and protocol["evidence_kind"] == "operator_evidence" and scope["coverage_kind"] == "feed_interval":
        from .intervals import verified_interval
        try:
            interval = verified_interval(store, protocol, receipt_id, now=now)
            row["source_interval_verification_receipt_id"] = interval["receipt_id"]
        except ValueError:
            reasons.append("source_interval_retention_attestation_missing_or_stale")
    return row, native, sorted(set(reasons))


def _native_first_observed(store, reference, protocol, *, now):
    """Earliest retained observation; never inferred historical availability."""
    clocks = []
    evidence_id = "evidence:" + digest([reference["source_id"], reference["native_id"]])
    row = store.conn.execute(
        "SELECT first_observed_at,payload FROM evidence WHERE evidence_id=? AND revision_id=?",
        (evidence_id, reference["revision_id"])).fetchone()
    real = protocol["evidence_kind"] == "operator_evidence"
    if row and (not real or json.loads(row[1]).get("evidence_kind") == "prospective") and instant(row[0]) <= instant(now):
        clocks.append(row[0])
    observed_in_cycles = set()
    for cycle in store.conn.execute("SELECT health FROM source_cycles WHERE source_id=?", (reference["source_id"],)):
        observed_in_cycles.update(json.loads(cycle[0]).get("observation_receipt_ids", []))
    for receipt in list_receipts(store, kind="native_inventory_observation"):
        payload = receipt["payload"]
        if reference not in payload.get("native_inventory", []):
            continue
        if not instant(payload["acquired_at"]) <= instant(receipt["observed_at"]) <= instant(now):
            continue
        if real and (payload.get("evidence_kind") not in ("operator_evidence", "collector_observation")
                     or receipt["receipt_id"] not in observed_in_cycles):
            continue
        if real and payload.get("evidence_kind") == "operator_evidence":
            try:
                validate_attestation(store, payload.get("attestation_ref"), purpose="source_inventory",
                                     subject_hash=digest({k: v for k, v in payload.items() if k != "attestation_ref"}),
                                     bindings={k: payload[k] for k in ("implementation_hash", "policy_hash")}, now=now)
            except (ValueError, KeyError):
                continue
        clocks.append(payload["acquired_at"])
    return min(clocks, key=instant) if clocks else None


def import_coverage(store, protocol_id, registry, policies, receipt_id, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    row, _, reasons = _coverage_check(store, protocol, receipt_id, now=now)
    payload = {"contract": VERSION, "protocol_id": protocol_id,
               "coverage_receipt_id": receipt_id, "coverage_hash": digest(row),
               "source_id": row["source_id"], "route_id": row["route_id"],
               "family": row["family"], "window_start": row["window_start"],
               "window_end": row["window_end"], "reason_codes": reasons,
               "status": "verified" if not reasons else "pending",
               "evidence_kind": row.get("evidence_kind", "unverified"),
               "source_interval_verification_receipt_id": row.get("source_interval_verification_receipt_id"),
               **_bindings(protocol)}
    refs = [protocol_id, receipt_id]
    if row.get("source_interval_verification_receipt_id"):
        refs.append(row["source_interval_verification_receipt_id"])
    with transaction(store):
        return _append(store, "coverage", payload, now=now, refs=refs)


def _closed(store, protocol_id, *, at=None):
    rows = _records(store, protocol_id, "inventory", at=at)
    if len(rows) > 1:
        raise ValueError("multiple closed inventories for one protocol")
    return rows[0] if rows else None


def _enumerated_rows(store, protocol, coverage, native, *, now):
    observations, excluded, reasons, refs = [], [], [], []
    for ref in native:
        first = _native_first_observed(store, ref, protocol, now=now)
        if not first:
            reasons.append("source_observation_time_unknown")
            continue
        if not instant(protocol["window_start"]) <= instant(first) <= instant(protocol["window_end"]):
            excluded.append({"native_ref": ref, "observed_at": first,
                             "reason": "outside_frozen_first_observation_window"})
            continue
        origin = _origin(store, ref, protocol, now=now)
        if not origin.get("origin_id"):
            reasons.append("assertion_origin_unresolved")
            continue
        links = origin.get("correspondence_receipt_ids", [])
        if links and origin.get("reviewed") is not True:
            reasons.append("origin_correspondence_unreviewed")
        refs.extend(links)
        observations.append({
            "native_ref": ref, "origin_id": origin["origin_id"],
            "origin_state_hash": digest(origin), "correspondence_receipt_ids": links,
            "source_id": coverage["source_id"], "route_id": coverage["route_id"],
            "family": coverage["family"], "coverage_receipt_id": coverage["receipt_id"],
            "observed_at": first,
            "evidence_kind": coverage.get("evidence_kind", "unverified")})
    return observations, excluded, sorted(set(reasons)), refs


def enumerate_sources(store, protocol_id, registry, policies, receipt_ids, *, now):
    """Enumerate source inventory membership without querying classified events."""
    protocol = protocol_state(store, protocol_id, registry, policies)
    _unique(receipt_ids, "source coverage receipt IDs")
    if _closed(store, protocol_id):
        raise ValueError("closed inventory cannot accept post-hoc observations")
    results = []
    with transaction(store):
        for receipt_id in receipt_ids:
            coverage, native, reasons = _coverage_check(store, protocol, receipt_id, now=now)
            imported = import_coverage(store, protocol_id, registry, policies, receipt_id, now=now)
            observations, excluded, issues, links = _enumerated_rows(store, protocol, coverage, native, now=now)
            reasons += issues
            refs = [protocol_id, receipt_id, imported["receipt_id"], *links]
            payload = {"contract": VERSION, "protocol_id": protocol_id,
                       "coverage_receipt_id": receipt_id, "observations": observations,
                       "excluded_source_records": excluded,
                       "enumeration_basis": "immutable_source_native_inventory",
                       "classifier_events_consulted": False,
                       "reason_codes": sorted(set(reasons)), **_bindings(protocol)}
            results.append(_append(store, "enumeration", payload, now=now, refs=refs))
    return {"contract": VERSION, "protocol_id": protocol_id,
            "enumerations": results, "automatic_activation": False}


def _inventory(store, protocol, *, now):
    grouped = {}
    reasons = []
    records = _records(store, protocol["protocol_id"], "enumeration", at=now)
    coverage_ids = set()
    validations = {}
    valid_native = set()
    for record in records:
        receipt_id = record["payload"]["coverage_receipt_id"]
        if receipt_id in validations:
            continue
        row, native, issues = _coverage_check(store, protocol, receipt_id, now=now)
        keys = {(row["source_id"], row["route_id"], row["family"], digest(ref)) for ref in native}
        validations[receipt_id] = (keys, issues, row, native)
        if not issues:
            valid_native |= keys
    historical = []
    for record in records:
        batch = record["payload"]
        coverage_ids.add(batch["coverage_receipt_id"])
        keys, issues, coverage, native = validations[batch["coverage_receipt_id"]]
        observations, excluded, source_issues, _ = _enumerated_rows(
            store, protocol, coverage, native, now=record["observed_at"])
        if (batch.get("contract") != VERSION or batch.get("enumeration_basis") != "immutable_source_native_inventory"
                or batch.get("classifier_events_consulted") is not False
                or any(batch.get(k) != v for k, v in _bindings(protocol).items())
                or batch.get("observations") != observations or batch.get("excluded_source_records") != excluded):
            reasons.append("enumeration_source_derivation_mismatch")
        reasons += source_issues
        historical += batch["reason_codes"]
        # A later complete immutable frame may resolve a prior cap/outage. The
        # old record stays, and every previously observed native ID must remain
        # in a complete matching frame; recovery cannot erase hard examples.
        if issues and not keys <= valid_native:
            reasons.extend(issues)
            reasons.append("previous_source_observations_not_in_complete_frame")
        # Retained snapshots cannot invent native observations or silently drop
        # difficult records even if inserted through the generic receipt API.
        for observation in observations:
            current = _origin(store, observation["native_ref"], protocol, now=now)
            if current.get("origin_id") != observation["origin_id"]:
                reasons.append("origin_correspondence_changed")
            origin = current.get("origin_id", observation["origin_id"])
            row = grouped.setdefault(origin, {"origin_id": origin, "native_refs": [],
                                               "observations": [], "families": []})
            if observation["native_ref"] not in row["native_refs"]:
                row["native_refs"].append(observation["native_ref"])
            row["observations"].append(observation)
            row["families"].append(observation["family"])
    mappings = _records(store, protocol["protocol_id"], "unit_mapping", at=now)
    by_origin = {}
    for receipt in mappings:
        by_origin.setdefault(receipt["payload"]["origin_id"], []).append(receipt)
    units = []
    for origin, group in sorted(grouped.items()):
        candidates = by_origin.get(origin, [])
        mapping = candidates[0] if len(candidates) == 1 else None
        if len(candidates) > 1:
            reasons.append("event_unit_mapping_conflicting_receipts")
        if mapping:
            payload = mapping["payload"]
            subject = {k: payload[k] for k in ("protocol_id", "origin_id", "native_refs", "units")}
            try:
                if any(payload.get(k) != v for k, v in _bindings(protocol).items()):
                    raise ValueError("event-unit mapping versions changed")
                if sorted(subject["native_refs"], key=digest) != sorted(group["native_refs"], key=digest):
                    raise ValueError("event-unit mapping source revision set changed")
                _review(store, payload["review_receipt_id"], purpose="event_unit_mapping",
                        subject=subject, protocol=protocol, now=now)
            except (ValueError, KeyError):
                reasons.append("event_unit_mapping_review_stale_or_revoked")
                mapping = None
        if mapping:
            specs = mapping["payload"]["units"]
        else:
            families = sorted(set(group["families"]))
            if len(families) != 1:
                reasons.append("multi_family_disclosure_requires_reviewed_mapping")
            specs = [{"unit_key": "whole_disclosure", "family": families[0],
                      "scope": "whole source disclosure; no inferred subevents"}]
        for spec in specs:
            unit_id = "unit:" + digest([origin, spec["unit_key"]])
            units.append({
                "unit_id": unit_id, "origin_id": origin,
                "family": spec["family"], "scope": spec["scope"],
                "native_refs": sorted(group["native_refs"], key=digest),
                "observations": sorted({digest(r): r for r in group["observations"]}.values(), key=digest),
                "mapping_receipt_id": mapping["receipt_id"] if mapping else None,
                "first_observed_at": min((r["observed_at"] for r in group["observations"]), key=instant)})
    sampling = protocol["sampling"]
    selected = [u for u in units if sampling["method"] == "census" or
                int(digest([sampling["seed"], u["unit_id"]]), 16) % sampling["modulus"] in sampling["buckets"]]
    return {"units": sorted(units, key=lambda r: r["unit_id"]),
            "selected": sorted(selected, key=lambda r: r["unit_id"]),
            "coverage_receipt_ids": sorted(coverage_ids),
            "enumeration_receipt_ids": [r["receipt_id"] for r in records],
            "historical_enumeration_reason_codes": sorted(set(historical)),
            "reason_codes": sorted(set(reasons))}


def map_bundled_events(store, protocol_id, registry, policies, mapping, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    if _closed(store, protocol_id):
        raise ValueError("event-unit mapping must precede inventory closure")
    origin = _text(mapping.get("origin_id"), "origin ID")
    inventory = _inventory(store, protocol, now=now)
    groups = [u for u in inventory["units"] if u["origin_id"] == origin]
    if not groups:
        raise ValueError("mapping must reference enumerated source origin")
    units = mapping.get("units")
    if not isinstance(units, list) or not 1 <= len(units) <= 100:
        raise ValueError("bounded reviewed event-unit mapping required")
    normalized = [{k: _text(r.get(k), k, 2000 if k == "scope" else 512)
                   for k in ("unit_key", "family", "scope")} for r in units]
    if len(normalized) != len({r["unit_key"] for r in normalized}):
        raise ValueError("duplicate event-unit mapping key")
    if any(r["family"] not in {s["family"] for s in protocol["source_scope"]} for r in normalized):
        raise ValueError("mapping family outside frozen source scope")
    subject = {"protocol_id": protocol_id, "origin_id": origin,
               "native_refs": groups[0]["native_refs"], "units": normalized}
    _review(store, mapping["review_receipt_id"], purpose="event_unit_mapping",
            subject=subject, protocol=protocol, now=now)
    payload = dict(subject, contract=VERSION, review_receipt_id=mapping["review_receipt_id"],
                   **_bindings(protocol))
    existing = _records(store, protocol_id, "unit_mapping")
    if any(r["payload"]["origin_id"] == origin and r["payload"] != payload for r in existing):
        raise ValueError("mapping already reviewed; a new protocol is required")
    with transaction(store):
        return _append(store, "unit_mapping", payload, now=now,
                       refs=[protocol_id, mapping["review_receipt_id"]])


def _coverage_union(store, protocol, coverage_ids, *, now):
    reasons = []
    strata = []
    for scope in protocol["source_scope"]:
        rows, incomplete = [], []
        for receipt_id in coverage_ids:
            row, native, invalid = _coverage_check(store, protocol, receipt_id, now=now)
            if all(row[k] == scope[k] for k in ("source_id", "route_id", "family")):
                if invalid:
                    incomplete.append((row, native, invalid))
                else:
                    rows.append(row)
        verified_native = {digest(ref) for r in rows for ref in r["native_inventory"]}
        for _, native, invalid in incomplete:
            if not rows or not {digest(ref) for ref in native} <= verified_native:
                reasons.extend(invalid)
        edge = instant(protocol["window_start"])
        end = instant(protocol["window_end"])
        has_gap = False
        for row in sorted(rows, key=lambda r: instant(r["window_start"])):
            if instant(row["window_start"]) > edge:
                reasons.append("unobserved_source_interval")
                has_gap = True
            edge = max(edge, instant(row["window_end"]))
        complete = bool(rows) and edge >= end and not has_gap
        if not complete:
            reasons.append("source_stratum_not_closed")
        strata.append({**{k: scope[k] for k in ("source_id", "route_id", "family")},
                       "coverage_receipt_ids": [r["receipt_id"] for r in rows],
                       "incomplete_historical_receipt_ids": [r[0]["receipt_id"] for r in incomplete],
                       "frame": scope["coverage_kind"], "interval_closed": complete,
                       "realtime_availability_certified": False})
    return strata, sorted(set(reasons))


def closure_request(store, protocol_id, registry, policies, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    if instant(now) < instant(protocol["window_end"]):
        raise ValueError("sampling window has not elapsed")
    inventory = _inventory(store, protocol, now=now)
    strata, gaps = _coverage_union(store, protocol, inventory["coverage_receipt_ids"], now=now)
    reasons = sorted(set(inventory["reason_codes"] + gaps))
    if not inventory["enumeration_receipt_ids"]:
        reasons.append("source_inventory_missing")
    if not inventory["selected"]:
        reasons.append("selected_inventory_empty")
    # Partition independence concerns source origins, including different
    # reviewed subevents from the same source disclosure.
    origins = {u["origin_id"] for u in inventory["units"]}
    conflicts = []
    for other in list_receipts(store, kind=KINDS["inventory"]):
        if other["payload"]["protocol_id"] == protocol_id or instant(other["observed_at"]) > instant(now):
            continue
        other_protocol = protocol_state(store, other["payload"]["protocol_id"])
        overlap = origins & {u["origin_id"] for u in other["payload"].get("units", other["payload"]["selected"])}
        if overlap:
            conflicts.append({"protocol_id": other_protocol["protocol_id"],
                              "partition": other_protocol["partition"],
                              "origin_ids": sorted(overlap)})
    if conflicts:
        reasons.append("event_origins_reused_across_partitions")
    return {"contract": VERSION, "protocol_id": protocol_id,
            "partition": protocol["partition"], **inventory,
            "inventory_hash": digest(inventory["selected"]),
            "source_strata": strata, "partition_conflicts": conflicts,
            "reason_codes": sorted(set(reasons)), **_bindings(protocol)}


def close_inventory(store, protocol_id, registry, policies, review_receipt_id=None, *, now):
    """Close only a complete, source-derived, reviewed immutable denominator."""
    protocol = protocol_state(store, protocol_id, registry, policies)
    previous = _closed(store, protocol_id)
    if previous:
        return previous
    request = closure_request(store, protocol_id, registry, policies, now=now)
    reasons = list(request["reason_codes"])
    if not review_receipt_id:
        reasons.append("inventory_closure_review_missing")
    else:
        _review(store, review_receipt_id, purpose="prospective_inventory_closure",
                subject=request, protocol=protocol, now=now)
    payload = dict(request, review_receipt_id=review_receipt_id,
                   status="closed" if not reasons else "pending",
                   reason_codes=sorted(set(reasons)))
    refs = [protocol_id] + request["coverage_receipt_ids"] + request["enumeration_receipt_ids"]
    refs += [r["receipt_id"] for r in _records(store, protocol_id, "unit_mapping")]
    if review_receipt_id:
        refs.append(review_receipt_id)
    with transaction(store):
        return _append(store, "inventory" if not reasons else "closure_attempt",
                       payload, now=now, refs=refs)


def _selected(store, protocol_id, unit_id, *, at=None):
    closed = _closed(store, protocol_id, at=at)
    if not closed:
        raise ValueError("closed source inventory required before adjudication")
    unit = next((u for u in closed["payload"]["selected"] if u["unit_id"] == unit_id), None)
    if not unit:
        raise ValueError("event unit is outside the closed sampled inventory")
    return closed, unit


def latest_adjudications(store, protocol_id, *, at=None):
    rows = _records(store, protocol_id, "adjudication", at=at)
    superseded = {r["payload"].get("supersedes") for r in rows}
    latest = [r for r in rows if r["receipt_id"] not in superseded]
    ids = [r["payload"]["unit_id"] for r in latest]
    if len(ids) != len(set(ids)):
        raise ValueError("conflicting adjudication heads")
    return latest


def adjudication_request(store, protocol_id, registry, policies, decision, *, now,
                         _existing_receipt_id=None):
    protocol = protocol_state(store, protocol_id, registry, policies)
    closed, unit = _selected(store, protocol_id, decision.get("unit_id"), at=now)
    fields = ("event_present", "detected", "unknown_issuer", "negative_outcome",
              "meaningfully_exercised", "critical_error", "proposed_publication")
    if any(type(decision.get(k)) is not bool for k in fields):
        raise ValueError("adjudication requires explicit boolean decisions")
    if decision["unknown_issuer"] and decision.get("issuer_ids"):
        raise ValueError("unknown issuer cannot have resolved issuer IDs")
    issuers = _unique(decision.get("issuer_ids", []), "issuer IDs", empty=True)
    if not decision["unknown_issuer"] and not issuers:
        raise ValueError("known issuer decision needs exact registry identities")
    if any(i not in registry.entities or registry.entities[i]["kind"] != "issuer" for i in issuers):
        raise ValueError("unsupported issuer mapping")
    if any(instant(registry.entities[i]["known_from"]) > instant(protocol["frozen_at"]) for i in issuers):
        raise ValueError("issuer identity was not available in the frozen era")
    if type(decision.get("correct")) is not bool:
        raise ValueError("explicit correctness adjudication required")
    reviewed_at = _text(decision.get("reviewed_at"), "reviewed_at")
    if not instant(unit["first_observed_at"]) <= instant(reviewed_at) <= instant(now):
        raise ValueError("adjudication precedes source observation or is future")
    detections = decision.get("detections", [])
    if not isinstance(detections, list) or len(detections) > 100:
        raise ValueError("bounded detection correspondence required")
    if bool(detections) != decision["detected"]:
        raise ValueError("detected flag must match exact detection correspondences")
    bound = []
    used = {}
    for old in latest_adjudications(store, protocol_id, at=now):
        if old["payload"]["unit_id"] != unit["unit_id"]:
            for d in old["payload"]["detections"]:
                used[d["event_id"]] = old["payload"]["unit_id"]
    for detection in detections:
        event_id = _text(detection.get("event_id"), "detected event ID")
        if event_id in used or any(d["event_id"] == event_id for d in bound):
            raise ValueError("detected event already matched to an event unit")
        packet = event_packet(store, event_id, detection["event_revision"])
        if packet["event"]["family"] != unit["family"]:
            raise ValueError("detected event family differs from the prespecified source unit")
        packet_hash = digest({"event": packet["event"], "documents": packet["documents"]})
        if detection.get("input_hash") != packet_hash:
            raise ValueError("detected evidence hash mismatch")
        head = store.conn.execute("SELECT revision_id FROM event_heads WHERE event_id=?", (event_id,)).fetchone()
        if not head or head[0] != detection["event_revision"]:
            raise ValueError("detection references a superseded event revision")
        lifecycle = packet.get("lifecycle", {})
        if (lifecycle.get("hard_reason_codes") or lifecycle.get("quarantined_pending_review")
                or lifecycle.get("quarantined") or lifecycle.get("status") in ("quarantined", "withdrawn", "retracted")):
            raise ValueError("detection is quarantined or no longer current")
        if instant(packet["first_observed_at"]) > instant(reviewed_at):
            raise ValueError("detected event observed after adjudication")
        if protocol["evidence_kind"] == "operator_evidence" and any(
                d.get("evidence_kind") != "prospective" for d in packet["documents"]):
            raise ValueError("synthetic, replay or unverified detection cannot become prospective proof")
        matches = []
        for document in packet["documents"]:
            reference = {"source_id": document["source_policy_id"],
                         "native_id": document["native_id"], "revision_id": document["revision_id"]}
            origin = _origin(store, reference, protocol, now=now)
            if origin["origin_id"] == unit["origin_id"]:
                matches.append(reference)
        if not matches:
            raise ValueError("detection has no reviewed source/native/revision correspondence")
        bound.append(dict(detection, observed_at=packet["first_observed_at"],
                          native_refs=matches,
                          lifecycle_hash=digest(packet.get("lifecycle", {}))))
    supersedes = decision.get("supersedes")
    if _existing_receipt_id:
        retained = get_receipt(store, _existing_receipt_id, kind=KINDS["adjudication"])
        if (retained["payload"].get("protocol_id") != protocol_id
                or retained["payload"].get("unit_id") != unit["unit_id"]
                or instant(retained["observed_at"]) > instant(now)):
            raise ValueError("retained adjudication scope or clock mismatch")
        previous = get_receipt(store, supersedes, kind=KINDS["adjudication"]) if supersedes else None
        if previous and (previous["receipt_id"] == _existing_receipt_id
                         or previous["payload"].get("protocol_id") != protocol_id
                         or previous["payload"].get("unit_id") != unit["unit_id"]
                         or instant(previous["observed_at"]) > instant(retained["observed_at"])):
            raise ValueError("retained adjudication supersession scope or clock mismatch")
    else:
        previous = next((r for r in latest_adjudications(store, protocol_id, at=now)
                         if r["payload"]["unit_id"] == unit["unit_id"]), None)
    if previous and supersedes != previous["receipt_id"]:
        raise ValueError("adjudication correction must explicitly supersede current receipt")
    if supersedes and (not previous or supersedes != previous["receipt_id"]):
        raise ValueError("adjudication supersession is not current")
    return {"contract": VERSION, "protocol_id": protocol_id,
            "inventory_receipt_id": closed["receipt_id"],
            "inventory_hash": closed["payload"]["inventory_hash"],
            "unit_id": unit["unit_id"], "origin_id": unit["origin_id"],
            "family": unit["family"], "issuer_ids": issuers,
            **{k: decision[k] for k in fields}, "correct": decision["correct"],
            "detections": bound, "reviewed_at": reviewed_at,
            "reviewer": _text(decision.get("reviewer"), "reviewer", 120),
            "supersedes": supersedes, "evidence_kind": protocol["evidence_kind"],
            "note": str(decision.get("note", ""))[:2000], **_bindings(protocol)}


def adjudicate(store, protocol_id, registry, policies, decision, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    # Exact replay is safe even after a later correction: it cannot become head.
    for row in _records(store, protocol_id, "adjudication"):
        if row["payload"].get("submitted_hash") == digest(decision):
            return row
    subject = adjudication_request(store, protocol_id, registry, policies, decision, now=now)
    review = _review(store, decision["review_receipt_id"], purpose="prospective_adjudication",
                     subject=subject, protocol=protocol, now=now)
    attestation = review["payload"]
    if attestation["reviewer"] != subject["reviewer"] or attestation["reviewed_at"] != subject["reviewed_at"]:
        raise ValueError("adjudicator and reviewed clock must match the attestation")
    payload = dict(subject, review_receipt_id=decision["review_receipt_id"],
                   submitted_hash=digest(decision))
    refs = [protocol_id, subject["inventory_receipt_id"], decision["review_receipt_id"]]
    if subject["supersedes"]:
        refs.append(subject["supersedes"])
    with transaction(store):
        if protocol["partition"] == "untouched":
            from .holdout import record_access
            record_access(store, protocol_id, registry, policies,
                          {"actor": subject["reviewer"], "purpose": "blind_adjudication",
                           "at": subject["reviewed_at"],
                           "attestation_id": decision["review_receipt_id"],
                           "attested_subject": subject}, now=now)
        return _append(store, "adjudication", payload, now=now, refs=refs)


def run_request(store, protocol_id, registry, policies, run, *, now, _existing_receipt_id=None):
    from zoneinfo import ZoneInfo
    protocol = protocol_state(store, protocol_id, registry, policies)
    ids = _unique(run.get("source_cycle_ids"), "run source cycles")
    start, end = instant(run["started_at"]), instant(run["completed_at"])
    if not instant(protocol["window_start"]) <= start <= end <= min(
            instant(now), instant(protocol["window_end"])):
        raise ValueError("run outside frozen observation window")
    mode = run.get("execution_mode")
    if mode not in ("manual", "scheduled", "backfill"):
        raise ValueError("explicit run execution mode required")
    session = start.astimezone(ZoneInfo(protocol["session_timezone"])).date().isoformat()
    if run.get("session") != session:
        raise ValueError("run session must match frozen New York start date")
    if type(run.get("exit_status")) is not int:
        raise ValueError("actual process exit status required")
    cycles = []
    for cycle_id in ids:
        row = store.conn.execute("SELECT * FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
        if not row or not start <= instant(row["observed_at"]) <= end:
            raise ValueError("source cycle absent or outside run")
        if row["source_id"] not in {s["source_id"] for s in protocol["source_scope"]}:
            raise ValueError("run cycle outside source scope")
        health = json.loads(row["health"])
        cycles.append({"cycle_id": cycle_id, "input_hash": row["input_hash"],
                       "status": health["status"], "gaps": health.get("gaps", [])})
    for prior in _records(store, protocol_id, "run", at=now):
        if prior["receipt_id"] == _existing_receipt_id:
            continue
        if prior["payload"]["run_id"] == run.get("run_id"):
            raise ValueError("run identity already imported")
        if set(prior["payload"]["source_cycle_ids"]) & set(ids):
            raise ValueError("source cycles already consumed by another run")
    scheduled_for = run.get("scheduled_for")
    if mode == "scheduled":
        if not scheduled_for or instant(scheduled_for) > start:
            raise ValueError("scheduled run needs the actual due-time receipt")
        _hash(run.get("scheduler_log_hash"), "scheduler log hash")
    return {"contract": VERSION, "protocol_id": protocol_id,
            "run_id": _text(run.get("run_id"), "run ID"),
            "started_at": run["started_at"], "completed_at": run["completed_at"],
            "session": session, "execution_mode": mode, "scheduled_for": scheduled_for,
            "scheduler_log_hash": run.get("scheduler_log_hash"),
            "invocation_hash": _hash(run.get("invocation_hash"), "invocation hash"),
            "exit_status": run["exit_status"], "source_cycle_ids": ids, "cycles": cycles,
            "evidence_kind": protocol["evidence_kind"],
            "eligible": mode == "scheduled" and run["exit_status"] == 0 and all(
                c["status"] == "complete" and not c["gaps"] for c in cycles),
            **_bindings(protocol)}


def import_run(store, protocol_id, registry, policies, run, *, now):
    for prior in _records(store, protocol_id, "run"):
        if prior["payload"].get("submitted_hash") == digest(run):
            return prior
    protocol = protocol_state(store, protocol_id, registry, policies)
    subject = run_request(store, protocol_id, registry, policies, run, now=now)
    _review(store, run["review_receipt_id"], purpose="prospective_run",
            subject=subject, protocol=protocol, now=now)
    with transaction(store):
        return _append(store, "run", dict(subject, review_receipt_id=run["review_receipt_id"],
                                         submitted_hash=digest(run)), now=now,
                       refs=[protocol_id, run["review_receipt_id"]])


def migrate_legacy(store, *, now, fail_at=None):
    """Append historical links; never modify or relabel the original pilot rows."""
    originals = [dict(r) for r in store.conn.execute("SELECT * FROM pilot_records ORDER BY record_id")]
    result = []
    with transaction(store):
        for row in originals:
            payload = {"contract": VERSION, "legacy_record_id": row["record_id"],
                       "legacy_pilot_id": row["pilot_id"], "legacy_kind": row["kind"],
                       "legacy_observed_at": row["observed_at"],
                       "legacy_payload_hash": digest(json.loads(row["payload"])),
                       "legacy_payload_bytes_hash": hashlib.sha256(row["payload"].encode("utf-8")).hexdigest(),
                       "partition": "historical_audit", "real_prospective_count": 0,
                       "interpretation": "original contract retained; no retrospective promotion"}
            result.append(_append(store, "legacy_history", payload, now=now))
            if fail_at == "after_first":
                raise RuntimeError("injected migration interruption")
    return {"contract": VERSION, "historical_links": [r["receipt_id"] for r in result],
            "original_records_modified": 0, "real_prospective_count": 0}


def evaluation_report(store, protocol_id, registry, policies, *, now,
                      untouched_receipt_id=None):
    """Re-resolve every binding before calculating release evidence."""
    from .evaluation import evaluate, _VerifiedEvidence
    from .timing import timing_report
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    current = current_bindings(registry, policies)
    reasons = []
    if any(r["payload"].get("protocol_id") == protocol_id
           and instant(r["observed_at"]) > instant(now) for r in list_receipts(store)):
        reasons.append("future_protocol_receipts_present")
    if any(protocol[k] != v for k, v in current.items()):
        reasons.append("implementation_registry_or_policy_changed")
    try:
        _review(store, protocol["review_receipt_id"], purpose="prospective_protocol",
                subject={k: v for k, v in protocol.items()
                         if k not in ("protocol_id", "review_receipt_id", "frozen_at")},
                protocol=protocol, now=now)
    except ValueError:
        reasons.append("protocol_review_stale_or_revoked")
    closed = _closed(store, protocol_id, at=now)
    if not closed:
        reasons.append("prospective_inventory_not_closed")
    selected = closed["payload"]["selected"] if closed else []
    if closed:
        current_inventory = _inventory(store, protocol, now=now)
        selected = current_inventory["selected"]
        if digest(current_inventory["selected"]) != closed["payload"]["inventory_hash"]:
            reasons.append("inventory_or_origin_correspondence_changed")
        reasons += current_inventory["reason_codes"]
        _, gaps = _coverage_union(store, protocol, closed["payload"]["coverage_receipt_ids"], now=now)
        reasons += gaps
        subject = {k: v for k, v in closed["payload"].items()
                   if k not in ("review_receipt_id", "status")}
        try:
            if (closed["payload"].get("status") != "closed"
                    or subject != closure_request(store, protocol_id, registry, policies, now=now)):
                raise ValueError("closed inventory is not its exact source derivation")
        except (ValueError, KeyError, TypeError):
            reasons.append("inventory_closure_derivation_mismatch")
        try:
            _review(store, closed["payload"]["review_receipt_id"],
                    purpose="prospective_inventory_closure", subject=subject,
                    protocol=protocol, now=now)
        except ValueError:
            reasons.append("inventory_closure_review_stale_or_revoked")
    by_id = {u["unit_id"]: u for u in selected}
    adjudications = latest_adjudications(store, protocol_id, at=now)
    cases, gold, invalid, verified_adjudications = [], [], [], []
    corpus_hash = closed["payload"]["inventory_hash"] if closed else None
    case_bindings = {k: protocol[k] for k in ("implementation_hash", "source_scope_hash")}
    case_bindings["corpus_hash"] = corpus_hash
    real = protocol["evidence_kind"] == "operator_evidence" and protocol["partition"] == "prospective"
    for receipt in adjudications:
        a = receipt["payload"]
        unit = by_id.get(a["unit_id"])
        issue = []
        subject = {k: v for k, v in a.items() if k not in ("review_receipt_id", "submitted_hash")}
        try:
            derived = adjudication_request(
                store, protocol_id, registry, policies, a, now=now,
                _existing_receipt_id=receipt["receipt_id"])
            if derived != subject:
                raise ValueError("adjudication is not its exact source-unit derivation")
        except (ValueError, KeyError, TypeError):
            invalid.append({"unit_id": a.get("unit_id"),
                            "reason_codes": ["adjudication_source_correspondence_or_shape_invalidated"]})
            continue
        try:
            review = _review(store, a["review_receipt_id"], purpose="prospective_adjudication",
                             subject=subject, protocol=protocol, now=now)
            if any(review["payload"].get(k) != subject[k] for k in ("reviewer", "reviewed_at")):
                raise ValueError("adjudication and attestation reviewer or clock mismatch")
        except (ValueError, KeyError, TypeError):
            issue.append("adjudication_review_stale_or_revoked")
        if not unit:
            issue.append("adjudication_outside_inventory")
        for d in a["detections"]:
            try:
                packet = event_packet(store, d["event_id"], d["event_revision"])
                if not unit or packet["event"]["family"] != unit["family"]:
                    issue.append("detected_event_family_mismatch")
                head = store.conn.execute("SELECT revision_id FROM event_heads WHERE event_id=?",
                                          (d["event_id"],)).fetchone()
                if not head or head[0] != d["event_revision"]:
                    issue.append("detected_event_superseded")
                if digest({"event": packet["event"], "documents": packet["documents"]}) != d["input_hash"]:
                    issue.append("detected_evidence_changed")
                lifecycle = packet.get("lifecycle", {})
                if (digest(lifecycle) != d["lifecycle_hash"]
                        or lifecycle.get("hard_reason_codes")
                        or lifecycle.get("quarantined_pending_review")
                        or lifecycle.get("quarantined") is True
                        or lifecycle.get("status") in ("quarantined", "withdrawn", "retracted")):
                    issue.append("detected_event_lifecycle_changed")
            except ValueError:
                issue.append("detected_evidence_missing")
        if issue:
            invalid.append({"unit_id": a["unit_id"], "reason_codes": sorted(set(issue))})
            continue
        verified_adjudications.append(receipt)
        observed = min((d["observed_at"] for d in a["detections"]), key=instant,
                       default=unit["first_observed_at"])
        source_classes = sorted({next(s["source_class"] for s in protocol["source_scope"]
                                if all(s[k] == o[k] for k in ("source_id", "route_id", "family")))
                                 for o in unit["observations"]})
        source_class = source_classes[0] if len(source_classes) == 1 else "mixed_review_required"
        outside = bool(a["issuer_ids"]) and not set(a["issuer_ids"]) & set(protocol["covered_issuer_ids"])
        evidence_kind = "prospective" if real else "synthetic" if protocol["evidence_kind"] == "synthetic" else protocol["partition"]
        gold.append(dict(event_id=a["unit_id"], prespecified=True, in_source_scope=True,
                         detected=a["detected"], event_present=a["event_present"],
                         unknown_issuer=a["unknown_issuer"], negative_outcome=a["negative_outcome"],
                         adjudication_receipt=receipt["receipt_id"], evidence_kind=evidence_kind,
                         evaluation_track="prospective" if real else protocol["partition"],
                         observed_at=unit["first_observed_at"], reviewed_at=a["reviewed_at"],
                         family=a["family"], source_class=source_class, **case_bindings))
        if a["detected"] or a["proposed_publication"]:
            cases.append(dict(event_case_id=a["unit_id"], evidence_kind=evidence_kind,
                              adjudicator=a["reviewer"], adjudication_receipt=receipt["receipt_id"],
                              correct=a["correct"], critical_error=a["critical_error"],
                              meaningfully_exercised=a["meaningfully_exercised"],
                              outside_cohort=outside, unknown_issuer=a["unknown_issuer"],
                              proposed_publication=False, published=False,
                              family=a["family"], source_class=source_class, observed_at=observed,
                              **case_bindings))
    if invalid:
        reasons.append("adjudications_invalidated")
    # Publication precision is a complete emitted-proposal census. The sampled
    # recall labels and a caller's proposed_publication flag cannot select it.
    from .publications import verified_records as publication_records
    publication_census, publication_labels = publication_records(
        store, protocol_id, registry, policies, now=now)
    reasons += publication_census["reason_codes"]
    case_by_unit = {c["event_case_id"]: c for c in cases}
    for receipt, unit in publication_labels:
        a = receipt["payload"]
        outside = bool(a["issuer_ids"]) and not set(a["issuer_ids"]) & set(protocol["covered_issuer_ids"])
        case_by_unit[unit["unit_id"]] = dict(
            event_case_id=unit["unit_id"], evidence_kind="prospective" if real else protocol["evidence_kind"],
            adjudicator=a["reviewer"], adjudication_receipt=receipt["receipt_id"],
            correct=a["correct"], critical_error=a["critical_error"],
            meaningfully_exercised=a["meaningfully_exercised"], outside_cohort=outside,
            unknown_issuer=a["unknown_issuer"], proposed_publication=True, published=False,
            family=unit["families"][0] if len(unit["families"]) == 1 else "unknown",
            source_class=unit["source_classes"][0] if len(unit["source_classes"]) == 1 else "unknown",
            observed_at=unit["first_proposed_at"], **case_bindings)
    cases = list(case_by_unit.values())
    if protocol["unresolved_coverage"]:
        reasons.append("baseline_coverage_identity_unresolved")
    if not real:
        reasons.append("partition_or_synthetic_evidence_not_real_prospective")
    run_rows = []
    for receipt in _records(store, protocol_id, "run", at=now):
        r = receipt["payload"]
        subject = {k: v for k, v in r.items() if k not in ("review_receipt_id", "submitted_hash")}
        try:
            derived = run_request(store, protocol_id, registry, policies, r,
                                  now=now, _existing_receipt_id=receipt["receipt_id"])
            if derived != subject:
                raise ValueError("run is not the exact current source-cycle derivation")
            _review(store, r["review_receipt_id"], purpose="prospective_run",
                    subject=subject, protocol=protocol, now=now)
        except (ValueError, KeyError, TypeError):
            reasons.append("run_source_cycle_scope_or_attestation_invalidated")
            continue
        run_rows.append(dict(r, **case_bindings, receipt=receipt["receipt_id"],
                             scheduler_receipt=r["review_receipt_id"],
                             eligible=r["eligible"] and real))
    timings = timing_report(store, protocol_id, registry, policies, now=now)
    untouched = False
    if untouched_receipt_id:
        from .holdout import verify_consumption
        try:
            proof = verify_consumption(store, untouched_receipt_id, registry, policies, now=now)
            other = protocol_state(store, proof["payload"]["protocol_id"])
            same_scope = other["source_scope"] == protocol["source_scope"]
            other_inventory = _closed(store, other["protocol_id"], at=now)
            overlap = {u["origin_id"] for u in closed["payload"]["units"]} & {
                u["origin_id"] for u in other_inventory["payload"]["units"]} if closed else set()
            untouched = same_scope and not overlap and other["evidence_kind"] == "operator_evidence"
            if not untouched:
                reasons.append("untouched_scope_overlap_or_evidence_mismatch")
        except ValueError:
            reasons.append("untouched_receipt_invalid")
    manifest = dict(protocol, corpus_hash=corpus_hash,
                    gold_event_ids=sorted(by_id),
                    source_coverage_receipt=closed["receipt_id"] if closed else None,
                    untouched_test_receipt=untouched_receipt_id if untouched else None)
    verified = _VerifiedEvidence(
        protocol_id=protocol_id, reasons=tuple(sorted(set(reasons))),
        coverage_ready=closed is not None and not any(
            "coverage" in r or "interval" in r or "inventory" in r or "stratum" in r for r in reasons),
        timing=timings, untouched_ready=untouched, real_prospective=real,
        required_strata=tuple(sorted({(s["family"], s["source_class"]) for s in protocol["source_scope"]})),
        minimum_unknown=protocol["minimum_unknown_issuer_gold"],
        minimum_negative=protocol["minimum_negative_outcome_gold"])
    report = evaluate(manifest, cases, run_rows, gold, _verified=verified)
    report.update(protocol_id=protocol_id, partition=protocol["partition"],
                  evidence_kind=protocol["evidence_kind"], trust_boundary=TRUST_BOUNDARY,
                  inventory={"closed": bool(closed), "expected": len(selected),
                             "adjudicated": len(gold), "pending": sorted(set(by_id) - {r["event_id"] for r in gold}),
                             "invalidated_adjudications": invalid,
                             "source_strata": closed["payload"]["source_strata"] if closed else [],
                             "unique_origins": len({u["origin_id"] for u in selected})},
                  publication_inventory=publication_census,
                  fixture_or_nonprospective_summary={
                      "adjudications": len(verified_adjudications) if not real else 0,
                      "detections": sum(r["payload"]["detected"] for r in verified_adjudications) if not real else 0,
                      "misses": [r["payload"]["unit_id"] for r in verified_adjudications
                                 if r["payload"]["event_present"] and not r["payload"]["detected"]] if not real else [],
                      "unknown_issuer_units": sum(r["payload"]["unknown_issuer"] for r in verified_adjudications) if not real else 0,
                      "negative_outcome_units": sum(r["payload"]["negative_outcome"] for r in verified_adjudications) if not real else 0,
                      "publication_adjudications": len(publication_labels) if not real else 0,
                      "real_gate_observations": 0 if not real else len(gold)},
                  pending_reason_codes=sorted(set(reasons)),
                  automatic_activation=False)
    return report


def _evaluation_inputs(store, protocol_id, untouched_receipt_id=None, *, at=None):
    ids = {protocol_id}
    for receipt in list_receipts(store):
        if (receipt["kind"] != KINDS["evaluation"]
                and receipt["payload"].get("protocol_id") == protocol_id
                and (at is None or instant(receipt["observed_at"]) <= instant(at))):
            ids.add(receipt["receipt_id"])
    if untouched_receipt_id:
        ids.add(untouched_receipt_id)
    return sorted(ids)


def record_evaluation(store, protocol_id, registry, policies, *, now,
                      untouched_receipt_id=None):
    """Record a reproducible calculation; this is not an activation approval."""
    protocol = protocol_state(store, protocol_id, registry, policies)
    report = evaluation_report(store, protocol_id, registry, policies, now=now,
                               untouched_receipt_id=untouched_receipt_id)
    ids = _evaluation_inputs(store, protocol_id, untouched_receipt_id, at=now)
    payload = {"contract": VERSION, "protocol_id": protocol_id,
               "report": report, "report_hash": digest(report),
               "input_receipt_ids": ids, "input_receipt_set_hash": digest(ids),
               "untouched_receipt_id": untouched_receipt_id,
               "evidence_kind": protocol["evidence_kind"], "partition": protocol["partition"],
               "automatic_activation": False, "trust_boundary": TRUST_BOUNDARY,
               **_bindings(protocol)}
    with transaction(store):
        return _append(store, "evaluation", payload, now=now, refs=ids)


def verify_evaluation_receipt(store, receipt_id, registry, policies, *, now,
                              require_real=True):
    receipt = get_receipt(store, receipt_id, kind=KINDS["evaluation"])
    payload = receipt["payload"]
    protocol = protocol_state(store, payload["protocol_id"], registry, policies)
    if instant(receipt["observed_at"]) > instant(now):
        raise ValueError("evaluation receipt is from the future")
    if require_real and (protocol["evidence_kind"] != "operator_evidence" or protocol["partition"] != "prospective"):
        raise ValueError("synthetic, historical and nonprospective evaluation cannot mature a real gate")
    if any(payload.get(k) != v for k, v in _bindings(protocol).items()):
        raise ValueError("evaluation version bindings mismatch")
    ids = _evaluation_inputs(store, protocol["protocol_id"], payload.get("untouched_receipt_id"), at=now)
    if ids != payload["input_receipt_ids"] or digest(ids) != payload["input_receipt_set_hash"]:
        raise ValueError("evaluation input receipts changed")
    current = evaluation_report(store, protocol["protocol_id"], registry, policies, now=now,
                                untouched_receipt_id=payload.get("untouched_receipt_id"))
    if digest(payload["report"]) != payload["report_hash"] or digest(current) != payload["report_hash"]:
        raise ValueError("evaluation report changed or evidence was invalidated")
    return {**receipt, "current_report": current, "verified_at": now}
