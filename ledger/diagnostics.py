"""Descriptive signal-family slices of an existing cohort verification report.

This module performs no acquisition, database access, ranking, or model fitting.
It counts ticker/entry-session observations, not the number of signal records.
Families overlap; a combined row-level direction cannot be assigned to each
family. Unknown outcomes are retained in coverage counts and excluded from
observed-outcome summaries, never replaced by a zero return.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import math


SCHEMA = "signal-family-diagnostics-1"
SOURCE_SCHEMA = "signal-verification-report-1"
_STATES = frozenset(("observed", "unavailable", "pending", "unsupported_asset"))
_PARTITION = ("pipeline_version", "cohort_sha256", "policy_timing", "horizon")


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _validate_row(row, index):
    """Refuse inconsistent observations rather than silently changing a cohort."""
    prefix = f"observation {index}"
    if not isinstance(row, dict):
        raise ValueError(f"{prefix} must be an object")
    for name in ("pipeline_version", "cohort_sha256", "policy_timing", "run_id", "ticker", "entry_session"):
        if not isinstance(row.get(name), str) or not row[name]:
            raise ValueError(f"{prefix} needs {name}")
    if row["policy_timing"] not in ("predeclared_policy", "retrospective_policy"):
        raise ValueError(f"{prefix} has unknown policy timing")
    if type(row.get("horizon")) is not int or not 1 <= row["horizon"] <= 252:
        raise ValueError(f"{prefix} needs a supported session horizon")
    try:
        date.fromisoformat(row["entry_session"])
    except ValueError as exc:
        raise ValueError(f"{prefix} has an invalid entry session") from exc
    if row.get("status") not in _STATES:
        raise ValueError(f"{prefix} has an unknown outcome status")
    signal = row.get("signal")
    if not isinstance(signal, dict) or type(signal.get("signal_count")) is not int or signal["signal_count"] < 0:
        raise ValueError(f"{prefix} needs a nonnegative recorded-signal count")
    families = signal.get("families")
    if not isinstance(families, list) or any(not isinstance(family, str) or not family for family in families):
        raise ValueError(f"{prefix} needs recorded family names")
    families = frozenset(families)
    if bool(families) != bool(signal["signal_count"]) or len(families) > signal["signal_count"]:
        raise ValueError(f"{prefix} has inconsistent signal and family counts")
    if row["status"] == "observed":
        excess = row.get("excess_return")
        if (isinstance(excess, bool) or not isinstance(excess, (int, float))
                or not math.isfinite(excess) or type(row.get("large_move")) is not bool):
            raise ValueError(f"{prefix} needs a finite observed excess and event flag")
    elif row.get("excess_return") is not None or row.get("large_move") is not None:
        raise ValueError(f"{prefix} assigns an outcome to an unobserved comparison")
    return families


def _mean_absolute(rows):
    if not rows:
        return None
    absolute = [abs(row["excess_return"]) for row in rows]
    scale = max(absolute)
    # Scaling avoids overflowing a sum of individually finite returns.
    return scale * (math.fsum(value / scale for value in absolute) / len(absolute)) if scale else 0.0


def _slice(rows, group_large_moves):
    states = Counter(row["status"] for row in rows)
    observed = [row for row in rows if row["status"] == "observed"]
    large = [row for row in observed if row["large_move"]]
    captured = sum(row["signal"]["signal_count"] > 0 for row in large)
    matured = len(observed) + states["unavailable"]
    return {
        "expected_observations": len(rows),
        "supported_observations": len(rows) - states["unsupported_asset"],
        "matured_expected": matured,
        "observed": len(observed),
        "unavailable": states["unavailable"],
        "pending": states["pending"],
        "unsupported_asset": states["unsupported_asset"],
        "expected_entry_sessions": len({row["entry_session"] for row in rows}),
        "distinct_entry_sessions": len({row["entry_session"] for row in observed}),
        "outcome_coverage": _ratio(len(observed), matured),
        "large_moves": len(large),
        "large_move_yield_observed": _ratio(len(large), len(observed)),
        "group_observed_large_moves": group_large_moves,
        "large_move_share_observed": _ratio(len(large), group_large_moves),
        "captured_large_moves": captured,
        "event_capture_rate_observed": _ratio(captured, group_large_moves),
        "mean_absolute_excess_return_observed": _mean_absolute(observed),
    }


def build_family_diagnostics(report):
    """Summarize one verifier report without mutating or dropping its rows.

    Groups never cross an era, frozen cohort, policy-timing partition or horizon.
    Each group contains complementary signal-present/signal-absent cohorts and
    one overlapping slice for every family actually recorded in that group.
    Absent families are not manufactured as zero-observation comparisons.

    ``event_capture_rate_observed`` uses all observed large moves in the group
    as its denominator. For the no-recorded-signal cohort its numerator is zero;
    ``large_move_share_observed`` separately shows the missed-event share.
    Large-move flags and benchmark excesses come from the supplied verifier;
    this function does not recalculate its policy or attest its source data.
    """
    if not isinstance(report, dict) or report.get("schema_version") != SOURCE_SCHEMA:
        raise ValueError("a supported signal verification report is required")
    observations = report.get("observations")
    if not isinstance(observations, list):
        raise ValueError("verification observations must be a list")
    grouped, seen = defaultdict(list), set()
    for index, row in enumerate(observations):
        families = _validate_row(row, index)
        partition = tuple(row[name] for name in _PARTITION)
        identity = (*partition, row["ticker"], row["entry_session"])
        if identity in seen:
            raise ValueError("duplicate ticker/entry observation within a diagnostic group")
        seen.add(identity)
        grouped[partition].append((row, families))

    groups = []
    for partition, members in sorted(grouped.items()):
        rows = [row for row, _ in members]
        group_large_moves = sum(row["status"] == "observed" and row["large_move"] for row in rows)
        by_family = defaultdict(list)
        for row, families in members:
            for family in families:
                by_family[family].append(row)
        groups.append({
            **dict(zip(_PARTITION, partition)),
            "population": _slice(rows, group_large_moves),
            "cohorts": {
                "no_recorded_signal": _slice(
                    [row for row in rows if not row["signal"]["signal_count"]], group_large_moves),
                "any_recorded_signal": _slice(
                    [row for row in rows if row["signal"]["signal_count"]], group_large_moves),
            },
            "families": {family: _slice(family_rows, group_large_moves)
                         for family, family_rows in sorted(by_family.items())},
            "multi_family_observations": sum(len(families) > 1 for _, families in members),
            "family_observation_memberships": sum(len(families) for _, families in members),
        })
    return {
        "schema_version": SCHEMA,
        "source_schema_version": SOURCE_SCHEMA,
        "as_of": report.get("as_of"),
        "policy_sha256": report.get("policy_sha256"),
        "observation_count": len(observations),
        "group_count": len(groups),
        "groups": groups,
        "rates_pooled": False,
        "directional_attribution": False,
        "inference_status": "descriptive_only",
        "definitions": {
            "observation": "one ticker/entry-session/horizon in the verifier's selected archive population",
            "group": "pipeline_version, cohort_sha256, policy_timing and horizon are all held fixed",
            "matured_expected": "observed plus unavailable; pending and unsupported comparisons remain separate",
            "outcome_coverage": "observed / matured_expected within the slice",
            "distinct_entry_sessions": "distinct entry sessions among observed outcomes; expected_entry_sessions includes all statuses",
            "large_move_yield_observed": "observed large moves in the slice / observed comparisons in that slice",
            "large_move_share_observed": "observed large moves in the slice / all observed large moves in the group",
            "event_capture_rate_observed": "observed large moves with a recorded signal in the slice / all observed large moves in the group",
            "mean_absolute_excess_return_observed": "unweighted mean of abs(excess_return) over observed comparisons; no extra friction deduction",
            "family_membership": "one membership per observation per recorded family, regardless of repeated records in that family",
        },
        "limitations": [
            "Signal-present and signal-absent populations can differ; these comparisons do not establish causal effects or alpha.",
            "Family slices overlap. Their counts and event-capture rates must not be added as independent contributions.",
            "The verifier's combined row direction is not attributed to individual families; family directional accuracy is unavailable.",
            "Missing outcomes remain unknown. Observed-only yields and averages can be affected by selective price coverage.",
            "Same-session securities, repeated events and overlapping horizons are dependent; no confidence interval or predictive accuracy is claimed.",
            "Coverage is limited to retained, eligible archive cohorts; no-recorded-signal does not establish why a signal was absent.",
            "The input report retains every observation; this sidecar adds descriptive aggregates without certifying provenance or trading feasibility.",
        ],
    }
