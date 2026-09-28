"""Offline M17 checks for typed cash-runway missingness and lineage."""

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import agents.research_agent as research_module
from agents.research_agent import ResearchAgent


def _balance_sheet(
    cash=10_000_000,
    debt_marker=None,
    period="2026-06-30",
    extra_cash=None,
):
    rows = {"Cash And Cash Equivalents": cash}
    if debt_marker is not None:
        rows["Total Debt"] = debt_marker
    if extra_cash is not None:
        rows["Cash Cash Equivalents And Short Term Investments"] = extra_cash
    column = pd.Timestamp(period) if period is not None else None
    return pd.DataFrame({column: rows})


def _cash_flow(metric, values, periods=None):
    if periods is None:
        periods = [
            "2026-06-30",
            "2026-03-31",
            "2025-12-31",
            "2025-09-30",
        ][:len(values)]
    columns = [pd.Timestamp(period) if period is not None else None
               for period in periods]
    return pd.DataFrame([values], index=[metric], columns=columns)


def _check(balance_sheet, cash_flow, info=None):
    stock = SimpleNamespace(
        quarterly_balance_sheet=balance_sheet,
        quarterly_cashflow=cash_flow,
        info=info or {},
    )
    fake_yf = SimpleNamespace(Ticker=lambda ticker: stock)
    with patch.object(research_module, "HAS_YFINANCE", True), patch.object(
        research_module, "yf", fake_yf
    ):
        return ResearchAgent().check_cash_runway("SYNTHETIC")


# M17: the actual method must not turn an absent table into positive cash flow.
missing = _check(_balance_sheet(), None)
assert missing["quarterly_burn"] is None, missing
assert missing["runway_quarters"] is None, missing
assert missing["risk_level"] == "UNKNOWN", missing
assert missing["reason"] == "missing_cashflow", missing
assert missing["cash_flow_status"] == "unavailable", missing
assert missing["cash_flow_positive"] is None, missing
assert missing["valid_quarter_count"] == 0, missing
assert missing["total_debt"] is None, missing
assert missing["market_cap"] is None, missing
assert missing["cash_metric"] == "Cash And Cash Equivalents", missing
assert missing["cash_period_end"] == "2026-06-30", missing
assert missing["source_time_available"] is False, missing


