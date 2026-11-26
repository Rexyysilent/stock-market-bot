"""Cohort-wide, descriptive verification of archived measurement coverage.

The existing signal ledger starts with emitted records. This sidecar starts with
every member of every frozen archive cohort, including runs with no signals.
It does not select securities, create orders, or estimate a strategy's P&L.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import DB_PATH
from .db import connect
from .outcomes import nyse_schedule
from .prices import (
    DEFAULT_ADJUSTMENT_BASIS, DEFAULT_INTERVAL, _provider_identity,
    ensure_window, fetch_yfinance, resolve_cached_window,
)

POLICY_SCHEMA = "signal-verification-policy-1"
REPORT_SCHEMA = "signal-verification-report-1"
REQUIRED_TABLES = frozenset((
    "runs", "run_cohorts", "signals", "signal_sightings", "price_acquisitions",
    "price_points", "price_windows", "price_fetch_misses",
))


def _now():
    return datetime.now(timezone.utc)


def _instant(value, name):
    try:
        parsed = (value if isinstance(value, datetime)
                  else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _z(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


@dataclass(frozen=True)
class VerificationPolicy:
    """Complete, hashable measurement assumptions; thresholds have no defaults."""

    policy_id: str
    declared_at: str
    move_threshold: float
    round_trip_cost_bps: float
    horizons: tuple[int, ...] = (1, 5, 20)
    entry_latency_seconds: int = 0
    benchmark: str = "SPY"

    def __post_init__(self):
        if not isinstance(self.policy_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.policy_id
        ):
            raise ValueError("policy_id must be a short identifier")
        object.__setattr__(self, "declared_at", _z(_instant(self.declared_at, "declared_at")))
        object.__setattr__(self, "move_threshold", _number(
            self.move_threshold, "move_threshold", positive=True))
        cost = _number(self.round_trip_cost_bps, "round_trip_cost_bps")
        if cost > 10_000:
            raise ValueError("round_trip_cost_bps must not exceed 10000")
        object.__setattr__(self, "round_trip_cost_bps", cost)
        if not isinstance(self.horizons, (list, tuple)) or not self.horizons:
            raise ValueError("horizons must be a nonempty list of session offsets")
        if any(type(horizon) is not int or not 1 <= horizon <= 252
               for horizon in self.horizons):
            raise ValueError("horizons must contain integers from 1 through 252")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError("horizons must not repeat")
        object.__setattr__(self, "horizons", tuple(sorted(self.horizons)))
        if (type(self.entry_latency_seconds) is not int
                or not 0 <= self.entry_latency_seconds <= 604_800):
            raise ValueError("entry_latency_seconds must be an integer from 0 through 604800")
        if not isinstance(self.benchmark, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9.-]{0,19}", self.benchmark
        ):
            raise ValueError("benchmark must name a stock/ETF symbol, not a future or index")

    def to_dict(self):
        return {
            "schema_version": POLICY_SCHEMA,
            "policy_id": self.policy_id,
            "declared_at": self.declared_at,
            "move_threshold": self.move_threshold,
            "round_trip_cost_bps": self.round_trip_cost_bps,
            "horizons": list(self.horizons),
            "entry_latency_seconds": self.entry_latency_seconds,
            "benchmark": self.benchmark,
        }

    @property
    def sha256(self):
        return _sha(self.to_dict())

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("verification policy must be an object")
        allowed = set(cls.__dataclass_fields__) | {"schema_version"}
        required = {"policy_id", "declared_at", "move_threshold", "round_trip_cost_bps"}
        if set(value) - allowed or not required.issubset(value):
            raise ValueError("verification policy has unknown or missing fields")
        if value.get("schema_version", POLICY_SCHEMA) != POLICY_SCHEMA:
            raise ValueError("unsupported verification policy schema")
        return cls(**{key: item for key, item in value.items() if key != "schema_version"})


def _cohort(row):
    if row["cohort_status"] != "valid":
        raise ValueError(row["cohort_reason"] or row["cohort_status"] or "missing_cohort")
    try:
        members = json.loads(row["members_json"])
        weights = json.loads(row["weights_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_cohort_json") from exc
    if (not isinstance(members, list) or not members
            or any(not isinstance(item, str) or not item
                   or item != item.strip().upper() for item in members)
            or len(set(members)) != len(members)):
        raise ValueError("invalid_cohort_members")
    if (not isinstance(weights, dict) or set(weights) != set(members)
            or any(isinstance(weight, bool) or not isinstance(weight, (int, float))
                   or not math.isfinite(weight) or weight <= 0
                   for weight in weights.values())
            or not math.isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-10)):
        raise ValueError("invalid_cohort_weights")
    identity = {
        "status": row["cohort_status"], "reason": row["cohort_reason"],
        "members": members, "weights": weights, "weight_method": row["weight_method"],
    }
    if _sha(identity) != row["cohort_sha256"]:
        raise ValueError("cohort_digest_mismatch")
    return members


def _quality(row):
    if not row["evidence_json"]:
        raise ValueError("run_quality_not_recorded")
    try:
        evidence = json.loads(row["evidence_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_run_evidence") from exc
    if (not isinstance(evidence, dict) or _sha(evidence) != row["evidence_sha256"]
            or evidence.get("brief_sha256") != row["brief_sha256"]
            or row["evidence_brief_sha256"] != row["brief_sha256"]):
        raise ValueError("run_evidence_digest_mismatch")
    if evidence.get("contract") != "ledger-run-evidence-1":
        raise ValueError("unsupported_run_evidence_contract")
    if evidence.get("source_schema_version") not in ("2.0", "2.5", "2.6", "2.7", "2.8", "2.9"):
        raise ValueError("unknown_archive_schema")
    if evidence.get("generated_at_origin") != "explicit_archive_timestamp":
        raise ValueError("archive_timestamp_not_established")
    if evidence.get("generated_at") != row["generated_at"]:
        raise ValueError("archive_timestamp_evidence_mismatch")
    if evidence.get("synthetic_status") in ("declared_synthetic", "invalid_marker"):
        raise ValueError("synthetic_or_invalid_archive_marker")
    if evidence.get("health_status") == "ERROR" or evidence.get("health_error_count"):
        raise ValueError("archive_failed_health")
    if evidence.get("failed_stage_count") or not evidence.get("failed_stage_shape_valid"):
        raise ValueError("archive_failed_stage")
    if evidence.get("health_status") not in ("OK", "WARN") or evidence.get("health_error_count") is None:
        raise ValueError("archive_health_unknown")
    return evidence


def _schedule(provider, generated, max_horizon, latency):
    start = generated.date() - timedelta(days=3)
    end = (generated + latency).date() + timedelta(days=3 * max_horizon + 21)
    rows = []
    for session, opened, closed in provider(start, end):
        session = date.fromisoformat(session).isoformat()
        opened, closed = _instant(opened, "market_open"), _instant(closed, "market_close")
        if closed <= opened:
            raise ValueError("market close must be later than open")
        if rows and (session <= rows[-1][0] or opened <= rows[-1][2]):
            raise ValueError("exchange schedule must have unique, ordered, disjoint sessions")
        rows.append((session, opened, closed))
    eligible_at = generated + latency
    entry = next((index for index, (_, opened, _) in enumerate(rows)
                  if opened > eligible_at), None)
    if entry is None or entry + max_horizon >= len(rows):
        raise ValueError("exchange_schedule_incomplete")
    return rows, entry


def _signal_summary(rows, generated, cutoff):
    valid, rejected = [], []
    for row in rows:
        try:
            observed = _instant(row["observed_at"], "signal observed_at")
            source = _instant(row["as_of"], "signal as_of") if row["as_of"] else None
            if observed > generated or observed > cutoff or (source and source > generated):
                raise ValueError("signal_time_after_archive")
        except ValueError as exc:
            rejected.append({"record_id": row["record_id"], "reason": str(exc)})
            continue
        valid.append(row)
    directions = sorted({row["direction"] for row in valid
                         if row["direction"] in ("long", "short")})
    return {
        "signal_count": len(valid),
        "record_ids": sorted(row["record_id"] for row in valid),
        "families": sorted({row["family"] for row in valid}),
        "directions": directions,
        "direction": directions[0] if len(directions) == 1 else None,
        "direction_conflict": len(directions) > 1,
        "first_emission_count": sum(row["first_seen_run"] == row["run_id"] for row in valid),
        "repeated_sighting_count": sum(row["first_seen_run"] != row["run_id"] for row in valid),
        "unknown_source_time_count": sum(not row["as_of"] for row in valid),
        "rejected_signal_count": len(rejected),
        "rejected_signals": rejected,
    }


def _price_window(conn, ticker, entry, exit_, *, provider, fetch, cutoff,
                  exit_close, strict_price_cutoff, memo):
    key = (ticker, entry, exit_)
    if key in memo:
        return memo[key]
    expected_provider = _provider_identity(provider)
    state = "not_cached"
    resolved = resolve_cached_window(conn, ticker, entry, exit_, provider=expected_provider,
                                     acquired_not_before=exit_close)
    metadata = resolved["metadata"] if resolved else None
    if fetch and metadata is None:
        # The network acquisition's clock is real. An explicit old cutoff never
        # makes a new download a historical observation.
        acquired = _now()
        state = ensure_window(
            conn, ticker, date.fromisoformat(entry), date.fromisoformat(exit_),
            provider, now=acquired, acquisition_clock=_now, acquired_not_before=exit_close, run_memo={},
        )
        resolved = resolve_cached_window(conn, ticker, entry, exit_, provider=expected_provider,
                                         acquired_not_before=exit_close)
        metadata = resolved["metadata"] if resolved else None
    result = {"status": state, "return": None, "metadata": metadata}
    if metadata is not None:
        if (metadata["provider"] != expected_provider
                or metadata["interval"] != DEFAULT_INTERVAL
                or metadata["adjustment_basis"] != DEFAULT_ADJUSTMENT_BASIS):
            result["status"] = "incompatible_price_identity"
        else:
            try:
                acquired = _instant(metadata["acquired_at"], "price acquired_at")
                known_by = cutoff if strict_price_cutoff else _now()
                if acquired < exit_close:
                    result["status"] = "price_acquired_before_exit_close"
                elif acquired > known_by:
                    result["status"] = "price_acquired_after_cutoff"
                else:
                    pair = resolved["open_close"]
                    value = pair[1] / pair[0] - 1.0 if pair else None
                    if value is None or not math.isfinite(value):
                        result["status"] = "invalid_or_incomplete_endpoints"
                    else:
                        result.update({"status": "ok", "return": value})
            except (ValueError, OverflowError):
                result["status"] = "invalid_price_metadata"
    memo[key] = result
    return result


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _counts(rows, *, rates):
    states = Counter(row["status"] for row in rows)
    observed = [row for row in rows if row["status"] == "observed"]
    directional = [row for row in observed if row["signal"]["direction"] is not None]
    movers = [row for row in observed if row["large_move"]]
    captured = sum(row["signal"]["signal_count"] > 0 for row in movers)
    matched = sum(row["capture_class"] == "direction_matched" for row in movers)
    mature = len(observed) + states["unavailable"]
    values = {
        "expected_observations": len(rows),
        "supported_observations": len(rows) - states["unsupported_asset"],
        "matured_expected": mature,
        "observed": len(observed),
        "unavailable": states["unavailable"],
        "pending": states["pending"],
        "unsupported_asset": states["unsupported_asset"],
        "large_moves": len(movers),
        "captured_large_moves": captured,
        "uncaptured_large_moves": len(movers) - captured,
        "direction_matched_large_moves": matched,
        "directional_observations": len(directional),
        "directional_hits_after_hurdle": sum(
            row["signed_excess_after_friction"] > 0 for row in directional),
        "conflicting_direction_observations": sum(
            row["signal"]["direction_conflict"] for row in observed),
        "signals_with_unknown_source_time": sum(
            row["signal"]["unknown_source_time_count"] for row in rows),
        "rejected_signal_occurrences": sum(
            row["signal"]["rejected_signal_count"] for row in rows),
        "distinct_entry_sessions": len({row["entry_session"] for row in observed}),
        "capture_classes": dict(sorted(Counter(
            row["capture_class"] for row in movers).items())),
        "outcome_coverage": _ratio(len(observed), mature),
        "complete_matured_population": bool(mature) and not states["unavailable"],
    }
    if rates:
        values.update({
            "event_capture_rate_observed": _ratio(captured, len(movers)),
            "directional_capture_rate_observed": _ratio(matched, len(movers)),
            "directional_hit_rate_observed": _ratio(
                values["directional_hits_after_hurdle"], len(directional)),
            "rate_scope": "observed complete pairs only; missing outcomes remain unknown",
            "inference_status": "descriptive_only",
        })
    return values


def build_verification(db_path=DB_PATH, *, policy, as_of=None, fetch=False,
                       provider=fetch_yfinance, schedule_provider=nyse_schedule):
    """Return a JSON-safe report without altering signals or their outcomes.

    Cache-only is the default and opens SQLite read-only. ``fetch=True`` can
    extend the existing coherent price cache, using its bounded miss backoff.
    An explicit ``as_of`` is a strict cutoff for both archives and acquisitions.
    When omitted, completed-session eligibility is fixed at function start and
    permitted acquisitions retain their actual, separately reported times.
    """
    policy = policy if isinstance(policy, VerificationPolicy) else VerificationPolicy.from_dict(policy)
    if type(fetch) is not bool:
        raise ValueError("fetch must be boolean")
    started = _now()
    cutoff = _instant(as_of, "as_of") if as_of is not None else started
    if cutoff > started:
        raise ValueError("as_of must not be in the future")
    if _instant(policy.declared_at, "declared_at") > cutoff:
        raise ValueError("policy declaration must not be after as_of")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"ledger database does not exist: {path}")
    conn = connect(path) if fetch else sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES.issubset(present):
            raise ValueError("ledger schema incomplete; ingest archives with the current ledger first")
        if not fetch:
            conn.execute("BEGIN")
        evidence_columns = (
            "e.metadata_json AS evidence_json,e.metadata_sha256 AS evidence_sha256,"
            "e.brief_sha256 AS evidence_brief_sha256" if "run_evidence" in present else
            "NULL AS evidence_json,NULL AS evidence_sha256,NULL AS evidence_brief_sha256"
        )
        evidence_join = "LEFT JOIN run_evidence e ON e.run_id=r.run_id" if "run_evidence" in present else ""
        runs = conn.execute(
            f"""SELECT r.*,c.status AS cohort_status,c.reason AS cohort_reason,
                      c.members_json,c.weights_json,c.weight_method,c.cohort_sha256
                      ,{evidence_columns}
               FROM runs r LEFT JOIN run_cohorts c ON c.run_id=r.run_id
               {evidence_join}
               ORDER BY r.generated_at,r.run_id"""
        ).fetchall()
        sightings = defaultdict(list)
        for row in conn.execute(
            """SELECT ss.*,s.first_seen_run FROM signal_sightings ss
               JOIN signals s ON s.record_id=ss.record_id
               ORDER BY ss.run_id,ss.ticker,ss.record_id"""
        ):
            sightings[(row["run_id"], row["ticker"])].append(dict(row))
        selected, exclusions, schedules = {}, [], {}
        latency = timedelta(seconds=policy.entry_latency_seconds)
        declared = _instant(policy.declared_at, "declared_at")
        for row in runs:
            try:
                generated = _instant(row["generated_at"], "archive generated_at")
                if generated > cutoff:
                    exclusions.append({"run_id": row["run_id"], "reason": "archive_after_cutoff"})
                    continue
                quality = _quality(row)
                members = _cohort(row)
                schedule_key = generated.date().isoformat()
                if schedule_key not in schedules:
                    schedules[schedule_key] = _schedule(
                        schedule_provider, generated, max(policy.horizons), latency)[0]
                sessions = schedules[schedule_key]
                entry_index = next((index for index, (_, opened, _) in enumerate(sessions)
                                    if opened > generated + latency), None)
                if entry_index is None or entry_index + max(policy.horizons) >= len(sessions):
                    raise ValueError("exchange_schedule_incomplete")
            except (TypeError, ValueError, OverflowError) as exc:
                exclusions.append({"run_id": row["run_id"], "reason": str(exc)})
                continue
            timing = "predeclared_policy" if declared <= generated else "retrospective_policy"
            key = (row["pipeline_version"], row["cohort_sha256"], sessions[entry_index][0])
            candidate = {
                "run_id": row["run_id"], "generated_at": _z(generated),
                "brief_sha256": row["brief_sha256"],
                "pipeline_version": row["pipeline_version"],
                "cohort_sha256": row["cohort_sha256"], "members": members,
                "quality_evidence": quality,
                "policy_timing": timing, "entry_session": sessions[entry_index][0],
                "_generated": generated, "_sessions": sessions, "_entry": entry_index,
            }
            if key in selected:
                previous = selected[key]
                winner, loser = (candidate, previous) if (
                    generated, row["run_id"]) > (previous["_generated"], previous["run_id"]
                ) else (previous, candidate)
                exclusions.append({
                    "run_id": loser["run_id"], "reason": "superseded_same_entry_session",
                    "selected_run_id": winner["run_id"], "entry_session": key[-1],
                })
                selected[key] = winner
            else:
                selected[key] = candidate

        successors = {row["run_id"]: row["selected_run_id"] for row in exclusions
                      if row["reason"] == "superseded_same_entry_session"}
        for excluded in exclusions:
            if "selected_run_id" in excluded:
                while excluded["selected_run_id"] in successors:
                    excluded["selected_run_id"] = successors[excluded["selected_run_id"]]

        observations, price_memo, run_receipts = [], {}, []
        friction = policy.round_trip_cost_bps / 10_000.0
        for _, run in sorted(selected.items()):
            sessions, entry_index = run["_sessions"], run["_entry"]
            run_receipts.append({key: value for key, value in run.items() if not key.startswith("_")})
            # A larger mature acquisition can satisfy shorter windows through
            # ensure_window's compatible immutable subwindow binding.
            for horizon in reversed(policy.horizons):
                exit_session, _, exit_close = sessions[entry_index + horizon]
                entry_open = sessions[entry_index][1]
                for ticker in run["members"]:
                    signal = _signal_summary(
                        sightings[(run["run_id"], ticker)], run["_generated"], cutoff)
                    row = {
                        "run_id": run["run_id"], "pipeline_version": run["pipeline_version"],
                        "cohort_sha256": run["cohort_sha256"], "policy_timing": run["policy_timing"],
                        "ticker": ticker, "horizon": horizon, "entry_session": run["entry_session"],
                        "exit_session": exit_session, "entry_open_at": _z(entry_open),
                        "exit_close_at": _z(exit_close), "signal": signal,
                        "status": None, "reason": None, "raw_return": None,
                        "benchmark_return": None, "excess_return": None,
                        "absolute_excess_after_friction": None,
                        "signed_excess_after_friction": None, "large_move": None,
                        "capture_class": None, "price": None, "benchmark_price": None,
                        "listing_identity_status": "archive_symbol_only; fillability_not_established",
                    }
                    if ticker.startswith("^") or ticker.endswith("=F"):
                        row.update(status="unsupported_asset", reason="index_or_continuous_future_proxy")
                    elif exit_close > cutoff:
                        row.update(status="pending", reason=(
                            "entry_not_reached" if entry_open > cutoff else "horizon_not_mature"))
                    else:
                        stock = _price_window(
                            conn, ticker, row["entry_session"], exit_session,
                            provider=provider, fetch=fetch, cutoff=cutoff, exit_close=exit_close,
                            strict_price_cutoff=as_of is not None, memo=price_memo)
                        benchmark = _price_window(
                            conn, policy.benchmark, row["entry_session"], exit_session,
                            provider=provider, fetch=fetch, cutoff=cutoff, exit_close=exit_close,
                            strict_price_cutoff=as_of is not None, memo=price_memo)
                        row.update(price=stock, benchmark_price=benchmark,
                                   raw_return=stock["return"], benchmark_return=benchmark["return"])
                        if stock["status"] != "ok" or benchmark["status"] != "ok":
                            row.update(status="unavailable", reason="incomplete_stock_benchmark_pair")
                        elif stock["metadata"]["currency"] != benchmark["metadata"]["currency"]:
                            row.update(status="unavailable", reason="currency_basis_mismatch")
                        else:
                            excess = stock["return"] - benchmark["return"]
                            if not math.isfinite(excess):
                                row.update(status="unavailable", reason="nonfinite_excess_return")
                            else:
                                magnitude = abs(excess) - friction
                                row.update(status="observed", excess_return=excess,
                                           absolute_excess_after_friction=magnitude,
                                           large_move=magnitude >= policy.move_threshold)
                                if signal["direction"] is not None:
                                    sign = 1 if signal["direction"] == "long" else -1
                                    row["signed_excess_after_friction"] = sign * excess - friction
                                if row["large_move"]:
                                    actual = "long" if excess > 0 else "short"
                                    if not signal["signal_count"]:
                                        row["capture_class"] = "no_recorded_signal"
                                    elif signal["direction_conflict"]:
                                        row["capture_class"] = "conflicting_directions"
                                    elif not signal["directions"]:
                                        row["capture_class"] = "directionless_only"
                                    elif signal["direction"] == actual:
                                        row["capture_class"] = "direction_matched"
                                    else:
                                        row["capture_class"] = "opposite_direction_only"
                    observations.append(row)
        if fetch:
            conn.commit()
    finally:
        conn.close()

    grouped = defaultdict(list)
    for row in observations:
        grouped[(row["pipeline_version"], row["cohort_sha256"], row["policy_timing"])].append(row)
    segments = []
    for (era, cohort, timing), rows in sorted(grouped.items()):
        segments.append({
            "pipeline_version": era, "cohort_sha256": cohort, "policy_timing": timing,
            "horizons": {str(horizon): _counts(
                [row for row in rows if row["horizon"] == horizon], rates=True)
                         for horizon in policy.horizons},
        })
    summary = _counts(observations, rates=False)
    summary.update({
        "archived_runs": len(runs), "selected_runs": len(run_receipts),
        "excluded_runs": len(exclusions),
        "exclusion_reasons": dict(sorted(Counter(row["reason"] for row in exclusions).items())),
        "n_segments": len(segments), "accuracy_rates_pooled": False,
    })
    finished = _now()
    report = {
        "schema_version": REPORT_SCHEMA,
        "as_of": _z(cutoff), "evaluation_started_at": _z(started), "evaluated_at": _z(finished),
        "price_known_by": _z(cutoff if as_of is not None else finished),
        "policy": policy.to_dict(), "policy_sha256": policy.sha256,
        "summary": summary, "segments": segments, "selected_runs": run_receipts,
        "excluded_runs": sorted(exclusions, key=lambda row: (row["run_id"], row["reason"])),
        "observations": observations,
        "definitions": {
            "population": "all members of the selected archive's frozen instrumented cohort",
            "run_selection": "latest eligible completed archive per era, cohort and eligible entry session",
            "entry": "first NYSE session open strictly after archive completion plus configured latency",
            "horizon": "exit close at entry_session_index + H; +1 ends at the following session close",
            "excess_return": "stock adjusted return minus benchmark adjusted return over identical endpoints",
            "large_move": "abs(excess_return) - round_trip_cost_bps / 10000 >= move_threshold",
            "directional_hit": "unique archived direction * excess_return - round_trip_cost_bps / 10000 > 0",
            "cost_interpretation": "linear relative-return hurdle; not an executable net trade or portfolio P&L",
            "event_capture": "at least one temporally admissible ledger sighting in the selected archive",
            "benchmark_completeness": "both exact endpoints from one compatible acquisition for stock and benchmark",
            "coverage": "observed pairs / matured supported cohort members; pending and unsupported remain separately counted",
            "policy_timing": "asserted declaration before/after archive generation; neither independently attests prospective custody",
        },
        "price_contract": {
            "provider": _provider_identity(provider), "interval": DEFAULT_INTERVAL,
            "adjustment_basis": DEFAULT_ADJUSTMENT_BASIS, "fetch_enabled": fetch,
            "currency": "known mismatches refused; two missing currency fields do not establish currency verification",
        },
        "limitations": [
            "Coverage is conditional on archived instrumented membership; whole-market/listing recall is unavailable.",
            "Source failures, rejected candidates and private model decisions are not fully retained by the signal ledger; no-recorded-signal is not a causal diagnosis.",
            "Archive times and declaration times are local assertions; the verifier has no independent custody or full source-freshness attestation.",
            "Failed, declared synthetic and unknown-quality archives are excluded. Absence of a synthetic marker is not independent proof of real data.",
            "Current-vintage adjusted endpoints can revise historical economics; retained acquisition identities make that basis visible.",
            "Missing prices, delistings and provider failures are unknown outcomes, never zero returns or confirmed non-events.",
            "Returns are descriptive adjusted-price comparisons, not fills. OTC eligibility, borrow, taxes, market impact and executable share accounting are not established.",
            "Indexes and continuous futures remain visible but are excluded from stock-relative event and direction metrics.",
            "Same-session securities and overlapping horizons are dependent; no confidence interval, probability calibration or validated predictive edge is claimed.",
            "Friction is a declared linear diagnostic hurdle, not an actual brokerage cost model; use portfolio_lab for supplied exposure-path accounting.",
        ],
    }
    from .diagnostics import build_family_diagnostics
    report["family_diagnostics"] = build_family_diagnostics(report)
    report["report_sha256"] = _sha(report)
    return report


def render_markdown(report):
    """Render counts and version-separated descriptive rates, with denominators."""
    def fraction(numerator, denominator):
        return f"{numerator} / {denominator}" if denominator else "unavailable (n=0)"

    summary = report["summary"]
    lines = [
        "# Archived-cohort signal verification", "",
        f"Outcome cutoff: {report['as_of']}",
        f"Policy: {report['policy']['policy_id']} ({report['policy_sha256']})", "",
        f"Selected {summary['selected_runs']} archived snapshots from {summary['archived_runs']} runs. "
        f"Observed {summary['observed']} of {summary['matured_expected']} matured supported comparisons; "
        f"{summary['unavailable']} unavailable, {summary['pending']} pending, "
        f"{summary['unsupported_asset']} unsupported instrument observations.", "",
        "These are historical event-coverage and directional-agreement diagnostics. "
        "They are not private-bot performance, executable missed trades, or independently verified prospective evidence.", "",
    ]
    for segment in report["segments"]:
        lines.extend([
            f"## Era {segment['pipeline_version']} / {segment['cohort_sha256'][:12]} / {segment['policy_timing']}", "",
            "| Offset | Observed / matured | Missing | Pending | Large moves | Event capture | Direction match | Directional hits |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for horizon, cell in segment["horizons"].items():
            lines.append(
                f"| +{horizon} | {cell['observed']} / {cell['matured_expected']} | "
                f"{cell['unavailable']} | {cell['pending']} | {cell['large_moves']} | "
                f"{fraction(cell['captured_large_moves'], cell['large_moves'])} | "
                f"{fraction(cell['direction_matched_large_moves'], cell['large_moves'])} | "
                f"{fraction(cell['directional_hits_after_hurdle'], cell['directional_observations'])} |"
            )
        lines.extend(["", "Rates use observed complete pairs only. Missing outcomes remain in the coverage denominator.", ""])
        groups = report.get("family_diagnostics", {}).get("groups", [])
        for group in groups:
            if any(group[key] != segment[key] for key in (
                    "pipeline_version", "cohort_sha256", "policy_timing")):
                continue
            lines.extend([
                f"### Signal-family coverage at +{group['horizon']}", "",
                "| Slice | Observed / matured | Large moves / observed | Share of group large moves | Entry sessions |",
                "| --- | ---: | ---: | ---: | ---: |",
            ])
            slices = list(group["cohorts"].items()) + list(group["families"].items())
            for name, cell in slices:
                lines.append(
                    f"| {name.replace('|', '/')} | {cell['observed']} / {cell['matured_expected']} | "
                    f"{cell['large_moves']} / {cell['observed']} | "
                    f"{cell['large_moves']} / {cell['group_observed_large_moves']} | "
                    f"{cell['distinct_entry_sessions']} |"
                )
            lines.extend(["", "Family slices overlap and must not be added as independent contributions. "
                          "Observed yields are descriptive; combined directions are not attributed to families.", ""])
    missed = sorted(
        [row for row in report["observations"]
         if row["large_move"] and row["capture_class"] != "direction_matched"],
        key=lambda row: (-abs(row["excess_return"]), row["pipeline_version"],
                         row["entry_session"], row["horizon"], row["ticker"]),
    )
    if missed:
        lines.extend([
            "## Large moves requiring review", "",
            "Directionless capture means the bot recorded an event without predicting its direction. "
            "Absence from the ledger does not establish why the event was missed. "
            "Review order uses absolute relative-move size, not a forecast or purchase ranking.", "",
            "| Archive | Symbol | Offset | Entry / exit | Relative move | Recorded classification |",
            "| --- | --- | ---: | --- | ---: | --- |",
        ])
        for row in missed[:50]:
            lines.append(
                f"| {row['run_id'].replace('|', '/')} | {row['ticker'].replace('|', '/')} | +{row['horizon']} | "
                f"{row['entry_session']} / {row['exit_session']} | {row['excess_return']:.2%} | {row['capture_class']} |"
            )
        if len(missed) > 50:
            lines.append(f"\nShowing 50 of {len(missed)} cases; the JSON report retains every observation.")
        lines.append("")
    lines.extend(["## Measurement definitions", ""])
    for name in ("entry", "horizon", "large_move", "directional_hit", "cost_interpretation", "policy_timing"):
        lines.append(f"- **{name.replace('_', ' ')}:** {report['definitions'][name]}.")
    lines.extend(["", "## Limits of this evidence", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)
