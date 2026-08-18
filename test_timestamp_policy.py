"""Offline regression: as_of = source time, UTC Z, null when unknown.

History (July 16): every timestamp in the brief was naive local time
(IST pipeline, NY exchange, epoch sources — three timezones in one field),
and add_record_metadata silently substituted the run timestamp when a source
had no event time, so a pre-open brief stamped yesterday's close as "true
now". Policy locked here: all as_of UTC with explicit Z; naive source times
truncate to date precision; no source time -> as_of null + a
data_quality.as_of_nulled entry (never generated_at); derived records
inherit from inputs and are never newer than any of them.
"""
import sys

sys.path.insert(0, ".")

from datetime import datetime, timezone, timedelta
import pandas as pd

from timeutil import to_utc_z, oldest, newest

# --- tz-aware -> exact UTC
est = timezone(timedelta(hours=-4))
assert to_utc_z(datetime(2026, 7, 15, 16, 0, tzinfo=est)) == "2026-07-15T20:00:00Z"
assert to_utc_z(datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)) == "2026-07-15T20:00:00Z"
# yfinance daily bar: tz-aware pandas Timestamp (exchange tz)
assert to_utc_z(pd.Timestamp("2026-07-15 00:00:00-04:00")) == "2026-07-15T04:00:00Z"

# --- naive -> date precision, never a guessed offset
assert to_utc_z(datetime(2026, 7, 9, 19, 0, 41)) == "2026-07-09T00:00:00Z"
assert to_utc_z("2026-07-09 19:00:41") == "2026-07-09T00:00:00Z"  # OpenInsider
assert to_utc_z("2026-07-15") == "2026-07-15T00:00:00Z"           # date-only
assert to_utc_z("Wed, 16 Jul 2026 09:15:00") == "2026-07-16T00:00:00Z"  # RSS pubDate, tz truncated

# --- RFC 2822 with zone (RSS pubDate as actually served) -> exact UTC
assert to_utc_z("Wed, 16 Jul 2026 09:15:00 GMT") == "2026-07-16T09:15:00Z"
assert to_utc_z("Wed, 16 Jul 2026 09:15:00 -0400") == "2026-07-16T13:15:00Z"

# --- ISO strings with explicit zone -> exact UTC
assert to_utc_z("2026-07-14T18:31:02.000-04:00") == "2026-07-14T22:31:02Z"  # EDGAR acceptance
assert to_utc_z("2026-07-15T00:00:00Z") == "2026-07-15T00:00:00Z"           # round-trip stable

# --- epoch seconds -> UTC
assert to_utc_z(1784152800) == datetime.fromtimestamp(1784152800, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# --- no usable time -> None, never a substitute
assert to_utc_z(None) is None
assert to_utc_z("") is None
assert to_utc_z("not a time") is None
assert to_utc_z(float("nan")) is None
assert to_utc_z(pd.NaT) is None
assert to_utc_z(42) is None  # not a plausible epoch

# --- derived rules
# multi-series aggregate: oldest of the latest bars; unknown input poisons it
assert oldest("2026-07-15T20:00:00Z", "2026-07-14T20:00:00Z") == "2026-07-14T20:00:00Z"
assert oldest("2026-07-15T20:00:00Z", None) is None
assert oldest() is None
# event set: newest constituent; unknowns ignored, all-unknown -> None
assert newest(["2026-07-09T00:00:00Z", "2026-07-12T00:00:00Z", None]) == "2026-07-12T00:00:00Z"
assert newest([None, None]) is None
assert newest([]) is None

# --- add_record_metadata: normalization + null policy, no run-time fallback
from export_for_gemini import add_record_metadata

sections = {
    "prices": [
        {"ticker": "COIN", "price": 167.21, "as_of": "2026-07-15T04:00:00Z"},
        {"ticker": "GLD", "price": None, "as_of": None},  # degraded fetch
    ],
    "sec_filings": [
        {"accession_number": "0001-24-000042", "filed_at": "2026-07-14T18:31:02.000-04:00"},
    ],
    "twitter_signals": [
        {"link": "http://x/1", "as_of": "Wed, 16 Jul 2026 09:15:00"},
    ],
    "earnings_calendar": [
        {"ticker": "TSLA", "earnings_date": "2026-07-22"},  # schedule, not source time
    ],
    "options_flow": {
        "COIN": {"option_contract_volume_oi_anomaly": [
            {"ticker": "COIN", "strike": 300, "type": "CALL",
             "expiration": "2026-07-18", "as_of": "2026-07-16T13:00:00Z"}
        ]},
    },
}
nulled = add_record_metadata(sections)

assert sections["prices"][0]["as_of"] == "2026-07-15T04:00:00Z"
assert sections["prices"][1]["as_of"] is None
assert sections["sec_filings"][0]["as_of"] == "2026-07-14T22:31:02Z"
assert sections["twitter_signals"][0]["as_of"] == "2026-07-16T00:00:00Z"
# earnings_date is an event schedule, not an observation time -> null
assert sections["earnings_calendar"][0]["as_of"] is None
assert sections["options_flow"]["COIN"]["option_contract_volume_oi_anomaly"][0]["as_of"] == "2026-07-16T13:00:00Z"
assert set(nulled) == {
    "$.sections.prices[1]",
    "$.sections.earnings_calendar[0]",
}, nulled
# every record got SOME as_of key (null included) and a record_id
for sec in ("prices", "sec_filings", "twitter_signals", "earnings_calendar"):
    for rec in sections[sec]:
        assert "as_of" in rec and rec["record_id"]
# nothing was stamped with pipeline time
now_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
for sec in sections.values():
    recs = sec if isinstance(sec, list) else []
    for rec in recs:
        assert not (rec.get("as_of") or "").startswith(now_prefix)

# --- CT.gov registry records carry lastUpdatePostDate, not the trial schedule.
# July 20: clinical_catalysts exported 21/21 as_of null while the API was
# handing us the update date on every study.
from export_for_gemini import build_clinical_catalysts

catalysts, biotech_news, registry_view = build_clinical_catalysts(
    [
        {"nct_id": "NCT05329649", "title": "CTX001 in pediatric SCD",
         "sponsor": "Vertex Pharmaceuticals Incorporated", "status": "ACTIVE_NOT_RECRUITING",
         "phase": "PHASE3", "target_date": "2027-06-06", "days_until": 320,
         "primary_completion_date": "2027-06-06", "enrollment": 13,
         "last_update_posted": "2026-07-13", "link": "http://ct/1"},
        {"nct_id": "NCT09999999", "title": "Undated registry record",
         "sponsor": "Unknown", "status": "RECRUITING", "phase": "PHASE3",
         "target_date": "2028-01-01", "days_until": 500,
         "last_update_posted": None, "link": "http://ct/2"},
    ],
    [],
)
by_nct = {c["nct_id"]: c for c in catalysts}
# as_of is when the registry entry changed...
assert by_nct["NCT05329649"]["as_of"] == "2026-07-13T00:00:00Z"
# ...not the completion date, which is a future schedule
assert by_nct["NCT05329649"]["target_date"] == "2027-06-06"
# no update date -> null, never the run time or the trial's own dates
assert by_nct["NCT09999999"]["as_of"] is None

print("Timestamp policy (UTC Z, source-time, null-not-run-time) checks passed")
