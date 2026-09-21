"""Coherent OHLC acquisition cache backed by yfinance."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger("SignalLedger.Prices")

DEFAULT_INTERVAL = "1d"
DEFAULT_ADJUSTMENT_BASIS = "provider_adjusted"

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_acquisitions (
  acquisition_id TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  provider TEXT NOT NULL,
  interval TEXT NOT NULL,
  requested_start TEXT NOT NULL,
  requested_end TEXT NOT NULL,
  adjustment_basis TEXT NOT NULL,
  currency TEXT,
  acquired_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_points (
  acquisition_id TEXT NOT NULL REFERENCES price_acquisitions(acquisition_id),
  session TEXT NOT NULL,
  open REAL,
  close REAL,
  PRIMARY KEY (acquisition_id, session)
);

CREATE TABLE IF NOT EXISTS price_windows (
  ticker TEXT NOT NULL,
  entry_session TEXT NOT NULL,
  exit_session TEXT NOT NULL,
  acquisition_id TEXT NOT NULL REFERENCES price_acquisitions(acquisition_id),
  PRIMARY KEY (ticker, entry_session, exit_session)
);

CREATE INDEX IF NOT EXISTS idx_price_acquisitions_ticker_range
  ON price_acquisitions(ticker, requested_start, requested_end);
"""


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
    return [cleaned[session] for session in sorted(cleaned)]


def _ensure_cache_schema(conn):
    """Add coherent cache tables without rewriting the legacy price table."""
    present = conn.execute(
        """SELECT COUNT(*) FROM sqlite_master
           WHERE type='table'
             AND name IN ('price_acquisitions','price_points','price_windows')"""
    ).fetchone()[0]
    if present == 3:
        return
    conn.executescript(_CACHE_SCHEMA)


def _utc_timestamp(value=None):
    if value is None:
        stamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("price acquisition time must include a timezone")
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_identity(provider):
    if provider is fetch_yfinance:
        return "yfinance"
    module = getattr(provider, "__module__", None)
    name = (getattr(provider, "__qualname__", None)
            or getattr(provider, "__name__", None))
    return ".".join(part for part in (module, name) if part) or type(provider).__name__


