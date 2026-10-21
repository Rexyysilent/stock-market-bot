"""Separate, bounded source observations, index sets and correction surveillance."""
from __future__ import annotations

from datetime import date, timedelta
import json
import re

from .identities import digest, instant
from .lifecycle import implementation_hash, reference_key
from .receipts import get_receipt, put_receipt

VERSION = "omni-source-coverage-1"
KINDS = {"feed_interval", "acquired_index_set", "correction_surveillance"}


def validate_observation(record):
    if record.get("contract") != "omni-native-inventory-1":
        raise ValueError("native inventory contract missing")
    for key in ("source_id", "route_id", "family", "policy_hash", "implementation_hash", "query_hash", "raw_response_hash"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError("native inventory missing " + key)
    start, end = instant(record.get("window_start")), instant(record.get("window_end"))
    acquired = instant(record.get("acquired_at"))
    if start > end or end > acquired:
        raise ValueError("native inventory window invalid")
    if record.get("evidence_kind") not in {"synthetic", "historical_audit", "operator_evidence", "collector_observation"}:
        raise ValueError("native inventory evidence kind unverified")
    inventory = record.get("native_inventory")
    if not isinstance(inventory, list) or len(inventory) > 50000:
        raise ValueError("native inventory membership required")
    keys = [reference_key(r) for r in inventory]
    if len(keys) != len(set(keys)) or any(r["source_id"] != record["source_id"] for r in inventory):
        raise ValueError("duplicate or wrong-source inventory")
    if record.get("native_inventory_hash") != digest(inventory):
        raise ValueError("native inventory hash mismatch")
    for key in ("rows_received", "rows_rejected"):
        if type(record.get(key)) is not int or record[key] < 0:
            raise ValueError("inventory counts missing")
    if record["rows_received"] < len(inventory) + record["rows_rejected"]:
        raise ValueError("inventory accounting mismatch")
    if record.get("reported_total") is not None and (type(record["reported_total"]) is not int or record["reported_total"] < len(inventory)):
        raise ValueError("reported count invalid")
    for key in ("truncated", "capped", "response_complete", "end_of_results"):
        if type(record.get(key)) is not bool:
            raise ValueError("inventory completeness flag missing")
    if not isinstance(record.get("gaps"), list) or any(not isinstance(x, str) for x in record["gaps"]):
        raise ValueError("inventory gaps missing")
    return record


def record_observation(store, record, *, now, refs=(), manage_transaction=True):
    validate_observation(record)
    if instant(record["acquired_at"]) > instant(now):
        raise ValueError("source acquisition is in the future")
    if record["implementation_hash"] != implementation_hash():
        raise ValueError("source observation implementation mismatch")
    if record["evidence_kind"] == "operator_evidence":
        from .attestations import validate_attestation
        attestation_ref = record.get("attestation_ref")
        body = {k: v for k, v in record.items() if k != "attestation_ref"}
        validate_attestation(store, attestation_ref, purpose="source_inventory", subject_hash=digest(body),
            bindings={"implementation_hash": record["implementation_hash"], "policy_hash": record["policy_hash"]}, now=now)
        refs = sorted(set(refs) | {attestation_ref})
    return put_receipt(store, "native_inventory_observation", record, observed_at=now, refs=list(refs),
                       manage_transaction=manage_transaction)


def observation_from_rows(rows, *, source_id, route_id, family, acquired_at, window_start, window_end,
                          policy_hash, raw_response_hash, query, gaps=(), capped=False, continuation=None,
                          end_of_results=False, reported_total=None, evidence_kind="collector_observation", request_context=None):
    inventory = [{"source_id": source_id, "native_id": r["native_id"],
                  "revision_id": r.get("revision_id") or digest(r)} for r in rows]
    return {"contract": "omni-native-inventory-1", "source_id": source_id, "route_id": route_id, "family": family,
            "window_start": window_start, "window_end": window_end, "acquired_at": acquired_at,
            "policy_hash": policy_hash, "implementation_hash": implementation_hash(), "query_hash": digest(query),
            "raw_response_hash": raw_response_hash, "native_inventory": inventory, "native_inventory_hash": digest(inventory),
            "rows_received": len(rows), "rows_rejected": 0, "reported_total": reported_total,
            "truncated": False, "capped": capped, "response_complete": not gaps, "end_of_results": end_of_results,
            "continuation": continuation, "gaps": list(gaps), "evidence_kind": evidence_kind,
            "request_context": request_context or {}, "request_context_hash": digest(request_context or {})}


def _coverage_payload(store, request, *, now, require_current=True):
    kind = request.get("kind")
    if kind not in KINDS:
        raise ValueError("separate coverage kind required")
    for field in ("source_id", "route_id", "family", "policy_hash", "implementation_hash"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError("coverage missing " + field)
    if require_current and request["implementation_hash"] != implementation_hash():
        raise ValueError("coverage implementation changed")
    start, end = instant(request.get("window_start")), instant(request.get("window_end"))
    if start > end or end > instant(now):
        raise ValueError("coverage interval invalid")
    cycles = request.get("cycle_ids", [])
    observations = request.get("observation_refs", [])
    if (not isinstance(cycles, list) or not isinstance(observations, list)
            or len(cycles) != len(set(cycles)) or len(observations) != len(set(observations))):
        raise ValueError("duplicate or invalid coverage references")
    gaps, inventory, native_seen, timestamps = set(), [], set(), []
    cycle_observations = set()
    if not cycles:
        gaps.add("source_cycles_missing")
    if not observations:
        gaps.add("source_observations_missing")
    for cycle in cycles:
        row = store.conn.execute("SELECT source_id,observed_at,health FROM source_cycles WHERE cycle_id=?", (cycle,)).fetchone()
        if not row or row[0] != request["source_id"] or instant(row[1]) > instant(now):
            raise ValueError("coverage source-cycle reference mismatch")
        health = json.loads(row[2])
        cycle_observations.update(health.get("observation_receipt_ids", []))
        exempt = {"index_reconciliation_separate_from_feed"} if kind != "feed_interval" else set()
        gaps.update(set(health.get("gaps", [])) - exempt)
    kinds, covered_windows = set(), []
    for ref in observations:
        envelope = get_receipt(store, ref, kind="native_inventory_observation")
        obs = validate_observation(envelope["payload"])
        if any(obs[k] != request[k] for k in ("source_id", "route_id", "family", "policy_hash", "implementation_hash")):
            raise ValueError("coverage observation scope/version mismatch")
        if not instant(obs["acquired_at"]) <= instant(envelope["observed_at"]) <= instant(now):
            raise ValueError("coverage observation clock mismatch")
        if ref not in cycle_observations:
            gaps.add("observation_not_bound_to_source_cycle")
        if obs["evidence_kind"] == "operator_evidence":
            from .attestations import validate_attestation
            validate_attestation(store, obs.get("attestation_ref"), purpose="source_inventory",
                subject_hash=digest({k: v for k, v in obs.items() if k != "attestation_ref"}),
                bindings={"implementation_hash": obs["implementation_hash"], "policy_hash": obs["policy_hash"]}, now=now)
        covered_windows.append((instant(obs["window_start"]), instant(obs["window_end"])))
        timestamps.append(obs["acquired_at"])
        kinds.add(obs["evidence_kind"])
        gaps.update(obs["gaps"])
        if obs["truncated"] or not obs["response_complete"] or obs["rows_rejected"]:
            gaps.add("source_response_incomplete")
        if obs["capped"] or obs.get("continuation") or not obs["end_of_results"]:
            gaps.add("source_inventory_not_exhausted")
        if obs["reported_total"] is not None and obs["reported_total"] != len(obs["native_inventory"]):
            gaps.add("source_reported_count_unmatched")
        for native in obs["native_inventory"]:
            key = reference_key(native)
            if key not in native_seen:
                native_seen.add(key)
                inventory.append(native)
    boundary = start
    for a, b in sorted(covered_windows):
        if a > boundary:
            gaps.add("source_interval_gap")
        if a <= boundary:
            boundary = max(boundary, b)
    if boundary < end:
        gaps.add("source_interval_not_closed")
    if kind == "feed_interval" and request.get("contiguous_head_traversal") is not True:
        gaps.add("contiguous_head_traversal_unverified")
    if kind == "correction_surveillance":
        comparisons=request.get("comparison_refs", [])
        if not comparisons:
            gaps.add("correction_comparison_missing")
        for ref in comparisons:
            comparison=get_receipt(store, ref, kind="index_comparison")
            data=comparison["payload"]
            if any(data.get(k)!=request[k] for k in ("source_id","route_id","policy_hash","implementation_hash")):
                raise ValueError("correction comparison scope/version mismatch")
            if instant(comparison["observed_at"])>instant(now):
                raise ValueError("future correction comparison")
            gaps.update(data.get("gaps", []))
    # A fetched set and a completed backfill never establish first availability.
    result = {**request, "contract": VERSION, "gaps": sorted(gaps),
              "status": "complete" if not gaps else "partial", "native_inventory": inventory,
              "native_inventory_hash": digest(inventory), "native_count": len(inventory),
              "received_count": len(inventory), "reported_count": len(inventory) if not gaps else None,
              "reported_count_basis": "verified_acquired_inventory" if not gaps else "incomplete_inventory",
              "source_reported_totals": [get_receipt(store, ref, kind="native_inventory_observation")["payload"].get("reported_total")
                                         for ref in observations],
              "observation_times": sorted(timestamps), "evidence_kinds": sorted(kinds),
              "continuous_source": False, "historical_availability_reconstructed": False,
              "corrections_certified": False, "automatic_activation": False}
    return result


def record_coverage(store, request, *, now):
    result = _coverage_payload(store, request, now=now)
    refs = sorted(set(request.get("observation_refs", []) + request.get("comparison_refs", [])))
    return put_receipt(store, "source_coverage", result, observed_at=now, refs=refs)


def get_coverage(store, receipt_id):
    envelope = get_receipt(store, receipt_id, kind="source_coverage")
    result = _coverage_payload(store, envelope["payload"], now=envelope["observed_at"], require_current=False)
    if result != envelope["payload"]:
        raise ValueError("coverage derivation mismatch")
    return {**result, "receipt_id": receipt_id, "observed_at": envelope["observed_at"]}


def cursor_proof(proof, *, source_id, old_watermark, watermark, observed_at):
    """Low-level cursor closure requires captured traversal evidence, not a flag."""
    if not isinstance(proof, dict) or proof.get("contract") != "omni-feed-traversal-1":
        return False
    pages = proof.get("pages", [])
    if (proof.get("source_id") != source_id or proof.get("previous_watermark") != old_watermark
            or proof.get("watermark") != watermark or proof.get("started_from_head") is not True
            or proof.get("gaps") or not pages or len(pages) > 12):
        return False
    if proof.get("proof_hash") != digest({k: v for k, v in proof.items() if k != "proof_hash"}):
        return False
    seen = set()
    for page in pages:
        if (not isinstance(page, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(page.get("raw_sha256", "")))
                or not isinstance(page.get("native_ids"), list) or page.get("response_complete") is not True):
            return False
        if (not page["native_ids"] or len(page["native_ids"]) > 100
                or len(page["native_ids"]) != len(set(page["native_ids"]))
                or len(page.get("publication_times", [])) != len(page["native_ids"])):
            return False
        if page.get("offset") in seen:
            return False
        seen.add(page.get("offset"))
    if sorted(seen) != list(range(0, len(pages) * 100, 100)):
        return False
    stamps = [s for p in pages for s in p.get("publication_times", [])]
    try:
        if not watermark or not stamps or max(map(instant, stamps)) != instant(watermark) or instant(watermark) > instant(observed_at):
            return False
        return bool(old_watermark and min(map(instant, stamps)) <= instant(old_watermark))
    except (TypeError, ValueError):
        return False


def seed_proof(proof, *, source_id, watermark, observed_at):
    if (not isinstance(proof, dict) or proof.get("previous_watermark") is not None
            or set(proof.get("gaps", [])) != {"initial_window_only"}
            or proof.get("proof_hash") != digest({k: v for k, v in proof.items() if k != "proof_hash"})):
        return False
    check = {**proof, "previous_watermark": watermark, "gaps": []}
    check["proof_hash"] = digest({k: v for k, v in check.items() if k != "proof_hash"})
    return cursor_proof(check, source_id=source_id, old_watermark=watermark, watermark=watermark, observed_at=observed_at)


def repair_plan(*, start_day, end_day, source_id="sec-edgar"):
    a, b = date.fromisoformat(start_day), date.fromisoformat(end_day)
    if b < a:
        raise ValueError("repair interval reversed")
    chunks = []
    while a <= b and len(chunks) < 52:
        stop = min(a + timedelta(days=6), b)
        chunks.append({"start_day": a.isoformat(), "end_day": stop.isoformat(), "max_requests": 7})
        a = stop + timedelta(days=1)
    return {"contract": "omni-repair-plan-1", "source_id": source_id, "batches": chunks,
            "continuation_day": a.isoformat() if a <= b else None, "status": "planned_only",
            "feed_watermark_advanced": False, "historical_availability_reconstructed": False,
            "execution_requires_explicit_command": True}


def feed_observation_window(store, *, source_id, route_id, family, query_hash, policy_hash, proof, now):
    """A poll clock interval requires a captured, contiguous prior poll boundary.

    This describes two observed feed snapshots. It does not assert retention,
    records between polls, first availability or uninterrupted transport.
    """
    previous=(proof or {}).get("previous_watermark")
    if store.cursor(source_id)["watermark"]!=previous or not cursor_proof(proof,source_id=source_id,old_watermark=previous,
                        watermark=(proof or {}).get("watermark"),observed_at=now):
        return now,now
    for row in store.conn.execute("SELECT observed_at,health FROM source_cycles WHERE source_id=? ORDER BY rowid DESC",(source_id,)):
        health=json.loads(row[1]); prior=health.get("coverage_proof")
        if (not prior or not health.get("observation_receipt_ids") or health.get("watermark_candidate")!=previous
                or instant(row[0])>=instant(now)):
            continue
        valid=cursor_proof(prior,source_id=source_id,old_watermark=prior.get("previous_watermark"),
            watermark=previous,observed_at=row[0]) or seed_proof(prior,source_id=source_id,watermark=previous,observed_at=row[0])
        if not valid or not set(health.get("gaps",[]))<={"initial_window_only"}:
            continue
        snapshots=[]
        try:
            for ref in health["observation_receipt_ids"]:
                envelope=get_receipt(store,ref,kind="native_inventory_observation")
                obs=validate_observation(envelope["payload"])
                if (any(obs[key]!=value for key,value in {"source_id":source_id,"route_id":route_id,
                        "family":family,"query_hash":query_hash,"policy_hash":policy_hash,
                        "implementation_hash":implementation_hash()}.items())
                        or instant(obs["acquired_at"])!=instant(row[0])
                        or instant(envelope["observed_at"])!=instant(row[0])):
                    raise ValueError("prior poll scope or clock mismatch")
                snapshots.append(obs)
        except (ValueError,KeyError,TypeError):
            continue
        if all(any(obs["raw_response_hash"]==page["raw_sha256"]
                and {n["native_id"] for n in obs["native_inventory"]}==set(page["native_ids"])
                for obs in snapshots) for page in prior["pages"]):
            return row[0],now
    return now,now
