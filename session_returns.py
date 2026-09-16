"""Explicit session-window returns for descriptive basket and pair comparisons.

NYSE date alignment is not synchronized intraday pricing. Context futures use
these comparison dates, not a claim that NYSE is their native trading calendar.
"""
from __future__ import annotations

from datetime import date, timedelta
import math
from timeutil import oldest, to_utc_z


def comparison_sessions(latest_session, horizon, calendar=None):
    if type(horizon) is not int or not 1 <= horizon <= 60:
        raise ValueError("horizon must be between 1 and 60 sessions")
    if latest_session is None:
        return []
    latest = date.fromisoformat(latest_session)
    if calendar is None:
        import pandas_market_calendars as mcal
        calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=latest - timedelta(days=180), end_date=latest)
    labels = [label.date().isoformat() for label in schedule.index]
    if not labels or labels[-1] != latest_session or len(labels) < horizon + 1:
        return []
    return labels[-(horizon + 1):]


def aligned_return(history, sessions):
    """Require all n+1 dated bars, including the exact current settled endpoint."""
    result = {"return_pct": None, "as_of": None, "eligible": False,
              "reason": None, "start_session": sessions[0] if sessions else None,
              "end_session": sessions[-1] if sessions else None,
              "horizon_sessions": len(sessions) - 1 if sessions else None}
    if not sessions or len(sessions) < 2:
        return {**result, "reason": "calendar_window_unavailable"}
    if history is None or history.empty or "Close" not in history:
        return {**result, "reason": "history_unavailable"}
    closes, times = {}, {}
    required = set(sessions)
    for index, value in history["Close"].items():
        try:
            label = index.date().isoformat()
        except (AttributeError, ValueError):
            continue
        if label not in required:
            continue
        if label in closes:
            return {**result, "reason": "duplicate_session"}
        try:
            price = float(value)
        except (ValueError, TypeError, OverflowError):
            return {**result, "reason": "invalid_price"}
        if isinstance(value, bool) or not math.isfinite(price) or price <= 0:
            return {**result, "reason": "nonpositive_or_nonfinite_price"}
        closes[label], times[label] = price, to_utc_z(index)
    missing = sorted(required - set(closes))
    if missing:
        return {**result, "reason": "missing_sessions", "missing_sessions": missing}
    change = 100.0 * (closes[sessions[-1]] / closes[sessions[0]] - 1.0)
    if not math.isfinite(change):
        return {**result, "reason": "nonfinite_return"}
    return {**result, "return_pct": change, "as_of": times[sessions[-1]],
            "eligible": times[sessions[-1]] is not None,
            "reason": None if times[sessions[-1]] is not None else "source_time_unavailable",
            "end_price": closes[sessions[-1]]}


def basket_return(histories, tickers, sessions):
    """Equal-weight fixed-membership comparison; partial baskets are not zero."""
    members = list(dict.fromkeys(tickers))
    measurements = {ticker: aligned_return(histories.get(ticker), sessions) for ticker in members}
    used = [ticker for ticker, measurement in measurements.items() if measurement["eligible"]]
    complete = bool(members) and len(used) == len(members)
    return {
        "return_pct": (sum(measurements[ticker]["return_pct"] for ticker in used) / len(used)
                       if complete else None),
        "as_of": oldest(*(measurements[ticker]["as_of"] for ticker in used)) if complete else None,
        "eligible": complete, "expected": len(members), "available": len(used),
        "excluded": {ticker: measurement["reason"] for ticker, measurement in measurements.items()
                     if not measurement["eligible"]},
        "start_session": sessions[0] if sessions else None,
        "end_session": sessions[-1] if sessions else None,
        "aggregation": "equal_weight_full_membership",
    }


def format_percent(value, digits=1):
    """Display missing/nonfinite measurements without coercing them to zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "N/A"
    return f"{value:+.{digits}f}%"