def _acquisition_identity(metadata, rows):
    content_bytes = json.dumps(
        rows, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()
    identity_bytes = json.dumps(
        {**metadata, "content_sha256": content_sha256},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(identity_bytes).hexdigest(), content_sha256


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


def cache_rows(
    conn,
    ticker,
    rows,
    *,
    provider="unspecified",
    interval=DEFAULT_INTERVAL,
    requested_start=None,
    requested_end=None,
    adjustment_basis=DEFAULT_ADJUSTMENT_BASIS,
    currency=None,
    acquired_at=None,
):
    """Store one immutable acquisition and bind its exact endpoint window.

    The legacy ``prices`` table is deliberately not updated: its
    ``(ticker, session)`` key has no acquisition or adjustment identity and
    therefore cannot prove that two endpoints share a basis.
    """
    _ensure_cache_schema(conn)
    cleaned = _clean_rows(rows)
    if not cleaned:
        return None
    first = (requested_start.isoformat() if isinstance(requested_start, date)
             else str(requested_start or cleaned[0]["session"]))
    last = (requested_end.isoformat() if isinstance(requested_end, date)
            else str(requested_end or cleaned[-1]["session"]))
    if date.fromisoformat(last) < date.fromisoformat(first):
        raise ValueError("price acquisition end must not precede start")
    provider = str(provider).strip()
    interval = str(interval).strip()
    adjustment_basis = str(adjustment_basis).strip()
    if not provider or not interval or not adjustment_basis:
        raise ValueError("price provider, interval, and adjustment basis are required")
    acquired_at = _utc_timestamp(acquired_at)
    metadata = {
        "ticker": ticker,
        "provider": provider,
        "interval": interval,
        "requested_start": first,
        "requested_end": last,
        "adjustment_basis": adjustment_basis,
        "currency": currency,
        "acquired_at": acquired_at,
    }
    acquisition_id, content_sha256 = _acquisition_identity(metadata, cleaned)
    conn.execute(
        """INSERT OR IGNORE INTO price_acquisitions(
             acquisition_id,ticker,provider,interval,requested_start,requested_end,
             adjustment_basis,currency,acquired_at,content_sha256
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (acquisition_id, ticker, provider, interval, first, last,
         adjustment_basis, currency, acquired_at, content_sha256),
    )
    conn.executemany(
        """INSERT OR IGNORE INTO price_points(acquisition_id,session,open,close)
           VALUES(?,?,?,?)""",
        ((acquisition_id, row["session"], row["open"], row["close"])
         for row in cleaned),
    )
    endpoints = {row["session"]: row for row in cleaned}
    if (_price(endpoints.get(first, {}).get("open")) is not None
            and _price(endpoints.get(last, {}).get("close")) is not None):
        conn.execute(
            """INSERT OR IGNORE INTO price_windows(
                 ticker,entry_session,exit_session,acquisition_id
               ) VALUES(?,?,?,?)""",
            (ticker, first, last, acquisition_id),
        )
    return acquisition_id


def ensure_window(
    conn,
    ticker,
    start,
    end,
    provider=fetch_yfinance,
    *,
    provider_name=None,
    interval=DEFAULT_INTERVAL,
    adjustment_basis=DEFAULT_ADJUSTMENT_BASIS,
    currency=None,
    acquired_at=None,
):
    """Require the entry open AND exit close, not just one overlapping row.

    Returns ok, empty, incomplete, or error. An incomplete acquisition remains
    retryable. An incompatible cache identity returns error rather than serving
    another provider, interval, adjustment basis, or currency. A refill must
    supply both valid endpoints in the same response; do not splice a newly
    adjusted exit onto a cached pre-adjustment entry.
    The exact requested window is bound to one immutable acquisition. Legacy
    session-only rows have unknown basis and cannot satisfy this check; they
    are refetched together or remain unavailable. This contract does not
    assert interior-session completeness or provider-wide adjustment accuracy.
    """
    if end < start:
        raise ValueError("price window end must not precede start")
    first, last = start.isoformat(), end.isoformat()
    expected_provider = provider_name or _provider_identity(provider)
    metadata = get_window_metadata(conn, ticker, first, last)
    if metadata is not None:
        expected = (
            expected_provider,
            interval,
            adjustment_basis,
            currency,
        )
        stored = (
            metadata["provider"],
            metadata["interval"],
            metadata["adjustment_basis"],
            metadata["currency"],
        )
        if stored != expected:
            logger.warning(
                "price cache identity mismatch for %s %s..%s", ticker, first, last
            )
            return "error"
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
    cache_rows(
        conn,
        ticker,
        [row for row in rows if first <= row["session"] <= last],
        provider=expected_provider,
        interval=interval,
        requested_start=first,
        requested_end=last,
        adjustment_basis=adjustment_basis,
        currency=currency,
        acquired_at=acquired_at,
    )
    conn.commit()
    return "ok" if get_open_close(conn, ticker, first, last) is not None else "incomplete"


def get_open_close(conn, ticker, entry_session, exit_session):
    """Return endpoints only when one bound acquisition contains both."""
    _ensure_cache_schema(conn)
    row = conn.execute(
        """SELECT entry.open AS entry_open, exit.close AS exit_close
           FROM price_windows AS window
           JOIN price_points AS entry
             ON entry.acquisition_id=window.acquisition_id
            AND entry.session=window.entry_session
           JOIN price_points AS exit
             ON exit.acquisition_id=window.acquisition_id
            AND exit.session=window.exit_session
           WHERE window.ticker=?
             AND window.entry_session=?
             AND window.exit_session=?""",
        (ticker, entry_session, exit_session),
    ).fetchone()
    opened = _price(row["entry_open"]) if row else None
    closed = _price(row["exit_close"]) if row else None
    return (opened, closed) if opened is not None and closed is not None else None


def get_window_metadata(conn, ticker, entry_session, exit_session):
    """Return immutable acquisition metadata for a bound price window."""
    _ensure_cache_schema(conn)
    row = conn.execute(
        """SELECT acquisition.acquisition_id,acquisition.provider,
                  acquisition.interval,acquisition.requested_start,
                  acquisition.requested_end,acquisition.adjustment_basis,
                  acquisition.currency,acquisition.acquired_at,
                  acquisition.content_sha256
           FROM price_windows AS window
           JOIN price_acquisitions AS acquisition
             ON acquisition.acquisition_id=window.acquisition_id
           WHERE window.ticker=?
             AND window.entry_session=?
             AND window.exit_session=?""",
        (ticker, entry_session, exit_session),
    ).fetchone()
    return dict(row) if row else None
