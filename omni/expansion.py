"""Version-bound expansion proposals and measurements from referenced records.

This workflow reads current coverage and immutable evidence. It has no membership
writer, collector fan-out, source approval or signal-eligibility operation.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import re

from .attestations import validate_attestation
from .identities import digest, instant
from .receipts import get_receipt, list_receipts, put_receipt
from .verticals.common import decimal_string, nonempty

VERSION = "omni-expansion-1"
PROPOSAL_KIND = "expansion_proposal"
OBSERVATION_KIND = "expansion_observation"
REPORT_KIND = "expansion_report"
CATEGORIES = {"reviewer_labor", "operator_labor", "vendor", "compute_storage", "other"}
TRUST_BOUNDARY = ("Local hashes, references and typed attestations validate supplied evidence consistency; "
                  "they do not authenticate a human, establish source rights or create real observations.")


def _list(values, name, *, empty=False, duplicates=False):
    if (not isinstance(values, list) or len(values) > 10000 or (not values and not empty)
            or any(not nonempty(v) for v in values) or (not duplicates and len(values) != len(set(values)))):
        raise ValueError("invalid " + name)
    return values


def _bindings(proposal):
    return {key: proposal[key] for key in ("implementation_hash", "policy_hash", "source_scope_hash",
                                          "registry_fingerprint", "dependency_locks_hash") if key in proposal}


def _external(value):
    if (not isinstance(value, dict) or not nonempty(value.get("reference"), maximum=2000)
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", "")))):
        raise ValueError("external evidence reference and hash required")


def _coverage(coverage):
    _list(coverage, "current coverage", empty=True)
    return {"contract": "omni-expansion-coverage-snapshot-1", "members": sorted(coverage),
            "version": "editorial-coverage:" + digest(sorted(coverage))}


def proposal_request(store, protocol_id, registry, policies, instruments, current_coverage, candidates,
                     *, now, minimum_independent_cases=100):
    """Freeze an operator list against current identities before observations."""
    from .prospective import protocol_state, current_bindings
    protocol = protocol_state(store, protocol_id, registry, policies)
    if not instant(now) < instant(protocol["window_start"]):
        raise ValueError("expansion proposal must precede the observation window")
    coverage = _coverage(current_coverage)
    if sorted(protocol["captured_coverage"]) != coverage["members"]:
        raise ValueError("protocol current coverage differs from expansion baseline")
    _list(candidates, "explicit candidate list", empty=True, duplicates=True)
    if type(minimum_independent_cases) is not int or not 1 <= minimum_independent_cases <= 10000:
        raise ValueError("invalid prespecified sample floor")
    requested = list(dict.fromkeys(candidates))
    additional = sorted(set(requested) - set(current_coverage))
    identities, unresolved = {}, []
    for symbol in sorted(set(current_coverage) | set(additional)):
        resolution = instruments.resolve(symbol, on=protocol["window_start"][:10], known_by=now)
        identity = {"symbol": symbol, "instrument_id": resolution.get("instrument_id"),
                    "issuer_id": resolution.get("issuer_id"), "listing": resolution.get("listing"),
                    "instrument_registry_fingerprint": instruments.fingerprint(), "known_by": now,
                    "effective_on": protocol["window_start"][:10], "status": resolution["status"]}
        issuer = identity["issuer_id"]
        if (resolution["status"] != "resolved" or issuer not in registry.entities
                or registry.entities[issuer].get("kind") != "issuer"
                or not (identity.get("listing") or {}).get("evidence")):
            unresolved.append(symbol)
        identities[symbol] = identity
    current = current_bindings(registry, policies)
    base = {"protocol_id": protocol_id, "window_start": protocol["window_start"], "window_end": protocol["window_end"],
            "source_scope_definition_hash": digest(protocol["source_scope"]), **current}
    scope = lambda symbols: digest({**base, "members": sorted(symbols), "instrument_registry_fingerprint": instruments.fingerprint()})
    return {"contract": VERSION, **base, "coverage_snapshot": coverage, "coverage_snapshot_hash": digest(coverage),
            "requested_candidates": list(candidates), "candidate_symbols": additional,
            "already_covered": sorted(set(requested) & set(current_coverage)),
            "duplicate_candidates_removed": len(candidates) - len(requested), "identities": identities,
            "identity_unresolved": unresolved, "instrument_registry_fingerprint": instruments.fingerprint(),
            "baseline_scope_hash": scope(current_coverage), "expanded_scope_hash": scope([*current_coverage, *additional]),
            "minimum_independent_cases": minimum_independent_cases, "frozen_at": now,
            "evidence_kind": protocol["evidence_kind"], "human_review_status": "pending",
            "automatic_membership_change": False, "automatic_activation": False, "signal_eligible": False,
            "trust_boundary": TRUST_BOUNDARY}


def create_proposal(store, protocol_id, registry, policies, instruments, current_coverage, candidates, *, now,
                    minimum_independent_cases=100):
    payload = proposal_request(store, protocol_id, registry, policies, instruments, current_coverage, candidates,
                               now=now, minimum_independent_cases=minimum_independent_cases)
    return put_receipt(store, PROPOSAL_KIND, payload, observed_at=now, refs=[protocol_id])


def _proposal(store, proposal_id):
    result = get_receipt(store, proposal_id, kind=PROPOSAL_KIND)
    if result["payload"].get("contract") != VERSION:
        raise ValueError("unsupported expansion proposal")
    return result["payload"]


def measurement_context(store, proposal_id, arm, registry, policies, current_coverage,
                        source_id, route_id, now, *, family=None, instruments=None,
                        allow_synthetic=False):
    """Bind an existing source-first collector run to a frozen comparison arm.

    This read-only context supplies neither source permission nor request counts.
    The collector must compare its actual query plan with ``query_hash``, retain
    every native source row, and add its independently counted request attempts.
    Synthetic contexts require an explicit offline-test opt-in.
    """
    from .prospective import protocol_state, protocol_request
    if type(allow_synthetic) is not bool:
        raise ValueError("synthetic measurement opt-in must be boolean")
    if arm not in ("baseline", "expanded"):
        raise ValueError("explicit baseline or expanded arm required")
    if not nonempty(source_id) or not nonempty(route_id) or (family is not None and not nonempty(family)):
        raise ValueError("exact source, route and optional family required")
    if instruments is None:
        from instrument_registry import load_registry as load_instruments
        instruments = load_instruments()
    original = get_receipt(store, proposal_id, kind=PROPOSAL_KIND)
    proposal = _proposal(store, proposal_id)
    protocol = protocol_state(store, proposal["protocol_id"], registry, policies)
    if (proposal["evidence_kind"] not in ({"operator_evidence", "synthetic"} if allow_synthetic else {"operator_evidence"})
            or proposal["evidence_kind"] != protocol["evidence_kind"] or protocol["partition"] != "prospective"):
        raise ValueError("actual prospective measurement context required; synthetic is offline-only")
    if not instant(proposal["window_start"]) <= instant(now) < instant(proposal["window_end"]):
        raise ValueError("measurement time must be inside the frozen comparison window")
    if (not instant(protocol["frozen_at"]) <= instant(proposal["frozen_at"]) < instant(proposal["window_start"])
            or instant(original["observed_at"]) > instant(now)):
        raise ValueError("measurement protocol and proposal must be frozen before collection")
    if digest(_coverage(current_coverage)) != proposal["coverage_snapshot_hash"]:
        raise ValueError("current coverage differs from the frozen comparison baseline")
    if proposal["instrument_registry_fingerprint"] != instruments.fingerprint():
        raise ValueError("instrument identity version changed")
    reproduced = proposal_request(store, proposal["protocol_id"], registry, policies, instruments,
        proposal["coverage_snapshot"]["members"], proposal["requested_candidates"], now=proposal["frozen_at"],
        minimum_independent_cases=proposal["minimum_independent_cases"])
    if reproduced != proposal or original["observed_at"] != proposal["frozen_at"] or original["refs"] != [proposal["protocol_id"]]:
        raise ValueError("expansion proposal cannot be reproduced from its frozen source and identity evidence")
    if proposal["identity_unresolved"] or not proposal["candidate_symbols"]:
        raise ValueError("measurement needs a resolved nonempty candidate set difference")
    subject = {k: v for k, v in protocol.items() if k not in ("protocol_id", "review_receipt_id", "frozen_at")}
    if protocol_request(registry, policies, protocol["captured_coverage"], protocol,
                        now=protocol["requested_at"]) != subject:
        raise ValueError("frozen source protocol cannot be reproduced")
    review = validate_attestation(store, protocol["review_receipt_id"], purpose="prospective_protocol",
                                  subject_hash=digest(subject), bindings=_bindings(proposal), now=now,
                                  allow_synthetic=allow_synthetic)
    protocol_envelope = get_receipt(store, proposal["protocol_id"], kind="prospective_protocol")
    if (review["payload"]["evidence_kind"] != proposal["evidence_kind"]
            or instant(review["observed_at"]) > instant(protocol["frozen_at"])
            or protocol_envelope["refs"] != [review["receipt_id"]]):
        raise ValueError("frozen protocol review does not match its exact evidence and recording clock")
    matches = [row for row in protocol["source_scope"] if row["source_id"] == source_id
               and row["route_id"] == route_id and (family is None or row["family"] == family)]
    if len(matches) != 1:
        raise ValueError("measurement requires one exact frozen source/route/family stratum")
    scope = matches[0]
    return {"expansion_scope_hash": proposal[arm + "_scope_hash"],
            "expansion_proposal_id": proposal_id, "expansion_arm": arm,
            "sampling_family": scope["family"], "query_hash": scope["query_hash"],
            "expansion_measurement_context": {
                "contract": "omni-expansion-measurement-context-1", "protocol_id": proposal["protocol_id"],
                "window_start": proposal["window_start"], "window_end": proposal["window_end"],
                "source_id": source_id, "route_id": route_id, "source_scope": deepcopy(scope),
                "bindings": _bindings(proposal), "coverage_snapshot_hash": proposal["coverage_snapshot_hash"],
                "instrument_registry_fingerprint": proposal["instrument_registry_fingerprint"],
                "observed_at": now, "evidence_kind": proposal["evidence_kind"],
                "human_review_status": "pending", "automatic_membership_change": False,
                "automatic_activation": False, "signal_eligible": False}}


def _records(store, proposal_id):
    return [r for r in list_receipts(store, kind=OBSERVATION_KIND) if r["payload"].get("proposal_id") == proposal_id]


def _cycle(store, cycle_id, proposal):
    row = store.conn.execute("SELECT * FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
    if not row:
        raise ValueError("source cycle missing")
    record = dict(row)
    if not instant(proposal["window_start"]) <= instant(row["observed_at"]) <= instant(proposal["window_end"]):
        raise ValueError("source cycle outside comparison window")
    return record


def observation_request(store, proposal_id, record, registry, policies, *, now, _existing_receipt_id=None):
    """Validate detailed assessments/attempt logs; no caller-supplied totals."""
    from .prospective import protocol_state, latest_adjudications
    proposal = _proposal(store, proposal_id)
    protocol = protocol_state(store, proposal["protocol_id"], registry, policies)
    if not isinstance(record, dict):
        raise ValueError("expansion observation must be an object")
    kind = record.get("kind")
    if kind not in ("event_assessment", "request_attempt", "request_census"):
        raise ValueError("unsupported expansion observation kind")
    if not nonempty(record.get("observation_id")):
        raise ValueError("observation identity required")
    _external(record.get("external_reference"))
    if record.get("evidence_kind") != proposal["evidence_kind"]:
        raise ValueError("observation evidence kind differs from frozen protocol")
    observed_at = record.get("observed_at")
    if not instant(proposal["window_start"]) <= instant(observed_at) <= instant(now):
        raise ValueError("observation clock outside permitted ordering")
    result = {"contract": VERSION, "proposal_id": proposal_id, "kind": kind, "observation_id": record["observation_id"],
              "observed_at": observed_at, "external_reference": deepcopy(record["external_reference"]),
              "evidence_kind": record["evidence_kind"], "supersedes": record.get("supersedes"), **_bindings(proposal)}
    refs = [proposal_id]
    if kind == "event_assessment":
        adjudication = get_receipt(store, record.get("adjudication_ref"), kind="prospective_adjudication")
        source = adjudication["payload"]
        heads = {r["receipt_id"] for r in latest_adjudications(store, proposal["protocol_id"], at=now)}
        if source.get("protocol_id") != proposal["protocol_id"] or adjudication["receipt_id"] not in heads:
            raise ValueError("assessment requires a current matching source adjudication")
        if instant(source["reviewed_at"]) > instant(observed_at):
            raise ValueError("usefulness assessment cannot precede source adjudication")
        if any(type(record.get(k)) is not bool for k in ("useful", "mapping_error")):
            raise ValueError("assessment needs explicit useful/mapping decisions")
        state = record.get("processing_state")
        if state not in ("detected", "abstained", "backlog", "not_detected") or (state == "detected") != source["detected"]:
            raise ValueError("processing state conflicts with source detection evidence")
        if record["useful"] and not (source["event_present"] and source["detected"] and source["correct"]
                                     and source["meaningfully_exercised"] and not source["critical_error"] and not record["mapping_error"]):
            raise ValueError("useful event contradicts source adjudication")
        result.update(adjudication_ref=adjudication["receipt_id"], unit_id=source["unit_id"], origin_id=source["origin_id"],
                      useful=record["useful"], mapping_error=record["mapping_error"], processing_state=state)
        refs.append(adjudication["receipt_id"])
    else:
        arm = record.get("arm")
        if arm not in ("baseline", "expanded"):
            raise ValueError("explicit baseline or expanded arm required")
        result.update(arm=arm, scope_hash=proposal[arm + "_scope_hash"], window_start=proposal["window_start"], window_end=proposal["window_end"])
        if kind == "request_attempt":
            if (not all(nonempty(record.get(k)) for k in ("request_id", "attempt_id", "source_cycle_id", "route_id"))
                    or type(record.get("attempt_number")) is not int or not 1 <= record["attempt_number"] <= 30
                    or record.get("method") not in ("GET", "POST")):
                raise ValueError("bounded exact request attempt required")
            cycle = _cycle(store, record["source_cycle_id"], proposal)
            if not any(s["source_id"] == cycle["source_id"] and s["route_id"] == record["route_id"] for s in protocol["source_scope"]):
                raise ValueError("attempt route outside frozen source scope")
            start, end = instant(record["started_at"]), instant(record["completed_at"])
            if not instant(proposal["window_start"]) <= start <= end <= min(instant(proposal["window_end"]), instant(observed_at)):
                raise ValueError("attempt outside aligned observation window")
            status = record.get("http_status")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError("invalid observed HTTP status")
            result.update({k: record[k] for k in ("request_id", "attempt_id", "source_cycle_id", "route_id", "attempt_number", "method", "started_at", "completed_at")})
            result.update(http_status=status, source_id=cycle["source_id"], source_cycle_hash=digest(cycle))
        else:
            if instant(observed_at) < instant(proposal["window_end"]):
                raise ValueError("request census cannot close before the comparison window ends")
            attempts = _list(record.get("attempt_refs"), "attempt inventory", empty=True)
            cycles = _list(record.get("cycle_ids"), "cycle inventory", empty=True)
            for ref in attempts:
                attempt = get_receipt(store, ref, kind=OBSERVATION_KIND)["payload"]
                if attempt.get("proposal_id") != proposal_id or attempt.get("kind") != "request_attempt" or attempt.get("arm") != arm:
                    raise ValueError("request census contains foreign attempts")
                if instant(attempt["observed_at"]) > instant(observed_at):
                    raise ValueError("request census precedes an observed attempt")
            for cycle in cycles:
                _cycle(store, cycle, proposal)
            result.update(attempt_refs=sorted(attempts), cycle_ids=sorted(cycles))
            refs.extend(attempts)
    if result["supersedes"]:
        old = get_receipt(store, result["supersedes"], kind=OBSERVATION_KIND)["payload"]
        subject_key = "unit_id" if kind == "event_assessment" else "attempt_id" if kind == "request_attempt" else "arm"
        if old.get("proposal_id") != proposal_id or old.get("kind") != kind or old.get(subject_key) != result.get(subject_key):
            raise ValueError("observation supersession has a different subject")
        if any(r["payload"].get("supersedes") == result["supersedes"] and r["receipt_id"] != _existing_receipt_id
               for r in _records(store, proposal_id)):
            raise ValueError("observation supersession is not current")
        refs.append(result["supersedes"])
    return result, sorted(set(refs))


def record_observation(store, proposal_id, record, registry, policies, *, now):
    if not isinstance(record, dict):
        raise ValueError("expansion observation must be an object")
    existing = _records(store, proposal_id)
    submitted = digest(record)
    for old in existing:
        if old["payload"].get("submitted_hash") == submitted:
            return old
        if old["payload"]["observation_id"] == record.get("observation_id"):
            raise ValueError("observation ID reused with different evidence")
    subject, refs = observation_request(store, proposal_id, record, registry, policies, now=now)
    proposal = _proposal(store, proposal_id)
    review = validate_attestation(store, record.get("attestation_ref"), purpose="expansion_observation",
                                  subject_hash=digest(subject), bindings={"proposal_id": proposal_id, **_bindings(proposal)},
                                  now=now, allow_synthetic=proposal["evidence_kind"] == "synthetic")
    if review["payload"]["evidence_kind"] != proposal["evidence_kind"]:
        raise ValueError("observation and attestation evidence kinds differ")
    payload = {**subject, "attestation_ref": review["receipt_id"], "submitted_hash": submitted}
    return put_receipt(store, OBSERVATION_KIND, payload, observed_at=now, refs=[*refs, review["receipt_id"]])


def _valid_observations(store, proposal_id, proposal, registry, policies, *, now):
    valid, excluded = [], []
    records = _records(store, proposal_id)
    superseded = {r["payload"].get("supersedes") for r in records}
    for envelope in records:
        if envelope["receipt_id"] in superseded:
            continue
        row = envelope["payload"]
        subject = {k: v for k, v in row.items() if k not in ("attestation_ref", "submitted_hash")}
        try:
            if row.get("contract") != VERSION or any(row.get(k) != v for k, v in _bindings(proposal).items()):
                raise ValueError("observation version mismatch")
            if instant(envelope["observed_at"]) > instant(now):
                raise ValueError("observation was imported after report time")
            normalized, refs = observation_request(store, proposal_id, row, registry, policies, now=now,
                                                  _existing_receipt_id=envelope["receipt_id"])
            if normalized != subject or set(envelope["refs"]) != set(refs + [row["attestation_ref"]]):
                raise ValueError("observation subject or references do not match current normalized evidence")
            attestation = validate_attestation(store, row["attestation_ref"], purpose="expansion_observation", subject_hash=digest(subject),
                                 bindings={"proposal_id": proposal_id, **_bindings(proposal)}, now=now,
                                 allow_synthetic=proposal["evidence_kind"] == "synthetic")
            if attestation["payload"]["evidence_kind"] != proposal["evidence_kind"]:
                raise ValueError("observation and attestation evidence kinds differ")
            if row["kind"] == "request_attempt" and digest(_cycle(store, row["source_cycle_id"], proposal)) != row["source_cycle_hash"]:
                raise ValueError("source cycle changed")
            _external(row.get("external_reference"))
        except (ValueError, KeyError, TypeError) as exc:
            excluded.append({"receipt_id": envelope["receipt_id"], "reason": str(exc)})
        else:
            valid.append(envelope)
    return valid, excluded


def _ratio(successes, total, *, complete=True):
    value = successes / total if total else None
    return {"successes": successes, "n": total, "estimate": value if complete else None,
            "observed_estimate": value, "complete": complete}


def _decimal_sum(values):
    with localcontext() as context:
        context.prec = 40
        return sum(values, Decimal(0))


def _requests(store, proposal, observations, arm):
    attempts = [r for r in observations if r["payload"]["kind"] == "request_attempt" and r["payload"]["arm"] == arm]
    censuses = [r for r in observations if r["payload"]["kind"] == "request_census" and r["payload"]["arm"] == arm]
    issues, groups = [], defaultdict(list)
    attempt_ids, external = set(), set()
    for receipt in attempts:
        row = receipt["payload"]
        if row["attempt_id"] in attempt_ids or row["external_reference"]["sha256"] in external:
            issues.append("duplicate_request_attempt_or_source_evidence")
        attempt_ids.add(row["attempt_id"]); external.add(row["external_reference"]["sha256"])
        groups[row["request_id"]].append(row)
    for values in groups.values():
        ordered = sorted(values, key=lambda r: r["attempt_number"])
        if [r["attempt_number"] for r in ordered] != list(range(1, len(ordered) + 1)):
            issues.append("retry_chain_incomplete")
        if len({(r["source_id"], r["route_id"], r["method"], r["source_cycle_id"]) for r in values}) != 1:
            issues.append("retry_chain_scope_mismatch")
        if any(instant(a["completed_at"]) > instant(b["started_at"]) for a, b in zip(ordered, ordered[1:])):
            issues.append("retry_chain_time_mismatch")
    if len(censuses) != 1:
        issues.append("request_census_missing_or_ambiguous")
    else:
        census = censuses[0]["payload"]
        if set(census["attempt_refs"]) != {r["receipt_id"] for r in attempts}:
            issues.append("request_census_incomplete")
        cycles = {r["payload"]["source_cycle_id"] for r in attempts}
        if not cycles <= set(census["cycle_ids"]):
            issues.append("request_cycles_missing_from_census")
        for cycle_id in census["cycle_ids"]:
            try:
                cycle = _cycle(store, cycle_id, proposal)
                metrics = json.loads(cycle["health"]).get("metrics", {})
                counts = metrics.get("request_counts", {})
                if not isinstance(metrics, dict) or not isinstance(counts, dict):
                    raise ValueError("invalid request telemetry")
            except (ValueError, TypeError, AttributeError):
                issues.append("source_cycle_or_telemetry_unavailable")
                continue
            observed = len([r for r in attempts if r["payload"]["source_cycle_id"] == cycle_id])
            if metrics.get("expansion_scope_hash") != proposal[arm + "_scope_hash"]:
                issues.append("source_cycle_comparison_scope_missing_or_mismatched")
            if type(counts.get("attempt")) is not int:
                issues.append("source_attempt_counter_missing")
            elif counts["attempt"] != observed:
                issues.append("attempt_log_and_cycle_counter_disagree")
    return {"requests": len(groups) if not issues else None, "attempts": len(attempts) if not issues else None,
            "retries": sum(r["payload"]["attempt_number"] > 1 for r in attempts) if not issues else None,
            "recorded_attempts": len(attempts), "reason_codes": sorted(set(issues)),
            "cycle_ids": sorted({r["payload"]["source_cycle_id"] for r in attempts}),
            "attempt_ids": sorted(attempt_ids), "source_evidence_hashes": sorted(external)}


def _operating(store, proposal, arm, *, now):
    scope_hash = proposal[arm + "_scope_hash"]
    candidates = [r for r in list_receipts(store, kind="operating_observation")
                  if r["payload"].get("scope_hash") == scope_hash]
    valid, reasons = [], []
    try:
        from .operating import validate_record
    except ImportError:
        return {"cost": None, "currency": None, "review_seconds": None, "record_refs": [],
                "reason_codes": ["operating_validation_unavailable"]}
    try:
        from .operating import cost_components
    except ImportError:
        cost_components = None
    for envelope in candidates:
        row = envelope["payload"]
        if row.get("kind") not in ("cost", "run_cost", "review_effort", "cost_completeness"):
            continue
        if any(row.get(k) != proposal[k] for k in ("window_start", "window_end", "implementation_hash", "policy_hash")):
            reasons.append("operating_window_or_version_mismatch")
            continue
        try:
            valid.append(validate_record(store, envelope["receipt_id"], now=now,
                                         allow_synthetic=proposal["evidence_kind"] == "synthetic"))
        except (ValueError, KeyError, TypeError):
            reasons.append("operating_record_invalid")
    ids, hashes = set(), set()
    for envelope in valid:
        row = envelope["payload"]
        if row["observation_id"] in ids or row["external_reference"]["sha256"] in hashes:
            reasons.append("duplicate_operating_observation_or_evidence")
        ids.add(row["observation_id"]); hashes.add(row["external_reference"]["sha256"])
    costs = [r for r in valid if r["payload"]["kind"] in ("cost", "run_cost")]
    run_ids = [r["payload"]["run_id"] for r in costs if r["payload"]["kind"] == "run_cost"]
    if len(run_ids) != len(set(run_ids)):
        reasons.append("duplicate_run_allocation_id")
    census = [r for r in valid if r["payload"]["kind"] == "cost_completeness"]
    components = []
    for envelope in costs:
        row = envelope["payload"]
        try:
            if cost_components is not None:
                components.append(cost_components(row))
            elif row["kind"] == "cost":
                amount = decimal_string(row.get("amount"), signed=row.get("category") in {"vendor", "compute_storage", "other"})
                if amount is None:
                    raise ValueError("unknown amount")
                components.append({row["category"]: Decimal(amount)})
            else:
                reasons.append("run_cost_component_validator_unavailable")
        except (ValueError, KeyError, TypeError):
            reasons.append("cost_amount_or_components_unknown")
    if {category for row in components for category in row} != CATEGORIES:
        reasons.append("cost_category_observations_incomplete")
    if len(census) != 1 or set(census[0]["payload"].get("covered_categories", [])) != CATEGORIES:
        reasons.append("complete_cost_categories_missing")
    elif set(census[0]["payload"].get("cost_refs", [])) != {r["receipt_id"] for r in costs}:
        reasons.append("cost_inventory_mismatch")
    currencies = {r["payload"].get("currency") for r in costs}
    if len(currencies) != 1 or None in currencies:
        reasons.append("cost_currency_missing_or_mixed")
    effort_rows = [r["payload"] for r in valid if r["payload"]["kind"] == "review_effort"]
    effort = [r["elapsed_seconds"] for r in effort_rows]
    if len({r["external_reference"]["sha256"] for r in effort_rows}) != len(effort_rows):
        reasons.append("duplicate_review_effort_evidence")
    if not effort:
        reasons.append("review_effort_missing")
    cost_issues = [x for x in reasons if x != "review_effort_missing"]
    return {"cost": format(_decimal_sum(value for row in components for value in row.values()), "f") if not cost_issues else None,
            "category_costs": {category: format(_decimal_sum(row[category] for row in components if category in row), "f")
                               for category in sorted(CATEGORIES)} if not cost_issues else None,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "review_seconds": format(_decimal_sum(Decimal(str(x)) for x in effort), "f")
                if effort and "duplicate_review_effort_evidence" not in reasons else None,
            "record_refs": [r["receipt_id"] for r in valid], "source_evidence_hashes": sorted(hashes),
            "run_ids": sorted(set(run_ids)),
            "reason_codes": sorted(set(reasons))}


def expansion_report(store, proposal_id, registry, policies, instruments, current_coverage, *, now,
                     wave_a_review_ref=None, candidate_review_ref=None, identity_review_refs=None, persist=False):
    """Derive measurements from current validated references; never expand a list."""
    from .prospective import evaluation_report, latest_adjudications, current_bindings, protocol_state
    proposal = _proposal(store, proposal_id)
    reasons = []
    current = current_bindings(registry, policies)
    if any(proposal.get(k) != v for k, v in current.items()):
        reasons.append("confounded_implementation_registry_or_policy_change")
    if proposal["instrument_registry_fingerprint"] != instruments.fingerprint():
        reasons.append("instrument_identity_version_changed")
    if digest(_coverage(current_coverage)) != proposal["coverage_snapshot_hash"]:
        reasons.append("current_coverage_changed")
    if (all(proposal.get(k) == v for k, v in current.items())
            and proposal["instrument_registry_fingerprint"] == instruments.fingerprint()):
        original = get_receipt(store, proposal_id, kind=PROPOSAL_KIND)
        reproduced = proposal_request(store, proposal["protocol_id"], registry, policies, instruments,
            proposal["coverage_snapshot"]["members"], proposal["requested_candidates"], now=proposal["frozen_at"],
            minimum_independent_cases=proposal["minimum_independent_cases"])
        if reproduced != proposal or original["observed_at"] != proposal["frozen_at"] or original["refs"] != [proposal["protocol_id"]]:
            raise ValueError("expansion proposal cannot be reproduced from its frozen source and identity evidence")
    if instant(now) < instant(proposal["window_end"]):
        reasons.append("observation_window_not_elapsed")
    source_report = evaluation_report(store, proposal["protocol_id"], registry, policies, now=now)
    protocol = protocol_state(store, proposal["protocol_id"], require_current=False)
    if any(protocol.get(k) != proposal.get(k) for k in ("window_start", "window_end", *current.keys())):
        reasons.append("source_protocol_window_or_version_mismatch")
    reasons.extend("source:" + x for x in source_report.get("pending_reason_codes", []))
    if source_report.get("manual_pilot", {}).get("status") != "eligible_for_review":
        reasons.append("wave_a_manual_pilot_not_eligible")
    if source_report.get("recall", {}).get("status") != "eligible_for_review":
        reasons.append("source_recall_not_eligible")
    inventory = source_report["inventory"]
    if not inventory["closed"] or inventory["pending"]:
        reasons.append("source_inventory_or_adjudication_incomplete")
    invalid = {x["unit_id"] for x in inventory["invalidated_adjudications"]}
    adjudications = {r["payload"]["unit_id"]: r for r in latest_adjudications(store, proposal["protocol_id"], at=now)
                    if r["payload"]["unit_id"] not in invalid}
    observations, excluded = _valid_observations(store, proposal_id, proposal, registry, policies, now=now)
    if excluded:
        reasons.append("measurement_records_invalidated")
    assessments, assessment_groups, external_units = {}, defaultdict(list), defaultdict(set)
    for envelope in observations:
        row = envelope["payload"]
        if row["kind"] != "event_assessment":
            continue
        unit = row["unit_id"]
        if unit not in adjudications or row["adjudication_ref"] != adjudications[unit]["receipt_id"]:
            reasons.append("assessment_adjudication_superseded_or_invalid")
            continue
        assessment_groups[unit].append(row)
        external_units[row["external_reference"]["sha256"]].add(unit)
    duplicate_units = {u for u, values in assessment_groups.items() if len(values) > 1}
    duplicate_units.update(u for values in external_units.values() if len(values) > 1 for u in values)
    if duplicate_units:
        reasons.append("duplicate_assessment_or_source_evidence")
    assessments = {u: rows[0] for u, rows in assessment_groups.items() if u not in duplicate_units}
    missing_assessments = sorted(set(adjudications) - set(assessments))
    if missing_assessments:
        reasons.append("useful_event_assessments_incomplete")
    baseline_issuers = {proposal["identities"][s]["issuer_id"] for s in proposal["coverage_snapshot"]["members"]}
    candidate_issuers = {proposal["identities"][s]["issuer_id"] for s in proposal["candidate_symbols"]}
    baseline_issuers.discard(None); candidate_issuers.discard(None)
    if proposal["identity_unresolved"]:
        reasons.append("issuer_security_identity_unresolved")
    buckets = {k: [] for k in ("baseline", "incremental_candidate", "shared", "unknown_issuer", "other")}
    for unit_id, receipt in adjudications.items():
        row = receipt["payload"]
        issuers = set(row["issuer_ids"])
        base, extra = bool(issuers & baseline_issuers), bool(issuers & candidate_issuers)
        name = "unknown_issuer" if row["unknown_issuer"] else "shared" if base and extra else "baseline" if base else "incremental_candidate" if extra else "other"
        buckets[name].append(row)
    baseline_origins = {r["origin_id"] for r in buckets["baseline"] + buckets["shared"]}
    overlaps = [r for r in buckets["incremental_candidate"] if r["origin_id"] in baseline_origins]
    buckets["shared"].extend(overlaps)
    buckets["incremental_candidate"] = [r for r in buckets["incremental_candidate"] if r["origin_id"] not in baseline_origins]
    unique_origins = {r["origin_id"] for group in buckets.values() for r in group}
    candidate_rows = buckets["incremental_candidate"]
    candidate_origins = {r["origin_id"] for r in candidate_rows}
    if len(candidate_origins) < proposal["minimum_independent_cases"]:
        reasons.append("candidate_independent_sample_below_prespecified_floor")
    if len(unique_origins) < len(adjudications):
        reasons.append("correlated_event_units_share_assertion_origins")
    useful_groups, base_groups = defaultdict(list), defaultdict(list)
    for rows, groups in ((candidate_rows, useful_groups), (buckets["baseline"] + buckets["shared"], base_groups)):
        for row in rows:
            if assessments.get(row["unit_id"], {}).get("useful") is True:
                groups[row["origin_id"]].append(row["unit_id"])
    useful = sorted(min(units) for units in useful_groups.values())
    useful_base = sorted(min(units) for units in base_groups.values())
    source_complete = bool(inventory["closed"] and not inventory["pending"] and not invalid
                           and not any(any(term in r for term in ("inventory", "coverage", "origin_correspondence"))
                                       for r in source_report.get("pending_reason_codes", [])))
    assessments_complete = source_complete and not missing_assessments
    truth = [r["payload"] for r in adjudications.values() if r["payload"]["event_present"]]
    recall = {}
    for name, group in (("overall", truth), ("unknown_issuers", [r for r in truth if r["unknown_issuer"]]),
                        ("negative_outcomes", [r for r in truth if r["negative_outcome"]])):
        recall[name] = {**_ratio(sum(r["detected"] for r in group), len(group), complete=source_complete),
                        "misses": sorted(r["unit_id"] for r in group if not r["detected"])}
        if not group:
            reasons.append(name + "_recall_denominator_missing")
    candidate_counts = {state: sum(assessments.get(r["unit_id"], {}).get("processing_state") == state for r in candidate_rows)
                        for state in ("detected", "abstained", "backlog", "not_detected")}
    candidate_counts["unassessed"] = sum(r["unit_id"] not in assessments for r in candidate_rows)
    baseline_rows = buckets["baseline"] + buckets["shared"]
    baseline_counts = {state: sum(assessments.get(r["unit_id"], {}).get("processing_state") == state for r in baseline_rows)
                       for state in ("detected", "abstained", "backlog", "not_detected")}
    baseline_counts["unassessed"] = sum(r["unit_id"] not in assessments for r in baseline_rows)
    critical_errors = sorted(r["payload"]["unit_id"] for r in adjudications.values() if r["payload"]["critical_error"])
    if critical_errors:
        reasons.append("critical_errors_observed")
    if any(a["mapping_error"] for a in assessments.values()):
        reasons.append("mapping_errors_observed")
    requests = {arm: _requests(store, proposal, observations, arm) for arm in ("baseline", "expanded")}
    economics = {arm: _operating(store, proposal, arm, now=now) for arm in ("baseline", "expanded")}
    for arm in ("baseline", "expanded"):
        reasons.extend(arm + ":" + x for x in requests[arm]["reason_codes"] + economics[arm]["reason_codes"])
    for field in ("cycle_ids", "attempt_ids", "source_evidence_hashes"):
        if set(requests["baseline"].get(field, [])) & set(requests["expanded"].get(field, [])):
            reasons.append("comparison_arms_reuse_request_evidence")
    if set(economics["baseline"].get("source_evidence_hashes", [])) & set(economics["expanded"].get("source_evidence_hashes", [])):
        reasons.append("comparison_arms_reuse_cost_evidence")
    if set(economics["baseline"].get("run_ids", [])) & set(economics["expanded"].get("run_ids", [])):
        reasons.append("comparison_arms_reuse_cost_run")
    cost_delta = unit_cost = None
    if (economics["baseline"]["cost"] is not None and economics["expanded"]["cost"] is not None
            and economics["baseline"]["currency"] == economics["expanded"]["currency"]
            and not any(x.startswith("confounded") or "mismatch" in x or "reuse" in x
                        or x in ("current_coverage_changed", "instrument_identity_version_changed", "observation_window_not_elapsed")
                        for x in reasons)):
        with localcontext() as context:
            context.prec = 40
            cost_delta = format(Decimal(economics["expanded"]["cost"]) - Decimal(economics["baseline"]["cost"]), "f")
            if useful and assessments_complete and not duplicate_units:
                unit_cost = format(Decimal(cost_delta) / len(useful), "f")
    elif economics["baseline"]["currency"] != economics["expanded"]["currency"]:
        reasons.append("comparison_currency_mismatch")
    if not useful:
        reasons.append("zero_useful_candidate_events")
    identity_review_refs = {} if identity_review_refs is None else identity_review_refs
    if not isinstance(identity_review_refs, dict) or set(identity_review_refs) - set(proposal["candidate_symbols"]):
        raise ValueError("identity review references must name only explicit additional candidates")
    verified_identity_reviews = []
    for symbol in proposal["candidate_symbols"]:
        try:
            receipt = validate_attestation(store, identity_review_refs.get(symbol), purpose="expansion_candidate_identity",
                subject_hash=digest(proposal["identities"][symbol]), bindings={"proposal_id": proposal_id, **_bindings(proposal)},
                now=now, allow_synthetic=proposal["evidence_kind"] == "synthetic")
            verified_identity_reviews.append(receipt["receipt_id"])
        except (ValueError, KeyError, TypeError):
            reasons.append("candidate_identity_review_missing_or_invalid:" + symbol)
    basis = {"proposal_id": proposal_id, "proposal_hash": digest(proposal), "source_report_hash": digest(source_report),
             "observation_refs": sorted(r["receipt_id"] for r in observations),
             "operating_refs": sorted(set(economics["baseline"]["record_refs"] + economics["expanded"]["record_refs"])),
             "coverage_snapshot_hash": proposal["coverage_snapshot_hash"]}
    review_subjects = {"wave_a": {"protocol_id": proposal["protocol_id"], "source_report_hash": digest(source_report),
                                  "coverage_snapshot_hash": proposal["coverage_snapshot_hash"]},
                       "candidate": {**basis, "candidate_symbols": proposal["candidate_symbols"]}}
    reviewed = []
    for key, ref, purpose in (("wave_a", wave_a_review_ref, "expansion_wave_a_review"),
                               ("candidate", candidate_review_ref, "expansion_candidate_review")):
        try:
            receipt = validate_attestation(store, ref, purpose=purpose, subject_hash=digest(review_subjects[key]),
                                           bindings={"proposal_id": proposal_id, **_bindings(proposal)}, now=now,
                                           allow_synthetic=proposal["evidence_kind"] == "synthetic")
            reviewed.append(receipt["receipt_id"])
        except (ValueError, KeyError, TypeError):
            reasons.append(key + "_review_missing_or_invalid")
    real = proposal["evidence_kind"] == "operator_evidence" and protocol["partition"] == "prospective"
    if not real:
        reasons.append("synthetic_or_nonprospective_evidence_not_real_measurement")
    if not proposal["candidate_symbols"]:
        reasons.append("candidate_set_difference_empty")
    report = {"contract": VERSION, "proposal_id": proposal_id, "as_of": now,
              "status": "eligible_for_expansion_review" if not reasons else "pending", "reason_codes": sorted(set(reasons)),
              "pending_reasons": sorted(set(reasons)),
              "candidate_symbols": proposal["candidate_symbols"], "already_covered": proposal["already_covered"],
              "coverage_snapshot": proposal["coverage_snapshot"], "window_start": proposal["window_start"], "window_end": proposal["window_end"],
              "basis": basis, "review_subjects": review_subjects, "identities": proposal["identities"],
              "review_refs": {"wave_a_review_ref": wave_a_review_ref, "candidate_review_ref": candidate_review_ref,
                              "identity_review_refs": deepcopy(identity_review_refs)},
              "metrics": {"deduplicated_useful_candidate_events": len(useful), "useful_candidate_event_ids": useful,
                          "useful_baseline_events": len(useful_base),
                          "useful_yield": _ratio(len(useful), len(candidate_origins), complete=assessments_complete),
                          "useful_candidate_origin_ids": sorted(useful_groups),
                          "all_useful_candidate_unit_ids": sorted(u for units in useful_groups.values() for u in units),
                          "critical_error_unit_ids": critical_errors,
                          "mapping_errors": sum(a["mapping_error"] for a in assessments.values()),
                          "mapping_error_unit_ids": sorted(k for k, a in assessments.items() if a["mapping_error"]),
                          "recall": recall, "candidate_processing": candidate_counts,
                          "baseline_processing": baseline_counts,
                          "baseline_abstention_rate": _ratio(baseline_counts["abstained"], len(baseline_rows), complete=assessments_complete),
                          "candidate_abstention_rate": _ratio(candidate_counts["abstained"], len(candidate_rows), complete=assessments_complete),
                          "requests": requests, "operating": economics,
                          "incremental_cost": cost_delta, "currency": economics["expanded"]["currency"],
                          "incremental_cost_per_useful_event": unit_cost},
              "sample": {"source_units": len(adjudications), "unique_origins": len(unique_origins),
                         "candidate_units": len(candidate_rows), "candidate_unique_origins": len(candidate_origins),
                         "candidate_units_sharing_baseline_origin": len(overlaps),
                         "buckets": {k: len(v) for k, v in buckets.items()}, "unassessed_unit_ids": missing_assessments,
                         "duplicate_assessment_unit_ids": sorted(duplicate_units),
                         "excluded_observations": excluded, "source_inventory": inventory},
              "real_gate_observations": len(adjudications) if real else 0, "evidence_kind": proposal["evidence_kind"],
              "automatic_membership_change": False, "automatic_activation": False, "signal_eligible": False,
              "human_review_status": "pending", "trust_boundary": TRUST_BOUNDARY, **_bindings(proposal)}
    if persist:
        refs = [proposal_id, *basis["observation_refs"], *basis["operating_refs"], *reviewed, *verified_identity_reviews]
        return put_receipt(store, REPORT_KIND, report, observed_at=now, refs=sorted(set(refs)))
    return report


def validate_report(store, receipt_id, registry, policies, *, current_coverage, now, instruments=None):
    """Resolve an actual gate receipt against the current complete evidence set.

    The local dependent gate may only become ready for human review. A persisted
    status string, an old report, or synthetic evidence cannot establish this.
    """
    if instruments is None:
        from instrument_registry import load_registry as load_instruments
        instruments = load_instruments()
    envelope = get_receipt(store, receipt_id, kind=REPORT_KIND)
    supplied = envelope["payload"]
    if (supplied.get("contract") != VERSION or supplied.get("evidence_kind") != "operator_evidence"
            or supplied.get("automatic_membership_change") is not False
            or supplied.get("automatic_activation") is not False
            or supplied.get("signal_eligible") is not False
            or instant(envelope["observed_at"]) > instant(now)):
        raise ValueError("actual bounded expansion report required")
    reviews = supplied.get("review_refs")
    if not isinstance(reviews, dict) or set(reviews) != {"wave_a_review_ref", "candidate_review_ref", "identity_review_refs"}:
        raise ValueError("expansion review references missing")
    current = expansion_report(store, supplied["proposal_id"], registry, policies, instruments,
                               list(current_coverage), now=now, **reviews)
    if current["status"] != "eligible_for_expansion_review" or current["pending_reasons"]:
        raise ValueError("expansion evidence remains pending: " + ",".join(current["pending_reasons"][:8]))
    if current["evidence_kind"] != "operator_evidence" or not current["real_gate_observations"]:
        raise ValueError("synthetic or absent observations cannot establish an expansion gate")
    if any(supplied.get(key) != value for key, value in current.items() if key != "as_of"):
        raise ValueError("expansion report is stale or its derived measurements differ")
    expected_refs = {current["proposal_id"], *current["basis"]["observation_refs"], *current["basis"]["operating_refs"],
                     reviews["wave_a_review_ref"], reviews["candidate_review_ref"], *reviews["identity_review_refs"].values()}
    if set(envelope["refs"]) != expected_refs:
        raise ValueError("expansion report receipt references differ from its verified evidence")
    return envelope