# Non-current liabilities are not a substitute for a reported debt metric.
liabilities_only_balance = _balance_sheet()
liabilities_only_balance.loc[
    "Total Non Current Liabilities Net Minority Interest"
] = 9_000_000
liabilities_only = _check(
    liabilities_only_balance,
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert liabilities_only["total_debt"] is None, liabilities_only
assert liabilities_only["debt_metric"] is None, liabilities_only


# A present but wholly non-finite OCF row is unavailable, not measured zero.
non_finite = _check(
    _balance_sheet(),
    _cash_flow("Operating Cash Flow", [float("nan"), float("inf"),
                                        float("-inf"), None]),
    {"financialCurrency": "USD"},
)
assert non_finite["quarterly_burn"] is None, non_finite
assert non_finite["runway_quarters"] is None, non_finite
assert non_finite["risk_level"] == "UNKNOWN", non_finite
assert non_finite["reason"] == "non_finite_cashflow", non_finite
assert non_finite["valid_quarter_count"] == 0, non_finite
assert non_finite["non_finite_quarter_count"] == 3, non_finite


# Genuine zero and positive OCF stay measured but have no invented infinity.
zero_flow = _check(
    _balance_sheet(),
    _cash_flow("Operating Cash Flow", [0, 0, 0, 0]),
    {"financialCurrency": "USD"},
)
assert zero_flow["quarterly_burn"] == 0, zero_flow
assert zero_flow["runway_quarters"] is None, zero_flow
assert zero_flow["risk_level"] == "UNKNOWN", zero_flow
assert zero_flow["reason"] == "zero_cash_flow", zero_flow
assert zero_flow["cash_flow_status"] == "zero", zero_flow
assert zero_flow["cash_flow_positive"] is False, zero_flow

positive_flow = _check(
    _balance_sheet(),
    _cash_flow("Operating Cash Flow", [2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert positive_flow["quarterly_burn"] == 2_000_000, positive_flow
assert positive_flow["runway_quarters"] is None, positive_flow
assert positive_flow["risk_level"] == "UNKNOWN", positive_flow
assert positive_flow["reason"] == "positive_cash_flow", positive_flow
assert positive_flow["cash_flow_status"] == "positive", positive_flow
assert positive_flow["cash_flow_positive"] is True, positive_flow


# Valid negative OCF preserves the existing finite-runway thresholds and lineage.
negative_flow = _check(
    _balance_sheet(debt_marker=3_000_000),
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "usd", "marketCap": 50_000_000},
)
assert negative_flow["quarterly_burn"] == -2_000_000, negative_flow
assert negative_flow["runway_quarters"] == 5.0, negative_flow
assert negative_flow["risk_level"] == "YELLOW", negative_flow
assert negative_flow["reason"] is None, negative_flow
assert negative_flow["cash_flow_metric"] == "Operating Cash Flow", negative_flow
assert negative_flow["cash_flow_period_end"] == "2026-06-30", negative_flow
assert negative_flow["valid_quarter_count"] == 4, negative_flow
assert negative_flow["period_match"] is True, negative_flow
assert negative_flow["currency"] == "USD", negative_flow
assert negative_flow["source_time_available"] is True, negative_flow
assert negative_flow["debt_metric"] == "Total Debt", negative_flow


# Free cash flow is retained as a distinct provider row, never used as OCF.
fcf_only = _check(
    _balance_sheet(),
    _cash_flow("Free Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert fcf_only["cash_flow_metric"] is None, fcf_only
assert fcf_only["quarterly_burn"] is None, fcf_only
assert fcf_only["runway_quarters"] is None, fcf_only
assert fcf_only["risk_level"] == "UNKNOWN", fcf_only
assert fcf_only["reason"] == "missing_operating_cash_flow", fcf_only


# A stale OCF period remains a measurement, but cannot produce a runway class.
period_mismatch = _check(
    _balance_sheet(period="2026-06-30"),
    _cash_flow(
        "Cash Flow From Continuing Operating Activities",
        [-2_000_000] * 4,
        ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"],
    ),
    {"financialCurrency": "USD"},
)
assert period_mismatch["quarterly_burn"] == -2_000_000, period_mismatch
assert period_mismatch["cash_flow_status"] == "negative", period_mismatch
assert period_mismatch["runway_quarters"] is None, period_mismatch
assert period_mismatch["risk_level"] == "UNKNOWN", period_mismatch
assert period_mismatch["reason"] == "period_mismatch", period_mismatch
assert period_mismatch["period_match"] is False, period_mismatch
assert period_mismatch["cash_flow_metric"] == (
    "Cash Flow From Continuing Operating Activities"
), period_mismatch


# Ambiguous financial currency blocks the derived class without erasing OCF.
currency_ambiguous = _check(
    _balance_sheet(),
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD/EUR"},
)
assert currency_ambiguous["quarterly_burn"] == -2_000_000, currency_ambiguous
assert currency_ambiguous["runway_quarters"] is None, currency_ambiguous
assert currency_ambiguous["risk_level"] == "UNKNOWN", currency_ambiguous
assert currency_ambiguous["reason"] == "currency_ambiguous", currency_ambiguous
assert currency_ambiguous["currency"] is None, currency_ambiguous
assert currency_ambiguous["currency_ambiguous"] is True, currency_ambiguous


# Keep source priority explicit rather than selecting the largest cash figure.
# Runway counts liquid resources, so the combined cash + short-term investments
# line comes first: many biotechs hold most of their cash in short-term
# investments (live 2026-09-23: CRSP read GREEN 25.6Q -> RED 3.2Q and NTLA
# YELLOW 5.1Q -> RED 1.2Q when only the narrow cash line was used).
priority = _check(
    _balance_sheet(cash=10_000_000, extra_cash=20_000_000),
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert priority["cash_and_equivalents"] == 20_000_000, priority
assert priority["cash_metric"] == (
    "Cash Cash Equivalents And Short Term Investments"
), priority
assert priority["runway_quarters"] == 10.0, priority
assert priority["risk_level"] == "GREEN", priority

# Priority, not magnitude: the combined line wins even when it is smaller.
not_largest = _check(
    _balance_sheet(cash=30_000_000, extra_cash=20_000_000),
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert not_largest["cash_and_equivalents"] == 20_000_000, not_largest
assert not_largest["cash_metric"] == (
    "Cash Cash Equivalents And Short Term Investments"
), not_largest

# A non-finite combined line falls through to the next named metric.
nan_combined = _check(
    _balance_sheet(cash=10_000_000, extra_cash=float("nan")),
    _cash_flow("Operating Cash Flow", [-2_000_000] * 4),
    {"financialCurrency": "USD"},
)
assert nan_combined["cash_and_equivalents"] == 10_000_000, nan_combined
assert nan_combined["cash_metric"] == "Cash And Cash Equivalents", nan_combined


# Finite quarters remain usable when sibling cells are non-finite.
partial = _check(
    _balance_sheet(),
    _cash_flow(
        "Operating Cash Flow",
        [float("nan"), -2_000_000, float("inf"), -4_000_000],
    ),
    {"financialCurrency": "USD"},
)
assert partial["quarterly_burn"] == -3_000_000, partial
assert partial["valid_quarter_count"] == 2, partial
assert partial["non_finite_quarter_count"] == 2, partial


print("cash runway M17 missingness checks passed")
