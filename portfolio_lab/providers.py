"""Explicit, read-only acquisition of one coherent adjusted-price dataset.

Nothing is fetched on import. Optional locked ``yfinance`` and
``pandas_market_calendars`` dependencies are loaded only by ``fetch_yahoo``.
The public history contract is documented at:
https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html
https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_history_metadata.html
https://pandas-market-calendars.readthedocs.io/en/latest/usage.html

Yahoo/yfinance adjusted OHLC is an approximate split/dividend-adjusted economic
return proxy, not an executable share-price or cash-dividend ledger. A complete
calendar match does not establish provider correctness, licensing rights,
historical investability, or strategy performance. No missing data is filled,
no alternate provider is substituted and no partially acquired dataset returns.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import importlib
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .data import CALENDARS, MAX_PRICE_CELLS, MAX_SESSIONS, digest, session_date, validate_dataset, validate_study


_NATIVE_CALENDAR = {"XNYS": "NYSE", "XNSE": "NSE"}
_TIMEZONES = {
    "XNYS": frozenset({"America/New_York", "US/Eastern"}),
    "XNSE": frozenset({"Asia/Kolkata", "Asia/Calcutta"}),
}
_HISTORY_OPTIONS = {
    "interval": "1d",
    "actions": False,
    "auto_adjust": True,
    "back_adjust": False,
    "repair": False,
    "keepna": True,
    "timeout": 20,
    "raise_errors": True,
}


def _instant(value: Any, label: str) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    try:
        if value.utcoffset() is None:
            raise ValueError(f"{label} must have a timezone offset")
        return value
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a valid timezone-aware datetime") from exc


def _clock(now: datetime | None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else _instant(now, "now")
    return value.astimezone(timezone.utc)


def _zone_name(value: Any) -> str:
    return str(getattr(value, "key", None) or getattr(value, "zone", None) or value)


def _calendar_label(value: Any) -> str:
    # Schedule indexes are session labels, often naive midnight timestamps;
    # they are not UTC instants to be shifted into the exchange's timezone.
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if any((value.hour, value.minute, value.second, value.microsecond)):
            raise ValueError("Calendar index must contain midnight session labels")
        value = value.date()
    if not isinstance(value, date):
        raise ValueError("Calendar index must contain date session labels")
    return value.isoformat()


def _rows(frame: Any, required_columns: tuple[str, ...], label: str):
    try:
        columns = list(frame.columns)
        count = len(frame)
    except (AttributeError, TypeError) as exc:
        raise ValueError(f"{label} is not a tabular response") from exc
    if len(columns) != len(set(columns)) or not set(required_columns).issubset(columns):
        raise ValueError(f"{label} has missing or duplicate required columns")
    if not 1 <= count <= MAX_SESSIONS:
        raise ValueError(f"{label} must contain 1–{MAX_SESSIONS} rows")
    if not callable(getattr(frame, "iterrows", None)):
        raise ValueError(f"{label} cannot be iterated as date-indexed rows")
    return frame.iterrows()


def _price(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a positive finite price")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a positive finite price; gaps are not filled")
    return result


def _dependency(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ValueError(
            f"fetch-yahoo requires optional {name}; install the repository's locked dependencies"
        ) from exc


def fetch_yahoo(
    study: dict[str, Any],
    start: str,
    end: str,
    *,
    now: datetime | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
    calendar_factory: Callable[[str], Any] | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch all study assets over an INCLUSIVE complete-session date range.

    A new identity binds the entire returned path, calendar, acquisition time,
    adjustment declaration and metadata; no old cache of price rows is merged.
    ``now`` is an aware datetime for deterministic tests, otherwise the clock is
    read before requests and again AFTER acquisition. Future dates and sessions
    whose close has not passed are rejected before requesting price history.

    ``ticker_factory(symbol)`` and ``calendar_factory(native_name)`` are test
    seams. Any injected factory conservatively marks the output synthetic and
    records that status in provenance; mocks can never become a live dataset.
    Mocks need only implement the documented history/metadata/calendar methods
    and date-indexed table ``columns``, ``len`` and ``iterrows`` interfaces.

    The live yfinance cache is confined to ``cache_dir`` or the ignored local
    ``workspaces/portfolio-lab/yfinance-cache`` directory. yfinance's cache
    location is process-global; use this standalone acquisition path before
    other yfinance calls in the process and retain one cache location.
    """
    validate_study(study)
    first, last = session_date(start), session_date(end)
    if first > last:
        raise ValueError("start must not be after end")
    if (last - first).days > MAX_SESSIONS * 2:
        raise ValueError("Requested calendar span exceeds the bounded acquisition limit")
    started_at = _clock(now)
    calendar_id = study["calendar"]
    exchange_zone = ZoneInfo(CALENDARS[calendar_id])
    if last > started_at.astimezone(exchange_zone).date():
        raise ValueError("Future end dates are not accepted; request a completed date explicitly")
    injected = ticker_factory is not None or calendar_factory is not None
    calendar_version = "injected-test-calendar"
    if calendar_factory is None:
        mcal = _dependency("pandas_market_calendars")
        calendar_factory = mcal.get_calendar
        calendar_version = str(getattr(mcal, "__version__", "unreported"))
    if not callable(calendar_factory):
        raise ValueError("calendar_factory must be callable")
    native_calendar = _NATIVE_CALENDAR[calendar_id]
    try:
        calendar = calendar_factory(native_calendar)
        if _zone_name(calendar.tz) not in _TIMEZONES[calendar_id]:
            raise ValueError("Calendar timezone does not match the study exchange")
        schedule = calendar.schedule(start_date=start, end_date=end)
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Exchange calendar acquisition failed ({type(exc).__name__})") from exc

    sessions = []
    expected = []
    previous_close = None
    for label, row in _rows(schedule, ("market_open", "market_close"), "Calendar schedule"):
        session = _calendar_label(label)
        if not start <= session <= end:
            raise ValueError("Calendar returned a session outside the requested range")
        if expected and session <= expected[-1]:
            raise ValueError("Calendar returned duplicate or unordered sessions")
        opening = _instant(row["market_open"], "market_open").astimezone(timezone.utc)
        closing = _instant(row["market_close"], "market_close").astimezone(timezone.utc)
        if not opening < closing or (previous_close is not None and opening <= previous_close):
            raise ValueError("Calendar sessions overlap or have invalid open/close instants")
        if any(t.astimezone(exchange_zone).date().isoformat() != session for t in (opening, closing)):
            raise ValueError("Calendar instants disagree with the exchange-local session date")
        if closing >= started_at:
            raise ValueError(f"Session {session} is incomplete; request a completed end date explicitly")
        expected.append(session)
        sessions.append({
            "session": session,
            "open_at": opening.isoformat(),
            "close_at": closing.isoformat(),
            "prices": {},
        })
        previous_close = closing

    if not sessions:
        raise ValueError("The requested interval has no expected exchange sessions")
    if len(expected) * len(study["assets"]) > MAX_PRICE_CELLS:
        raise ValueError(
            f"Requested instrument-session cells exceed the {MAX_PRICE_CELLS} acquisition limit; "
            "reduce the date range or declared asset universe"
        )
    if set(flow["session"] for flow in study["contributions"]) - set(expected):
        raise ValueError("A study contribution falls outside the requested exchange sessions")

    provider_version = "injected-test-provider"
    if ticker_factory is None:
        yf = _dependency("yfinance")
        path = Path(cache_dir) if cache_dir is not None else Path("workspaces/portfolio-lab/yfinance-cache")
        path = path.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(path))
        ticker_factory = yf.Ticker
        provider_version = str(getattr(yf, "__version__", "unreported"))
    if not callable(ticker_factory):
        raise ValueError("ticker_factory must be callable")
    try:
        exclusive_end = (last + timedelta(days=1)).isoformat()
    except OverflowError as exc:
        raise ValueError("end cannot be represented as a provider-exclusive date") from exc
    options = {**_HISTORY_OPTIONS, "start": start, "end": exclusive_end}
    instrument_metadata = {}
    for symbol in study["assets"]:
        try:
            ticker = ticker_factory(symbol)
            history = ticker.history(**options)
            metadata = ticker.get_history_metadata()
        except Exception as exc:
            raise ValueError(f"Yahoo acquisition failed for {symbol} ({type(exc).__name__}); no dataset produced") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"Yahoo metadata is unavailable for {symbol}")
        if metadata.get("currency") != study["currency"]:
            raise ValueError(f"Yahoo currency missing or mismatched for {symbol}; implicit FX is forbidden")
        provider_zone = metadata.get("exchangeTimezoneName")
        if not isinstance(provider_zone, str) or provider_zone not in _TIMEZONES[calendar_id]:
            raise ValueError(f"Yahoo exchange timezone missing or mismatched for {symbol}")
        instrument_metadata[symbol] = {
            "currency": metadata["currency"],
            "exchangeTimezoneName": metadata["exchangeTimezoneName"],
        }
        seen = []
        for position, (timestamp, row) in enumerate(_rows(history, ("Open", "Close"), f"Yahoo history for {symbol}")):
            stamp = _instant(timestamp, f"{symbol} daily-bar index")
            if _zone_name(stamp.tzinfo) not in _TIMEZONES[calendar_id]:
                raise ValueError(f"Yahoo daily-bar timezone disagrees with the exchange for {symbol}")
            if any((stamp.hour, stamp.minute, stamp.second, stamp.microsecond)):
                raise ValueError(f"Yahoo daily bars must be labelled at local midnight for {symbol}")
            session = stamp.date().isoformat()
            if position >= len(expected) or session != expected[position]:
                raise ValueError(f"Yahoo has missing, duplicate, unexpected or unordered sessions for {symbol}")
            seen.append(session)
            sessions[position]["prices"][symbol] = {
                "open": _price(row["Open"], f"{symbol} {session} Open"),
                "close": _price(row["Close"], f"{symbol} {session} Close"),
            }
        if seen != expected:
            raise ValueError(f"Yahoo history is incomplete for {symbol}; no forward-fill or partial dataset")

    acquired_at = _clock(now)
    if acquired_at < started_at:
        raise ValueError("Acquisition clock moved backwards; no dataset produced")
    provenance = {
        "requested_start_inclusive": start,
        "requested_end_inclusive": end,
        "request_started_at": started_at.isoformat(),
        "history_options": options,
        "yfinance_version": provider_version,
        "pandas_market_calendars_version": calendar_version,
        "instrument_metadata": instrument_metadata,
        "injected_factory_diagnostic": injected,
    }
    dataset = {
        "schema_version": "portfolio-lab.dataset.v1",
        "dataset_id": "pending",
        "synthetic": injected,
        "currency": study["currency"],
        "calendar": calendar_id,
        "price_basis": "total_return_proxy",
        "adjustment_policy": (
            "Yahoo Finance via yfinance auto_adjust=True, back_adjust=False, repair=False: "
            "provider-adjusted OHLC approximates split/dividend-adjusted total returns. "
            "Dividends are reflected through the provider adjustment factor, not separately paid "
            "or added again. These are fractional economic units, not raw executable share prices. "
            "Adjustment completeness and historical provider revisions are not independently verified."
        ),
        "acquired_at": acquired_at.isoformat(),
        "source": {
            "name": "Yahoo Finance via yfinance" + (" (injected diagnostic; synthetic)" if injected else ""),
            "url": "https://finance.yahoo.com/",
            "rights_note": (
                "For local personal research subject to applicable provider terms. Availability is "
                "not a redistribution license or a guarantee of source-use rights; do not publish "
                "raw provider data. Injected test data, if marked synthetic, is not live evidence."
            ),
            "calendar_source": (
                f"pandas_market_calendars {calendar_version}, {native_calendar}; "
                "https://pandas-market-calendars.readthedocs.io/en/latest/usage.html; "
                "schedule is independent of the returned Yahoo rows; library accuracy is not guaranteed"
            ),
        },
        "expected_sessions": expected,
        "sessions": sessions,
        "notes": json.dumps(provenance, sort_keys=True, separators=(",", ":"), allow_nan=False),
    }
    dataset["dataset_id"] = "yahoo-" + digest({k: v for k, v in dataset.items() if k != "dataset_id"})[:32]
    return validate_dataset(dataset, study)
