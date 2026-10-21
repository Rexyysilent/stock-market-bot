"""Bounded full/quarterly SEC snapshots and comparisons; acquisition stays disabled."""
from __future__ import annotations

from datetime import date
import json
from zoneinfo import ZoneInfo

from .coverage import observation_from_rows
from .identities import digest, instant
from .lifecycle import implementation_hash, observe_change
from .receipts import get_receipt, list_receipts, put_receipt, transaction
from .reconciliation import parse_daily_index
from .route_contracts import check_route, route_plan

VERSION = "omni-index-snapshot-1"


def parse_snapshot(raw, *, route_id, observed_at, year=None, quarter=None, max_rows=50000):
    plan = route_plan(route_id, year=year, quarter=quarter)
    if plan["format"] != "sec_master_index":
        raise ValueError("index route required")
    if route_id=="sec-full-index":
        observed_day=instant(observed_at).astimezone(ZoneInfo("America/New_York")).date()
        actual_scope=(observed_day.year,(observed_day.month-1)//3+1)
        if (year is not None and year!=actual_scope[0]) or (quarter is not None and quarter!=actual_scope[1]):
            raise ValueError("full index scope must match its observed current quarter")
        year,quarter=actual_scope
    parsed = parse_daily_index(raw, day=instant(observed_at).date().isoformat(), max_rows=max_rows)
    for row in parsed["rows"]:
        filed = date.fromisoformat(row["filed_date"])
        if filed.year != year or (filed.month-1)//3+1 != quarter:
            raise ValueError("quarterly index contains out-of-scope filing")
        row.pop("index_day", None)
    return {"contract": VERSION, "route_id": route_id, "url": plan["url"], "year": year,
            "quarter": quarter, "acquired_at": observed_at, **parsed,
            "gaps": ["empty_index_requires_review"] if not parsed["rows"] else [],
            "source_time_precision": "filing_date_only", "historical_availability_reconstructed": False}


def record_snapshot(store, parsed, *, source_id, policy_hash, now, evidence_kind="synthetic"):
    """Retain bounded metadata chunks with an atomic source-cycle/manifest link.

    Direct local raw-file imports are synthetic/audit observations. The explicit
    collector supplies collector_observation after its route/source grant checks.
    """
    if evidence_kind not in {"synthetic", "historical_audit", "collector_observation"}:
        raise ValueError("use the typed source-inventory importer for operator evidence")
    if parsed.get("contract") != VERSION or instant(parsed["acquired_at"]) > instant(now):
        raise ValueError("invalid index snapshot")
    rows = parsed["rows"]
    if len(rows) > 50000 or len({r["native_id"] for r in rows}) != len(rows):
        raise ValueError("index inventory duplicate or cap")
    scope = {k: parsed[k] for k in ("route_id", "url", "year", "quarter")}
    cycle_id = "index-snapshot-cycle:" + digest([source_id, scope, parsed["raw_sha256"], parsed["acquired_at"]])
    for prior in list_receipts(store,kind="index_snapshot"):
        p=prior["payload"]
        if p.get("cycle_id")!=cycle_id: continue
        if (p.get("rows_hash")!=digest(rows) or p.get("policy_hash")!=policy_hash or p.get("evidence_kind")!=evidence_kind
                or p.get("implementation_hash")!=implementation_hash() or instant(prior["observed_at"])>instant(now)):
            raise ValueError("snapshot identity reused with changed content/version or clock")
        return prior
    observations = [observation_from_rows(chunk, source_id=source_id, route_id=parsed["route_id"],
        family="sec_filings", acquired_at=parsed["acquired_at"], window_start=parsed["acquired_at"],
        window_end=parsed["acquired_at"], policy_hash=policy_hash, raw_response_hash=parsed["raw_sha256"],
        query={**scope, "chunk": offset}, gaps=parsed["gaps"], end_of_results=True, evidence_kind=evidence_kind)
        for offset in range(0, max(1, len(rows)), 500) for chunk in [rows[offset:offset+500]]]
    with transaction(store):
        parts = [put_receipt(store, "index_snapshot_part", {"contract": VERSION, "scope": scope,
                 "source_id": source_id, "rows": rows[offset:offset+500], "offset": offset},
                 observed_at=now, manage_transaction=False)
                 for offset in range(0, max(1, len(rows)), 500)]
        store.commit_cycle(cycle_id=cycle_id, source_id=source_id, observed_at=now, documents=[], events=[],
            decisions=[], candidates=rows, coverage_observations=observations, complete=False, preserve_cursor=True,
            gaps=["index_reconciliation_separate_from_feed"]+parsed["gaps"],
            metrics={"raw_sha256": parsed["raw_sha256"], "unique_accessions": len(rows)}, manage_transaction=False)
        health = json.loads(store.conn.execute("SELECT health FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()[0])
        body = {"contract": VERSION, **scope, "source_id": source_id, "acquired_at": parsed["acquired_at"],
                "raw_sha256": parsed["raw_sha256"], "rows_hash": digest(rows), "row_count": len(rows),
                "part_refs": [r["receipt_id"] for r in parts], "cycle_id": cycle_id,
                "observation_refs": health["observation_receipt_ids"], "gaps": parsed["gaps"],
                "policy_hash": policy_hash, "implementation_hash": implementation_hash(),
                "evidence_kind": evidence_kind, "continuous_source": False,
                "historical_availability_reconstructed": False}
        return put_receipt(store, "index_snapshot", body, observed_at=now,
            refs=body["part_refs"]+body["observation_refs"], manage_transaction=False)


def snapshot_rows(store, receipt_id):
    envelope = get_receipt(store, receipt_id, kind="index_snapshot")
    p = envelope["payload"]
    rows = []
    for ref in p["part_refs"]:
        part = get_receipt(store, ref, kind="index_snapshot_part")["payload"]
        if part["offset"] != len(rows) or part["source_id"] != p["source_id"] or part["scope"]!={k:p[k] for k in ("route_id","url","year","quarter")}:
            raise ValueError("index part order/scope mismatch")
        rows.extend(part["rows"])
    if len(rows) != p["row_count"] or digest(rows) != p["rows_hash"]:
        raise ValueError("index snapshot integrity mismatch")
    return envelope, rows


def compare_snapshots(store, previous_ref, current_ref, *, now, fail_at=None):
    old, before = snapshot_rows(store, previous_ref)
    new, after = snapshot_rows(store, current_ref)
    a, b = old["payload"], new["payload"]
    fields = ("source_id", "route_id", "year", "quarter", "policy_hash", "implementation_hash")
    if any(a[k] != b[k] for k in fields) or b["implementation_hash"] != implementation_hash():
        raise ValueError("snapshot comparison scope/version mismatch")
    if not instant(a["acquired_at"]) < instant(b["acquired_at"]) <= instant(now):
        raise ValueError("snapshot comparison times invalid")
    old_rows, new_rows = {r["native_id"]: r for r in before}, {r["native_id"]: r for r in after}
    missing = sorted(set(old_rows)-set(new_rows))
    changed = sorted(k for k in set(old_rows) & set(new_rows) if old_rows[k] != new_rows[k])
    result = {"contract": VERSION, "previous_ref": previous_ref, "current_ref": current_ref,
        "source_id": b["source_id"], "route_id": b["route_id"], "policy_hash": b["policy_hash"],
        "implementation_hash": b["implementation_hash"], "missing_native_ids": missing,
        "changed_native_ids": changed, "added_native_ids": sorted(set(new_rows)-set(old_rows)),
        "gaps": sorted(set(a["gaps"]+b["gaps"])), "missing_means": "discrepancy_not_retraction",
        "continuous_source": False, "historical_availability_reconstructed": False, "human_review_status": "pending"}
    with transaction(store):
        for prior in list_receipts(store,kind="index_comparison"):
            if prior["payload"]==result:
                if instant(prior["observed_at"])>instant(now): raise ValueError("comparison import clock moved backwards")
                return prior
        receipt = put_receipt(store, "index_comparison", result, observed_at=now,
                              refs=[previous_ref, current_ref], manage_transaction=False)
        for key in missing+changed:
            observe_change(store, source_id=b["source_id"], native_id=key, old=old_rows[key],
                new=new_rows.get(key), kind="discrepancy", observed_at=now, policy_hash=b["policy_hash"],
                proof_refs=[receipt["receipt_id"]], manage_transaction=False)
        if fail_at == "after_changes":
            raise RuntimeError("injected comparison crash")
    return receipt


def collect_snapshot(store, client, *, route_id, now, year=None, quarter=None):
    plan = route_plan(route_id, year=year, quarter=quarter)
    check_route(client.policy, route_id, plan["url"], store=store, now=now, year=year, quarter=quarter)
    raw = client.get(plan["url"], content_types=("text/plain", "application/octet-stream"))
    parsed = parse_snapshot(raw, route_id=route_id, observed_at=now, year=year, quarter=quarter)
    return record_snapshot(store, parsed, source_id=client.policy["policy_id"],
                           policy_hash=digest(client.policy), now=now, evidence_kind="collector_observation")
