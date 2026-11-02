"""One fixed historical reference rule, without selection or optimization.

This is a retrospective accounting sanity check.  It is not the user's private
bot, a suggested portfolio, or a live decision generator.  The caller must
provide independently sourced exchange month-end session dates, preserve that
schedule with the study, and verify its market calendar.  A date appearing in
this list is an assertion, not a certification that the exchange was open or
that it was the month's final session.
"""
from __future__ import annotations

from datetime import timedelta
import math

from .data import (
    SYMBOL, digest, session_date, utc, validate_dataset, validate_decisions,
    validate_study,
)


def reference_rule(*, symbol, months=10, equity_weight=1.0):
    """Return the exact, hashable algorithm specification for this reference.

    The independent calendar schedule is an additional data input, not a rule
    parameter inferred by inspecting the final observed row of each month.
    """
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("reference symbol must be a valid declared asset identifier")
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 120:
        raise ValueError("months must be an integer between one and 120")
    if (isinstance(equity_weight, bool) or not isinstance(equity_weight, (int, float))
            or not math.isfinite(equity_weight) or not 0 <= equity_weight <= 1):
        raise ValueError("equity_weight must be finite and between zero and one")
    return {
        "schema_version": "portfolio-lab.reference-rule.v1",
        "rule_id": "monthly-sma-long-or-cash-v1",
        "symbol": symbol, "months": months, "equity_weight": float(equity_weight),
        "price_basis": "total_return_proxy",
        "calendar_policy": "independently_supplied_exchange_month_end_sessions",
        "observation_window": "consecutive_monthly_closes_including_current_completed_month",
        "warmup": "no_decision_until_full_window",
        "condition": "current_close_strictly_greater_than_arithmetic_mean",
        "otherwise": "cash_including_equality",
        "decision_time": "completed_month_end_close_plus_one_second",
        "execution": "deferred_to_separate_engine_next_eligible_open",
        "origin": "retrospective",
        "optimization": False,
    }


def _month_ends(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 30000:
        raise ValueError("month_end_sessions must be a nonempty bounded date sequence")
    parsed = [session_date(item) for item in value]
    if list(value) != sorted(set(value)):
        raise ValueError("month-end sessions must be unique and chronological")
    ordinals = [item.year * 12 + item.month for item in parsed]
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("supply exactly one asserted month-end session per month")
    if any(right != left + 1 for left, right in zip(ordinals, ordinals[1:])):
        raise ValueError("month-end schedule has a missing calendar month")
    return list(value)


def monthly_sma_decisions(study, dataset, *, symbol, months=10,
                          equity_weight=1.0, month_end_sessions):
    """Generate only retrospective reference decisions from completed closes.

    The current completed month is included in the ``months`` observations.
    Equality and a below-average close produce cash.  No decision appears
    during warmup.  The engine, not this rule, determines later hypothetical
    fills and their costs.  Schedule dates after the available data are ignored;
    appending prices therefore cannot turn a formerly partial month into an
    earlier signal.  Dates within the observed interval must have actual rows.

    Preserve ``month_end_sessions`` alongside the saved inputs: the algorithm
    fingerprint intentionally does not change when the calendar is extended.
    """
    validate_study(study)
    validate_dataset(dataset, study)
    rule = reference_rule(symbol=symbol, months=months, equity_weight=equity_weight)
    if study["strategy_sha256"] != digest(rule):
        raise ValueError("study strategy_sha256 must match the exact reference_rule fingerprint")
    if symbol not in study["assets"]:
        raise ValueError("reference symbol must be included in the study assets")
    if equity_weight > min(study["limits"]["max_weight"], study["limits"]["max_gross"]):
        raise ValueError("reference equity_weight exceeds the study's declared limits")
    dates = _month_ends(month_end_sessions)
    rows = {row["session"]: row for row in dataset["sessions"]}
    first, last = dataset["expected_sessions"][0], dataset["expected_sessions"][-1]
    closes, decisions = [], []
    for day in dates:
        if day < first or day > last:
            continue
        if day not in rows:
            raise ValueError("An asserted month-end inside the observed interval has no price row")
        row = rows[day]
        close = row["prices"][symbol]["close"]
        closes.append(close)
        if len(closes) < months:
            continue
        # Division before summation avoids an unnecessary overflow for a
        # finite mean of individually large, but valid, proxy prices.
        average = math.fsum(value / months for value in closes[-months:])
        weight = float(equity_weight) if close > average else 0.0
        decided_at = utc(row["close_at"]) + timedelta(seconds=1)
        decisions.append({
            "decision_id": f"sma-{symbol}-{day}",
            "strategy_sha256": study["strategy_sha256"],
            "decided_at": decided_at.isoformat(), "information_cutoff": row["close_at"],
            "weights": {symbol: weight} if weight else {},
            "origin": "retrospective", "evidence": [],
        })
    return validate_decisions(decisions, study)
