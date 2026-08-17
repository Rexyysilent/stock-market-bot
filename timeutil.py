"""Timestamp policy for the brief.

as_of = when the data was true at its source (exchange bar time for prices,
filing time for filings, publication time for news) — NEVER when the pipeline
observed it. Every timestamp is UTC with an explicit Z suffix. A source that
exposes no usable time gets as_of null (listed in data_quality.as_of_nulled);
run time is never substituted. Naive source times carry no offset we can
trust (yfinance, EDGAR, and this machine sit in three different timezones),
so they are truncated to date precision (T00:00:00Z) rather than guessed.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UTC_Z = "%Y-%m-%dT%H:%M:%SZ"

# ISO first (handles offsets, 'Z', space separator, fractional seconds),
# then the formats our sources actually emit (last one: RSS pubDate,
# RFC 2822 truncated to 25 chars by the twitter agent — tz lost, so naive)
_STR_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y", "%a, %d %b %Y %H:%M:%S")


def utc_now_z():
    """Pipeline observation time, UTC Z. For generated_at only — never as_of."""
    return datetime.now(timezone.utc).strftime(UTC_Z)


@dataclass(frozen=True)
class RunContext:
    """One immutable clock/calendar view shared by every pipeline component.

    ``latest_completed_session`` uses an explicit post-close settle delay.
    That makes manual pre-open/intraday exports safe: daily technical and
    options measurements are anchored to the same completed NYSE session
    instead of whichever local date the machine happens to use.
    """

    started_at: str
    utc_date: str
    calendar: str
    settle_delay_minutes: int
    market_state: str
    current_session: str | None
    current_session_open: str | None
    current_session_close: str | None
    latest_completed_session: str | None
    latest_completed_close: str | None

    def to_dict(self):
        return asdict(self)


def build_run_context(now=None, calendar="NYSE", settle_delay_minutes=60):
    """Build a UTC/NYSE run context without relying on the host timezone."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    import pandas_market_calendars as mcal

    schedule = mcal.get_calendar(calendar).schedule(
        start_date=(now.date() - timedelta(days=10)).isoformat(),
        end_date=(now.date() + timedelta(days=2)).isoformat(),
    )
    rows = []
    for session, row in schedule.iterrows():
        opened = row["market_open"].to_pydatetime().astimezone(timezone.utc)
        closed = row["market_close"].to_pydatetime().astimezone(timezone.utc)
        rows.append((session.date().isoformat(), opened, closed))

    today_row = next((row for row in rows if row[0] == now.date().isoformat()), None)
    if today_row is None:
        market_state = "closed"
        current_session = current_open = current_close = None
    else:
        current_session, opened, closed = today_row
        current_open = opened.strftime(UTC_Z)
        current_close = closed.strftime(UTC_Z)
        settled_at = closed + timedelta(minutes=settle_delay_minutes)
        if now < opened:
            market_state = "pre_open"
        elif now < closed:
            market_state = "open"
        elif now < settled_at:
            market_state = "settling"
        else:
            market_state = "closed"

    settle_delay = timedelta(minutes=settle_delay_minutes)
    completed = [row for row in rows if row[2] + settle_delay <= now]
    latest = completed[-1] if completed else None
    return RunContext(
        started_at=now.strftime(UTC_Z),
        utc_date=now.date().isoformat(),
        calendar=calendar,
        settle_delay_minutes=settle_delay_minutes,
        market_state=market_state,
        current_session=current_session,
        current_session_open=current_open,
        current_session_close=current_close,
        latest_completed_session=latest[0] if latest else None,
        latest_completed_close=latest[2].strftime(UTC_Z) if latest else None,
    )


def to_utc_z(value):
    """Normalize a datetime / pandas Timestamp / date / epoch / string to
    'YYYY-MM-DDTHH:MM:SSZ' (UTC). Returns None when no usable time exists:
    tz-aware -> exact UTC; naive -> date precision; unparseable -> None."""
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value != value:  # pandas NaT
                return None
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).strftime(UTC_Z)
            return value.strftime("%Y-%m-%dT00:00:00Z")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%dT00:00:00Z")
        if isinstance(value, (int, float)):
            if value != value or not (1e8 < value < 1e11):
                return None
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime(UTC_Z)

        text = str(value).strip()
        if not text:
            return None
        try:
            return to_utc_z(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            pass
        try:
            # RFC 2822 (RSS pubDate): tz-aware when GMT/offset present
            return to_utc_z(parsedate_to_datetime(text))
        except (ValueError, TypeError):
            pass
        for fmt in _STR_FORMATS:
            for candidate in (text, text[:19]):
                try:
                    return to_utc_z(datetime.strptime(candidate, fmt))
                except ValueError:
                    continue
        return None
    except Exception:
        return None


def oldest(*stamps):
    """Derived-record as_of for multi-series aggregates: the oldest of the
    constituent series' latest bars (a spread is only as fresh as its stalest
    input). None when any input actually used lacks a usable time."""
    stamps = list(stamps)
    if not stamps or any(s is None for s in stamps):
        return None
    return min(stamps)


def newest(stamps):
    """Derived-record as_of for event sets (clusters, confluence): the record
    became true when its newest constituent landed. Constituents without a
    usable time are ignored; None when none carry one."""
    known = [s for s in stamps if s is not None]
    return max(known) if known else None


def split_fresh_records(records, time_field, max_age_days, now=None):
    """Return (fresh, dropped) using the brief's source-time policy.

    A missing or unparseable timestamp fails a freshness gate. Dropped rows are
    copied and annotated for data_quality; source records are never mutated.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = to_utc_z(now - timedelta(days=max_age_days))
    fresh, dropped = [], []
    for record in records or []:
        stamp = to_utc_z(record.get(time_field))
        if stamp is None:
            dropped.append({**record, "as_of": None, "drop_reason": "undated"})
        elif stamp < cutoff:
            dropped.append({**record, "as_of": stamp, "drop_reason": "stale"})
        else:
            fresh.append(record)
    return fresh, dropped
