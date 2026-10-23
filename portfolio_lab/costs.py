"""Explicit fee assumptions for hypothetical cash-delivery research.

The generic model is operator-supplied, not a certification of any broker tariff.
The India model is one prospective estimate dated 2026-10-23. Applying that
estimate to an old price path is a current-cost sensitivity exercise, not a
reconstruction of historical charges. It covers NSE cash delivery only, assumes
at most one trade per instrument per day, and omits contract-note rounding,
special account tariffs, personal capital-gains tax, FX, spreads and slippage.
``dp_sell_fee`` is REQUIRED and ALREADY INCLUDES GST; no holder characteristic
or DP tariff is inferred. The caller supplies instrument tax classification.

Primary sources reviewed on 2026-10-23:
* https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies
  Delivery shares: STT 0.1% each side; equity-oriented fund units: nil on
  purchase and 0.001% on sale. Stamp duty: 0.015% on cash-delivery purchases.
* https://nsearchives.nseindia.com/content/circulars/FA73061.pdf
  From 2026-03-01, cash transaction charge plus IPFT totals INR 307 per crore
  each side. The component split changed; the total must not be counted twice.
* https://zerodha.com/charges/
  Delivery brokerage zero; SEBI fee INR 10/crore; GST 18% on applicable fees.
* https://upstox.com/brokerage-charges/
  Delivery brokerage INR 20 per executed order, excluding other charges.
* https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/stt-etfs
  Non-equity classifications such as gold, liquid and gilt ETFs can have no STT.
* https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/what-do-dp-charges-mean
  DP charges apply per sold instrument/day; account/instrument concessions vary.

``non_equity_etf`` means an operator-verified STT-exempt ETF in this model;
neither a ticker nor a generic overseas-equity label proves that tax treatment.
"""

from __future__ import annotations

import math
from typing import Any


INDIA_TARIFF_AS_OF = "2026-10-23"
ASSET_KINDS = frozenset({"equity", "equity_etf", "non_equity_etf"})
_COMPONENTS = (
    "brokerage",
    "transaction_and_ipft",
    "sebi_fee",
    "stt",
    "stamp_duty",
    "gst",
    "dp_fee_including_gst",
)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def validate_cost_model(model: dict[str, Any], currency: str) -> None:
    """Reject unknown/missing fields, ambiguous tariffs and currency mismatch.

    Accept only the reviewed India snapshot date. A new tariff requires a new
    implementation/snapshot rather than changing the date on old constants.
    The input dictionary is never mutated.
    """
    if not isinstance(model, dict):
        raise ValueError("cost_model must be an object")
    if currency not in ("USD", "INR"):
        raise ValueError("cost-model currency must be USD or INR")
    kind = model.get("kind")
    if kind == "generic":
        fields = {"kind", "buy_bps", "sell_bps", "fixed_per_order"}
        if set(model) != fields:
            raise ValueError("generic cost_model requires exactly kind, buy_bps, sell_bps, fixed_per_order")
        for field in fields - {"kind"}:
            _number(model[field], field)
    elif kind == "india_delivery":
        fields = {"kind", "broker", "as_of", "dp_sell_fee"}
        if set(model) != fields:
            raise ValueError("india_delivery requires exactly kind, broker, as_of, dp_sell_fee")
        if currency != "INR":
            raise ValueError("india_delivery cost_model requires INR")
        if model["broker"] not in ("zerodha", "upstox"):
            raise ValueError("india_delivery broker must be zerodha or upstox")
        if model["as_of"] != INDIA_TARIFF_AS_OF:
            raise ValueError(f"only the India cost snapshot {INDIA_TARIFF_AS_OF} is implemented")
        _number(model["dp_sell_fee"], "dp_sell_fee (including GST)")
    else:
        raise ValueError("cost_model kind must be generic or india_delivery")


def trade_cost(
    model: dict[str, Any],
    asset_kind: str,
    side: str,
    notional: float,
    *,
    multiplier: float = 1.0,
) -> dict[str, float]:
    """Return fee components and their ``total`` in the notional's currency.

    This function has no orders, positions, account access or network effects.
    Zero notional has zero fees, including fixed ticket and DP costs. Scaling
    applies to ALL components for explicit cost sensitivity, not to slippage.
    The engine validates the actual study currency with ``validate_cost_model``.
    Values are left unrounded so a caller can aggregate hypothetical costs;
    they do not reproduce settlement-level or contract-note rounding.
    """
    if not isinstance(model, dict):
        raise ValueError("cost_model must be an object")
    validate_cost_model(model, "INR" if model.get("kind") == "india_delivery" else "USD")
    if not isinstance(asset_kind, str) or asset_kind not in ASSET_KINDS:
        raise ValueError("asset_kind must be equity, equity_etf or non_equity_etf")
    if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    side = side.upper()
    amount = _number(notional, "notional")
    scale = _number(multiplier, "multiplier")
    result = dict.fromkeys(_COMPONENTS, 0.0)
    if amount == 0 or scale == 0:
        return {**result, "total": 0.0}

    if model["kind"] == "generic":
        bps = float(model["buy_bps"] if side == "BUY" else model["sell_bps"])
        result["brokerage"] = amount * (bps / 10_000.0) + float(model["fixed_per_order"])
    else:
        result["brokerage"] = 0.0 if model["broker"] == "zerodha" else 20.0
        result["transaction_and_ipft"] = amount * 0.0000307
        result["sebi_fee"] = amount * 0.000001
        if asset_kind == "equity":
            result["stt"] = amount * 0.001
        elif asset_kind == "equity_etf" and side == "SELL":
            result["stt"] = amount * 0.00001
        result["stamp_duty"] = amount * 0.00015 if side == "BUY" else 0.0
        result["gst"] = 0.18 * (
            result["brokerage"] + result["transaction_and_ipft"] + result["sebi_fee"]
        )
        if side == "SELL":
            result["dp_fee_including_gst"] = float(model["dp_sell_fee"])

    for component in result:
        result[component] *= scale
        if not math.isfinite(result[component]):
            raise ValueError("trade cost overflows finite arithmetic")
    try:
        total = math.fsum(result.values())
    except OverflowError as exc:
        raise ValueError("trade cost overflows finite arithmetic") from exc
    if not math.isfinite(total):
        raise ValueError("trade cost overflows finite arithmetic")
    result["total"] = total
    return result
