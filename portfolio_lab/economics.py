"""One-year nominal scenario arithmetic, with no investment predictions.

All rates and financial amounts are explicitly supplied by the caller. A rate
is an assumed one-year total return before the stated fixed overhead, not a
dividend yield, sustainable withdrawal rate or promised monthly income. The
identity is gain = capital * rate - overhead; a monthly equivalent divides
that result by twelve. Capital required for a supplied annual cash target is
(target + overhead) / rate, defined here only for positive rates.

This module uses no market observations or product recommendations. It does not
model the path of returns, within-year cash availability, tax, FX, inflation,
fees other than the stated overhead, or changes in withdrawal purchasing power.
The withdrawal comparison deducts the annual target at the end of the year;
it is not a simulation of monthly withdrawals or sequence-of-returns risk.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def _finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def income_scenarios(
    capital: float,
    annual_rates: Iterable[float],
    *,
    annual_fixed_cost: float = 0.0,
    monthly_income_goal: float = 0.0,
    currency: str = "USD",
) -> dict[str, Any]:
    """Compare caller-supplied assumptions without presenting them as yields.

    Rates are fractions: ``0.04`` is a hypothetical 4%, ``-0.20`` a 20% loss.
    Positive starting capital, nonnegative overhead/goal, and rates >= -1 are
    required for this unlevered arithmetic. Nonpositive assumed rates produce
    ``None`` for required capital rather than an infinite or negative corpus.
    Negative ending-capital arithmetic is retained and explicitly flagged as
    a funding shortfall, never disguised as a feasible funded withdrawal.
    Inputs are not mutated, and every numeric output must be finite JSON data.
    """
    amount = _finite(capital, "capital", minimum=0.0)
    if amount == 0:
        raise ValueError("capital must be positive")
    overhead = _finite(annual_fixed_cost, "annual_fixed_cost", minimum=0.0)
    goal = _finite(monthly_income_goal, "monthly_income_goal", minimum=0.0)
    if currency not in ("USD", "INR"):
        raise ValueError("currency must be USD or INR")
    if isinstance(annual_rates, (str, bytes, dict)) or not isinstance(annual_rates, Iterable):
        raise ValueError("annual_rates must be a nonempty iterable of numbers")

    annual_goal = _finite(goal * 12.0, "annual goal")
    break_even = _finite(overhead / amount, "fixed-cost break-even rate")
    scenarios = []
    for index, supplied_rate in enumerate(annual_rates):
        if index >= 1000:
            raise ValueError("at most 1000 annual rate assumptions are supported")
        rate = _finite(supplied_rate, f"annual_rates[{index}]", minimum=-1.0)
        gross = _finite(amount * rate, "gross annual change")
        net = _finite(gross - overhead, "net annual change")
        reinvested = _finite(amount + net, "ending capital if reinvested")
        withdrawn = _finite(reinvested - annual_goal, "ending capital after goal withdrawal")
        required = None
        reason = "nonpositive_assumed_rate"
        if rate > 0:
            target_with_cost = _finite(annual_goal + overhead, "goal plus overhead")
            required = _finite(target_with_cost / rate, "required capital")
            reason = None
        scenarios.append({
            "annual_return_assumption": rate,
            "gross_annual_change": gross,
            "net_annual_change_after_fixed_cost": net,
            "monthly_equivalent_after_fixed_cost": net / 12.0,
            "ending_capital_if_reinvested": reinvested,
            "ending_capital_after_goal_withdrawal": withdrawn,
            "annual_goal_minus_net_change": _finite(annual_goal - net, "annual gap to goal"),
            "goal_covered_by_assumed_net_change": net >= annual_goal,
            "reinvestment_case_has_funding_shortfall": reinvested < 0,
            "withdrawal_case_has_funding_shortfall": withdrawn < 0,
            "required_capital_for_goal": required,
            "required_capital_reason": reason,
        })
    if not scenarios:
        raise ValueError("annual_rates must not be empty")
    return {
        "schema_version": "portfolio-lab.economics.v1",
        "research_only": True,
        "currency": currency,
        "capital": amount,
        "annual_fixed_cost": overhead,
        "monthly_income_goal": goal,
        "annual_goal_withdrawal": annual_goal,
        "fixed_cost_break_even_annual_rate": break_even,
        "projection_basis": "one_year_nominal_end_of_year_cost_and_withdrawal",
        "limitations": [
            "User-supplied return assumptions are not forecasts or sustainable withdrawal rates.",
            "Monthly equivalent is an annual result divided by twelve, not a promised cash payout.",
            "Withdrawals are deducted at year end; monthly liquidity and return sequence are not modeled.",
            "No tax, FX, inflation or costs other than the stated fixed overhead are modeled.",
            "Negative capital arithmetic indicates an unfunded scenario, not permitted borrowing.",
        ],
        "scenarios": scenarios,
    }
