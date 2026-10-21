"""Receipt-backed coverage/timing import; imprecise clocks remain descriptive."""
from __future__ import annotations

from datetime import timedelta
import json
import math
import re

from .identities import digest, instant
from .receipts import get_receipt, list_receipts, put_receipt
from .prospective import (
    VERSION, _bindings, _closed, _coverage_check, _review, _selected, _text,
    native_reference, protocol_state, transaction)

AVAILABILITY_KIND = "prospective_source_availability"
ACQUISITION_KIND = "prospective_acquisition_timing"
CANDIDATE_KIND = "prospective_candidate_timing"
TIMING_KIND = "prospective_timing"
MEASURED_PRECISIONS = {"second": 1, "millisecond": .001, "microsecond": .000001}


def _clock(value, name):
    """Return a bounded clock interval without inventing a timezone or precision."""
    if not isinstance(value, dict):
        return None, [name + "_clock_missing"]
    reasons = []
    precision = value.get("precision")
    uncertainty = value.get("uncertainty_seconds")
    if precision not in MEASURED_PRECISIONS:
        reasons.append(name + "_precision_insufficient")
    if value.get("timezone_basis") not in ("explicit_offset", "documented_source_timezone", "recorded_utc_clock"):
        reasons.append(name + "_timezone_unverified")
    if type(uncertainty) not in (int, float) or not math.isfinite(uncertainty) or uncertainty < 0:
        reasons.append(name + "_uncertainty_unknown")
    try:
        stamp = instant(value.get("at"))
    except (ValueError, TypeError, OverflowError):
        return None, reasons + [name + "_instant_unknown"]
    if reasons:
        return None, reasons
    half_width = uncertainty + MEASURED_PRECISIONS[precision] / 2
    return (stamp - timedelta(seconds=half_width), stamp + timedelta(seconds=half_width)), []


def _append(store, kind, payload, *, now, refs):
    receipt_id = "prospective-timing:" + digest([kind, payload])
    try:
        existing = get_receipt(store, receipt_id, kind=kind)
    except (KeyError, ValueError):
        existing = None
    if existing:
        return existing
    return put_receipt(store, kind, payload, observed_at=now, refs=sorted(set(refs)),
                       receipt_id=receipt_id, manage_transaction=False)


