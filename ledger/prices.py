"""Adjusted OHLC cache backed by yfinance."""
from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger("SignalLedger.Prices")


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
    # yfinance may return MultiIndex columns even for a single ticker.
    if getattr(frame.columns, "nlevels", 1) > 1:
        frame.columns = frame.columns.get_level_values(0)
    rows = []
    for index, row in frame.iterrows():
        opened, closed = row.get("Open"), row.get("Close")
        if opened is None or closed is None:
            continue
        try:
            opened, closed = float(opened), float(closed)
        except (TypeError, ValueError):
            continue
        if opened != opened or closed != closed:
            continue
        rows.append({
            "session": index.date().isoformat(),
            "open": opened,
            "close": closed,
        })
    return rows


def cache_rows(conn, ticker, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO prices(ticker,session,open,close) VALUES(?,?,?,?)",
        ((ticker, row["session"], row["open"], row["close"]) for row in rows),
    )


def ensure_window(conn, ticker, start, end, provider=fetch_yfinance):
    """Ensure a ticker window exists. Returns ok, empty, or error."""
    cached = conn.execute(
        "SELECT COUNT(*) AS n FROM prices WHERE ticker=? AND session BETWEEN ? AND ?",
        (ticker, start.isoformat(), end.isoformat()),
    ).fetchone()["n"]
    if cached:
        return "ok"
    try:
        rows = provider(ticker, start, end)
    except Exception as exc:
        logger.warning("price fetch failed for %s: %s", ticker, exc)
        return "error"
    if not rows:
        return "empty"
    cache_rows(conn, ticker, rows)
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
    if not entry or not exit_row or entry["open"] is None or exit_row["close"] is None:
        return None
    return float(entry["open"]), float(exit_row["close"])
