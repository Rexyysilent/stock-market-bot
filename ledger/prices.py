"""Adjusted OHLC cache backed by yfinance; outcome endpoints fail closed."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta

logger = logging.getLogger("SignalLedger.Prices")


def _price(value):
    """Return a finite positive price, or None when a return is not defined."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _clean_rows(rows):
    cleaned = {}
    for row in rows:
        session = date.fromisoformat(row["session"]).isoformat()
        cleaned[session] = {
            "session": session,
            "open": _price(row.get("open")),
            "close": _price(row.get("close")),
        }
    return list(cleaned.values())


def fetch_yfinance(ticker, start, end):
    """Return adjusted daily OHLC rows; ``end`` is inclusive to callers."""
    import yfinance as yf
    from yfinance_util import configure_yfinance_cache
    configure_yfinance_cache(yf)

    frame = yf.download(
        ticker,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return []
    if getattr(frame.columns, "nlevels", 1) > 1:
        frame.columns = frame.columns.get_level_values(0)
    return _clean_rows([
        {"session": index.date().isoformat(),
         "open": row.get("Open"), "close": row.get("Close")}
        for index, row in frame.iterrows()
    ])


def cache_rows(conn, ticker, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO prices(ticker,session,open,close) VALUES(?,?,?,?)",
        ((ticker, row["session"], row["open"], row["close"])
         for row in _clean_rows(rows)),
    )


def ensure_window(conn, ticker, start, end, provider=fetch_yfinance):
    """Require the entry open AND exit close, not just one overlapping row.

    Returns ok, empty, incomplete, or error. An incomplete acquisition remains
    retryable. A refill must supply both valid endpoints in the same response;
    do not splice a newly adjusted exit onto a cached pre-adjustment entry.
    This endpoint contract does not assert interior-session completeness or
    retroactively verify the adjustment basis of legacy cached rows.
    """
    if end < start:
        raise ValueError("price window end must not precede start")
    first, last = start.isoformat(), end.isoformat()
    if get_open_close(conn, ticker, first, last) is not None:
        return "ok"
    try:
        rows = _clean_rows(provider(ticker, start, end))
    except Exception as exc:
        logger.warning("price fetch failed for %s: %s", ticker, exc)
        return "error"
    if not rows:
        return "empty"
    endpoints = {row["session"]: row for row in rows}
    if (endpoints.get(first, {}).get("open") is None
            or endpoints.get(last, {}).get("close") is None):
        return "incomplete"
    cache_rows(conn, ticker, [row for row in rows if first <= row["session"] <= last])
    conn.commit()
    return "ok"


def get_open_close(conn, ticker, entry_session, exit_session):
    entry = conn.execute(
        "SELECT open FROM prices WHERE ticker=? AND session=?",
        (ticker, entry_session),
    ).fetchone()
    exit_row = conn.execute(
        "SELECT close FROM prices WHERE ticker=? AND session=?",
        (ticker, exit_session),
    ).fetchone()
    opened = _price(entry["open"]) if entry else None
    closed = _price(exit_row["close"]) if exit_row else None
    return (opened, closed) if opened is not None and closed is not None else None