def clock_proof_request(store, protocol_id, registry, policies, kind, record, *, now):
    """Prepare review of an actual source log/API or acquisition log reference.

    A publication clock may be retained descriptively. It is never upgraded to
    public availability merely because a source supplied the publication date.
    """
    protocol = protocol_state(store, protocol_id, registry, policies)
    if kind not in ("availability", "acquisition", "candidate"):
        raise ValueError("unsupported timing proof kind")
    reference = native_reference(record["native_ref"])
    if reference["source_id"] not in {s["source_id"] for s in protocol["source_scope"]}:
        raise ValueError("timing proof source outside frozen protocol")
    proof_id = _text(record.get("source_proof_receipt_id"), "source proof receipt")
    source_proof = get_receipt(store, proof_id)
    if source_proof["observed_at"] and instant(source_proof["observed_at"]) > instant(now):
        raise ValueError("future underlying source proof")
    raw = source_proof["payload"]
    if raw.get("native_ref") != reference or not re.fullmatch(r"[a-f0-9]{64}", str(raw.get("artifact_hash", ""))):
        raise ValueError("source timing proof needs exact native revision and artifact hash")
    if protocol["evidence_kind"] == "operator_evidence" and (
            raw.get("synthetic") is True or raw.get("evidence_kind") not in ("operator_evidence", "collector_observation")):
        raise ValueError("synthetic or unverified raw clock proof cannot become operational evidence")
    # The reference resolves to immutable bytes/hash in the receipt ledger.
    # Its origin and external authenticity remain operator responsibilities.
    common = {"contract": VERSION, "protocol_id": protocol_id,
              "native_ref": reference, "source_proof_receipt_id": proof_id,
              "source_proof_hash": source_proof["record_hash"],
              "evidence_kind": protocol["evidence_kind"], **_bindings(protocol)}
    if kind == "availability":
        basis = record.get("availability_basis")
        if basis not in ("source_availability_api", "dissemination_log",
                         "publication_only", "first_successful_poll", "unknown"):
            raise ValueError("unsupported availability basis")
        common.update(availability_basis=basis, clock=record.get("clock"),
                      publication_clock=record.get("publication_clock"),
                      source_field=_text(record.get("source_field"), "availability source field"))
        # Unknown/imprecise clocks are deliberately importable, with no latency credit.
        _, issues = _clock(common["clock"], "availability")
        common["clock_reason_codes"] = issues
    elif kind == "acquisition":
        cycle_id = _text(record.get("source_cycle_id"), "source cycle ID")
        cycle = store.conn.execute("SELECT * FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
        if not cycle or cycle["source_id"] != reference["source_id"]:
            raise ValueError("acquisition proof has no matching source cycle")
        start, completed = record.get("started_clock"), record.get("completed_clock")
        low, low_reasons = _clock(start, "acquisition_start")
        high, high_reasons = _clock(completed, "acquisition_completion")
        if low and high and low[0] > high[1]:
            raise ValueError("acquisition completion precedes start")
        if high and not low_reasons + high_reasons:
            observed = instant(cycle["observed_at"])
            if observed < low[0] or observed > high[1]:
                raise ValueError("stored source cycle clock outside acquisition interval")
        health = json.loads(cycle["health"])
        common.update(source_cycle_id=cycle_id, source_cycle_input_hash=cycle["input_hash"],
                      started_clock=start, completed_clock=completed,
                      observed_at=cycle["observed_at"], cycle_status=health["status"],
                      cycle_gaps=health.get("gaps", []),
                      clock_reason_codes=sorted(set(low_reasons + high_reasons)))
    else:
        event_id = _text(record.get("event_id"), "candidate event ID")
        revision = _text(record.get("event_revision"), "candidate event revision")
        packet_hash = _text(record.get("packet_hash"), "candidate packet hash")
        packet = store.conn.execute(
            "SELECT payload,observed_at FROM event_packets WHERE event_id=? AND revision_id=? AND packet_hash=?",
            (event_id, revision, packet_hash)).fetchone()
        if not packet or digest(json.loads(packet["payload"])) != packet_hash:
            raise ValueError("candidate timing requires exact retained event packet")
        source_cycle_id = _text(record.get("source_cycle_id"), "candidate source cycle")
        cycle = store.conn.execute("SELECT * FROM source_cycles WHERE cycle_id=?", (source_cycle_id,)).fetchone()
        if not cycle or cycle["source_id"] != reference["source_id"]:
            raise ValueError("candidate timing requires matching source cycle")
        interval, issues = _clock(record.get("clock"), "candidate_ready")
        if interval and (interval[1] < instant(packet["observed_at"]) or interval[1] < instant(cycle["observed_at"])):
            raise ValueError("candidate completion precedes retained packet/source cycle")
        common.update(event_id=event_id, event_revision=revision, packet_hash=packet_hash,
                      packet_observed_at=packet["observed_at"], source_cycle_id=source_cycle_id,
                      source_cycle_input_hash=cycle["input_hash"], clock=record.get("clock"),
                      clock_reason_codes=issues)
    return common


def import_clock_proof(store, protocol_id, registry, policies, kind, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    subject = clock_proof_request(store, protocol_id, registry, policies, kind, record, now=now)
    _review(store, record["review_receipt_id"], purpose="source_" + kind + "_timing",
            subject=subject, protocol=protocol, now=now)
    payload = dict(subject, review_receipt_id=record["review_receipt_id"])
    with transaction(store):
        receipt_kind = {"availability": AVAILABILITY_KIND, "acquisition": ACQUISITION_KIND,
                        "candidate": CANDIDATE_KIND}[kind]
        return _append(store, receipt_kind,
                       payload, now=now, refs=[protocol_id, record["review_receipt_id"],
                                               subject["source_proof_receipt_id"]])


def _proof(store, receipt_id, kind, protocol, *, now):
    receipt = get_receipt(store, receipt_id, kind=kind)
    if instant(receipt["observed_at"]) > instant(now):
        raise ValueError("timing proof was imported after the report clock")
    payload = receipt["payload"]
    if payload.get("protocol_id") != protocol["protocol_id"]:
        raise ValueError("timing proof belongs to another protocol")
    if any(payload.get(k) != v for k, v in _bindings(protocol).items()):
        raise ValueError("timing proof version mismatch")
    subject = {k: v for k, v in payload.items() if k != "review_receipt_id"}
    purpose = {AVAILABILITY_KIND: "source_availability_timing", ACQUISITION_KIND: "source_acquisition_timing",
               CANDIDATE_KIND: "source_candidate_timing"}[kind]
    _review(store, payload["review_receipt_id"], purpose=purpose, subject=subject,
            protocol=protocol, now=now)
    proof = get_receipt(store, payload["source_proof_receipt_id"])
    if proof["record_hash"] != payload["source_proof_hash"]:
        raise ValueError("underlying timing source proof changed")
    return receipt


def _calculate(store, protocol, record, *, now):
    _, unit = _selected(store, protocol["protocol_id"], record["unit_id"], at=now)
    reasons = []
    refs = [protocol["protocol_id"]]
    availability = acquisition = candidate = None
    for field, kind in (("availability_receipt_id", AVAILABILITY_KIND),
                        ("acquisition_receipt_id", ACQUISITION_KIND),
                        ("candidate_receipt_id", CANDIDATE_KIND)):
        if not record.get(field):
            reasons.append(field + "_missing")
            continue
        try:
            receipt = _proof(store, record[field], kind, protocol, now=now)
        except (ValueError, KeyError):
            reasons.append(field + "_unverified")
            continue
        refs.append(receipt["receipt_id"])
        if receipt["payload"]["native_ref"] not in unit["native_refs"]:
            reasons.append(field + "_native_revision_mismatch")
        if field == "availability_receipt_id":
            availability = receipt["payload"]
        elif field == "acquisition_receipt_id":
            acquisition = receipt["payload"]
        else:
            candidate = receipt["payload"]
    native_keys = {digest(p["native_ref"]) for p in (availability, acquisition, candidate) if p}
    if len(native_keys) > 1:
        reasons.append("timing_native_revisions_disagree")
    coverage_ids = record.get("coverage_receipt_ids", [])
    if (not isinstance(coverage_ids, list) or not coverage_ids
            or len(coverage_ids) != len(set(coverage_ids))):
        reasons.append("coverage_receipts_missing_or_repeated")
        coverage_ids = []
    coverages = []
    for receipt_id in coverage_ids:
        try:
            row, native, issues = _coverage_check(store, protocol, receipt_id, now=now)
        except (ValueError, KeyError):
            reasons.append("coverage_receipt_unverified")
            continue
        reasons.extend(issues)
        if acquisition and acquisition["native_ref"] not in native:
            reasons.append("timing_native_revision_not_in_coverage")
        refs.append(receipt_id)
        coverages.append(row)
    if not any(c["kind"] == "feed_interval" for c in coverages):
        reasons.append("continuous_feed_interval_not_established")
    if availability and availability["availability_basis"] not in ("source_availability_api", "dissemination_log"):
        reasons.append("publication_or_poll_is_not_exact_availability")
    low, high = None, None
    if availability:
        low, issues = _clock(availability["clock"], "availability")
        reasons += issues
        if low and availability["clock"]["uncertainty_seconds"] > protocol["max_clock_uncertainty_seconds"]:
            reasons.append("availability_clock_uncertainty_exceeds_protocol")
    if acquisition:
        high, issues = _clock(acquisition["completed_clock"], "observation")
        reasons += issues + acquisition.get("clock_reason_codes", [])
        if high and acquisition["completed_clock"]["uncertainty_seconds"] > protocol["max_clock_uncertainty_seconds"]:
            reasons.append("observation_clock_uncertainty_exceeds_protocol")
        if acquisition["cycle_status"] != "complete" or acquisition["cycle_gaps"]:
            reasons.append("acquisition_cycle_incomplete")
        cycle = store.conn.execute("SELECT * FROM source_cycles WHERE cycle_id=?",
                                   (acquisition["source_cycle_id"],)).fetchone()
        if not cycle or cycle["input_hash"] != acquisition["source_cycle_input_hash"]:
            reasons.append("acquisition_cycle_changed_or_missing")
        if not any(acquisition["source_cycle_id"] in c["cycle_ids"] for c in coverages):
            reasons.append("acquisition_cycle_not_in_coverage_receipt")
        # Poll observation is not candidate completion. A separate immutable
        # collector completion log must bind the exact emitted event packet.
        from .prospective import latest_adjudications
        adjudication = next((r for r in latest_adjudications(store, protocol["protocol_id"], at=now)
                             if r["payload"]["unit_id"] == unit["unit_id"]), None)
        if not adjudication:
            reasons.append("reviewed_detection_missing")
            candidate_at = reviewed_at = None
        else:
            refs.append(adjudication["receipt_id"])
            a = adjudication["payload"]
            reviewed_at = a["reviewed_at"]
            if len(a["detections"]) != 1:
                reasons.append("bundled_detection_completion_not_fully_measured")
            candidate_at = candidate["clock"].get("at") if candidate and isinstance(candidate.get("clock"), dict) else None
            if not candidate or not any(d["event_id"] == candidate["event_id"] and
                    d["event_revision"] == candidate["event_revision"] and candidate["native_ref"] in d["native_refs"]
                    for d in a["detections"]):
                reasons.append("candidate_clock_not_bound_to_reviewed_detection")
            if candidate_at is None or not a["detections"]:
                reasons.append("candidate_ready_clock_unavailable")
            try:
                _review(store, a["review_receipt_id"], purpose="prospective_adjudication",
                        subject={k: v for k, v in a.items() if k not in ("review_receipt_id", "submitted_hash")},
                        protocol=protocol, now=now)
                from .review import event_packet
                for detection in a["detections"]:
                    packet = event_packet(store, detection["event_id"], detection["event_revision"])
                    head = store.conn.execute("SELECT revision_id FROM event_heads WHERE event_id=?", (detection["event_id"],)).fetchone()
                    state = packet.get("lifecycle", {})
                    if (not head or head[0] != detection["event_revision"]
                            or digest({"event": packet["event"], "documents": packet["documents"]}) != detection["input_hash"]
                            or digest(state) != detection["lifecycle_hash"] or state.get("hard_reason_codes")
                            or state.get("quarantined_pending_review")):
                        reasons.append("candidate_detection_superseded_or_invalidated")
                    if candidate and digest(packet["event"]) != candidate["packet_hash"]:
                        reasons.append("candidate_packet_hash_changed")
            except (ValueError, KeyError):
                reasons.append("candidate_adjudication_unverified")
            if candidate_at and instant(candidate_at) > instant(reviewed_at):
                reasons.append("review_precedes_candidate")
            if candidate_at and high and instant(candidate_at) < high[0]:
                reasons.append("candidate_precedes_acquisition")
    else:
        candidate_at = reviewed_at = None
    candidate_interval = None
    if candidate:
        candidate_interval, issues = _clock(candidate.get("clock"), "candidate_ready")
        reasons += issues
        if candidate_interval and candidate["clock"]["uncertainty_seconds"] > protocol["max_clock_uncertainty_seconds"]:
            reasons.append("candidate_clock_uncertainty_exceeds_protocol")
        if acquisition and candidate["source_cycle_id"] != acquisition["source_cycle_id"]:
            reasons.append("candidate_and_acquisition_cycles_disagree")
        if high and candidate_interval and high[1] > candidate_interval[0]:
            reasons.append("acquisition_to_candidate_clock_order_uncertain")
        if candidate_interval and reviewed_at and candidate_interval[1] > instant(reviewed_at):
            reasons.append("candidate_completion_after_review")
    if low and high:
        if low[0] > high[1]:
            reasons.append("source_availability_after_observation")
        elif low[1] > high[0]:
            reasons.append("availability_to_acquisition_clock_order_uncertain")
        if low[0] < instant(protocol["window_start"]) or high[1] > instant(protocol["window_end"]):
            reasons.append("timing_outside_prespecified_window")
        if not any(instant(c["window_start"]) <= low[0] <= high[1] <= instant(c["window_end"])
                   and c["kind"] == "feed_interval" for c in coverages):
            reasons.append("timing_interval_not_fully_covered")
    latency = None
    descriptive = None
    if low and candidate_interval:
        lower = (candidate_interval[0] - low[1]).total_seconds()
        upper = (candidate_interval[1] - low[0]).total_seconds()
        descriptive = {"minimum_seconds": max(0, lower), "maximum_seconds": upper}
        if upper < 0:
            reasons.append("negative_latency_interval")
        if not reasons:
            latency = upper
    source_routes = sorted({(c["source_id"], c["route_id"], c["family"]) for c in coverages})
    if len(source_routes) != 1:
        reasons.append("timing_requires_one_exact_source_route_family")
        latency = None
    return {
        "contract": VERSION, "protocol_id": protocol["protocol_id"],
        "unit_id": unit["unit_id"], "origin_id": unit["origin_id"],
        "availability_receipt_id": record.get("availability_receipt_id"),
        "acquisition_receipt_id": record.get("acquisition_receipt_id"),
        "candidate_receipt_id": record.get("candidate_receipt_id"),
        "coverage_receipt_ids": coverage_ids,
        "source_route_family": list(source_routes[0]) if len(source_routes) == 1 else None,
        "source_available_clock": availability.get("clock") if availability else None,
        "publication_clock": availability.get("publication_clock") if availability else None,
        "first_observed_at": acquisition.get("observed_at") if acquisition else None,
        "candidate_ready_at": candidate_at, "review_completed_at": reviewed_at,
        "candidate_ready_clock": candidate.get("clock") if candidate else None,
        "descriptive_latency_interval": descriptive,
        "conservative_latency_seconds": latency if not reasons else None,
        "status": "verified" if not reasons and latency is not None else "descriptive_only",
        "reason_codes": sorted(set(reasons)), "evidence_kind": protocol["evidence_kind"],
        **_bindings(protocol)}, refs


def import_timing(store, protocol_id, registry, policies, record, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    if any(k in record for k in ("continuous_source", "latency_seconds",
                                "source_available_at", "candidate_ready_at")):
        raise ValueError("caller-supplied clocks/latency flags are not operational proof")
    # Replay an original input cannot move a superseded receipt back to head.
    all_rows = [r for r in list_receipts(store, kind=TIMING_KIND)
                if r["payload"]["protocol_id"] == protocol_id]
    for old in all_rows:
        if old["payload"].get("submitted_hash") == digest(record):
            return old
    payload, refs = _calculate(store, protocol, record, now=now)
    superseded = {r["payload"].get("supersedes") for r in all_rows}
    current = [r for r in all_rows if r["payload"]["unit_id"] == payload["unit_id"]
               and r["receipt_id"] not in superseded]
    if len(current) > 1:
        raise ValueError("conflicting timing receipt heads")
    payload["supersedes"] = record.get("supersedes")
    if current and payload["supersedes"] != current[0]["receipt_id"]:
        raise ValueError("timing correction must supersede current receipt")
    if payload["supersedes"] and (not current or payload["supersedes"] != current[0]["receipt_id"]):
        raise ValueError("timing supersession is not the current receipt for this unit")
    payload["submitted_hash"] = digest(record)
    if payload["supersedes"]:
        get_receipt(store, payload["supersedes"], kind=TIMING_KIND)
        refs.append(payload["supersedes"])
    with transaction(store):
        return _append(store, TIMING_KIND, payload, now=now, refs=refs)


def timing_report(store, protocol_id, registry, policies, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    rows = [r for r in list_receipts(store, kind=TIMING_KIND)
            if r["payload"]["protocol_id"] == protocol_id
            and instant(r["observed_at"]) <= instant(now)]
    superseded = {r["payload"].get("supersedes") for r in rows}
    rows = [r for r in rows if r["receipt_id"] not in superseded]
    included = []
    excluded = []
    for receipt in rows:
        original = receipt["payload"]
        try:
            calculated, _ = _calculate(store, protocol, original, now=now)
        except (ValueError, KeyError):
            excluded.append({"unit_id": original["unit_id"], "reason_codes": ["timing_references_no_longer_valid"]})
            continue
        if calculated["status"] != "verified":
            excluded.append({"unit_id": original["unit_id"], "reason_codes": calculated["reason_codes"]})
        elif protocol["evidence_kind"] != "operator_evidence" or protocol["partition"] != "prospective":
            excluded.append({"unit_id": original["unit_id"], "reason_codes": ["not_real_prospective_timing"]})
        else:
            included.append(calculated)
    closed = _closed(store, protocol_id, at=now)
    selected = closed["payload"]["selected"] if closed else []
    strata = []
    for scope in protocol["source_scope"]:
        key = [scope[k] for k in ("source_id", "route_id", "family")]
        expected = {u["unit_id"] for u in selected if any(
            all(o[k] == scope[k] for k in ("source_id", "route_id", "family"))
            for o in u["observations"])}
        samples = [r for r in included if r["source_route_family"] == key]
        latencies = sorted(r["conservative_latency_seconds"] for r in samples)
        p95 = latencies[max(0, math.ceil(.95 * len(latencies)) - 1)] if latencies else None
        missing = sorted(expected - {r["unit_id"] for r in samples})
        reasons = []
        if not scope["timing_required"]:
            reasons.append("source_not_prespecified_for_operational_timing")
        if len(samples) < protocol["minimum_timing_per_stratum"]:
            reasons.append("timing_stratum_sample_insufficient")
        if missing:
            reasons.append("timing_observations_missing_or_excluded")
        if p95 is None or p95 > 2 * protocol["poll_interval_seconds"]:
            reasons.append("timing_p95_unavailable_or_above_threshold")
        strata.append({"source_id": key[0], "route_id": key[1], "family": key[2],
                       "n_expected": len(expected), "n": len(samples),
                       "minimum_required": protocol["minimum_timing_per_stratum"],
                       "p95_seconds": p95, "missing_unit_ids": missing,
                       "reason_codes": reasons, "status": "eligible_for_review" if not reasons else "pending"})
    values = sorted(r["conservative_latency_seconds"] for r in included)
    return {"contract": VERSION, "n": len(values),
            "p95_seconds": values[max(0, math.ceil(.95 * len(values)) - 1)] if values else None,
            "strata": strata, "excluded": excluded,
            "status": "eligible_for_review" if strata and all(
                s["status"] == "eligible_for_review" for s in strata) else "pending",
            "definition": "upper uncertainty bound of source availability to exact emitted-packet completion proof",
            "automatic_activation": False}
