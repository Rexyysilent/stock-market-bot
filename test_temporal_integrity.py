"""Offline regressions for v2.6 temporal and transactional integrity."""

import json
import os
import tempfile
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import agents.watcher_agent as watcher_module
import signals
from agents.research_agent import ResearchAgent
from agents.watcher_agent import WatcherAgent
from export_for_gemini import collect_future_result
from stateutil import (
    atomic_text_writer,
    atomic_write_bytes,
    exclusive_file_lock,
    load_json_state,
    state_transaction,
)
from timeutil import build_run_context


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# One immutable NYSE clock drives every session-derived measurement.
pre_open = build_run_context(utc("2026-07-27T12:00:00Z"))
assert pre_open.market_state == "pre_open"
assert pre_open.current_session == "2026-07-27"
assert pre_open.latest_completed_session == "2026-07-24"

open_market = build_run_context(utc("2026-07-27T15:00:00Z"))
assert open_market.market_state == "open"
assert open_market.latest_completed_session == "2026-07-24"

settling = build_run_context(utc("2026-07-27T20:30:00Z"))
assert settling.market_state == "settling"
assert settling.latest_completed_session == "2026-07-24"

settled = build_run_context(utc("2026-07-27T21:01:00Z"))
assert settled.market_state == "closed"
assert settled.latest_completed_session == "2026-07-27"

weekend = build_run_context(utc("2026-07-26T16:00:00Z"))
assert weekend.market_state == "closed"
assert weekend.current_session is None
assert weekend.latest_completed_session == "2026-07-24"

# The settle delay follows the exchange's early close, not a hard-coded 20:00Z.
early_close_settling = build_run_context(utc("2026-11-27T18:30:00Z"))
assert early_close_settling.current_session_close == "2026-11-27T18:00:00Z"
assert early_close_settling.market_state == "settling"
assert early_close_settling.latest_completed_session == "2026-11-25"
early_close_settled = build_run_context(utc("2026-11-27T19:01:00Z"))
assert early_close_settled.latest_completed_session == "2026-11-27"

watcher = WatcherAgent(run_context=pre_open)
history = pd.DataFrame(
    {"Close": [100.0, 101.0]},
    index=pd.to_datetime(["2026-07-24", "2026-07-27"]),
)
trimmed = watcher._completed_daily_history(history)
assert [stamp.date().isoformat() for stamp in trimmed.index] == ["2026-07-24"]


# Option volume must belong to the latest completed session. A fetch-time
# snapshot cannot make stale volume current, and CALL/PUT is not direction.
current_call = {
    "strike": 300.0,
    "volume": 1000,
    "openInterest": 100,
    "lastPrice": 10.0,
    "lastTradeDate": pd.Timestamp("2026-07-24T19:45:00Z"),
}
stale_call = {
    "strike": 310.0,
    "volume": 2000,
    "openInterest": 100,
    "lastPrice": 20.0,
    "lastTradeDate": pd.Timestamp("2026-07-23T19:45:00Z"),
}
current_put = {
    "strike": 250.0,
    "volume": 500,
    "openInterest": 100,
    "lastPrice": 1.0,
    "lastTradeDate": pd.Timestamp("2026-07-24T19:55:00Z"),
}


class FakeTicker:
    options = ["2026-07-29"]

    def option_chain(self, expiration):
        assert expiration == "2026-07-29"
        return SimpleNamespace(
            calls=pd.DataFrame([current_call, stale_call]),
            puts=pd.DataFrame([current_put]),
        )


original_ticker = watcher_module.yf.Ticker
try:
    watcher_module.yf.Ticker = lambda ticker: FakeTicker()
    flow = watcher.get_options_flow("TSLA")
finally:
    watcher_module.yf.Ticker = original_ticker

assert flow["source_session"] == "2026-07-24"
assert flow["eligible_volume_contracts"] == 2
assert flow["excluded_stale_volume_contracts"] == 1
assert flow["signal_eligible"] is True
assert flow["put_call_vol_ratio"] == 0.5
assert flow["total_call_volume"] == 1000
assert flow["total_put_volume"] == 500
assert flow["as_of"] == "2026-07-24T19:55:00Z"
assert len(flow["option_contract_volume_oi_anomaly"]) == 1
anomaly = flow["option_contract_volume_oi_anomaly"][0]
assert anomaly["strike"] == 300.0
assert anomaly["notional_estimate"] == 1_000_000
assert anomaly["notional_estimate_method"] == (
    "last_price_x_completed_session_volume_x100"
)
assert anomaly["last_trade_at"] == "2026-07-24T19:45:00Z"
assert all("PUT/CALL" not in alert.upper() for alert in flow["alerts"])


# Timestamp-less records fail closed at the confluence boundary.
assert signals.collect_alert_events(
    [{"ticker": "TSLA", "tag": "SOCIAL_BURST"}],
    [], {}, [], {}, [], [], [],
) == []

# One failed top-level source is isolated and described for export health.
failed_future = Future()
failed_future.set_exception(ValueError("malformed upstream payload"))
failures = []
fallback = []
assert collect_future_result(
    "fixture_source", failed_future, fallback, failures
) is fallback
assert failures == [{
    "stage": "fixture_source",
    "error": "ValueError: malformed upstream payload",
}]


# FDA's server-rendered landing page is the authoritative fallback when the
# client-rendered calendar table contains no rows.
adcom_html = """
<h2>Upcoming Advisory Committee Meetings</h2>
<a href="/advisory-committees/foo/cellular-july-29-2026-meeting-announcement">
  Cellular, Tissue, and Gene Therapies Advisory Committee July 29, 2026
  Meeting Announcement- UPDATED INFORMATION (as of 7/16/2026)
</a>
"""
research = ResearchAgent(now=utc("2026-07-27T12:00:00Z"))
meetings = research._parse_adcom_page(adcom_html)
assert meetings == [{
    "date": "2026-07-29",
    "title": (
        "Cellular, Tissue, and Gene Therapies Advisory Committee July 29, "
        "2026 Meeting Announcement- UPDATED INFORMATION (as of 7/16/2026)"
    ),
    "link": (
        "https://www.fda.gov/advisory-committees/foo/"
        "cellular-july-29-2026-meeting-announcement"
    ),
    "as_of": "2026-07-16T00:00:00Z",
    "observed_at": "2026-07-27T12:00:00Z",
    "source": "FDA Advisory Committees",
}]

adcom_detail_html = """
<main>
  <h2>Agenda</h2>
  <p>On July 29, 2026, the Committee will discuss Biologics License
  Application (BLA) 125842 from Capricor, Inc. for deramiocel
  (human allogeneic cells) for Duchenne muscular dystrophy.</p>
  <h2>Meeting Materials</h2>
</main>
"""
detail = research._parse_adcom_detail(adcom_detail_html)
assert detail["application"] == "BLA 125842"
assert detail["sponsor"] == "Capricor, Inc."
assert detail["product"] == "deramiocel"
assert detail["ticker"] == "CAPR"
assert "Duchenne muscular dystrophy" in detail["agenda"]


# Existing state is never interpreted as empty on corruption; transactions
# restore every signal history when the run fails.
with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    corrupt = root / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    try:
        load_json_state(corrupt, {})
        raise AssertionError("corrupt state must fail closed")
    except json.JSONDecodeError:
        pass

    existing = root / "existing.json"
    created = root / "created.json"
    existing.write_bytes(b'{"value":1}\n')
    try:
        with state_transaction([existing, created]):
            atomic_write_bytes(existing, b'{"value":2}\n')
            atomic_write_bytes(created, b'{"value":3}\n')
            raise RuntimeError("abort")
    except RuntimeError as exc:
        assert str(exc) == "abort"
    assert existing.read_bytes() == b'{"value":1}\n'
    assert not created.exists()

    text_path = root / "operator.txt"
    text_path.write_text("old\n", encoding="utf-8")
    try:
        with atomic_text_writer(text_path) as handle:
            handle.write("partial\n")
            raise RuntimeError("abort")
    except RuntimeError:
        pass
    assert text_path.read_text(encoding="utf-8") == "old\n"

    lock_path = root / "export.lock"
    with exclusive_file_lock(lock_path):
        try:
            with exclusive_file_lock(lock_path):
                raise AssertionError("duplicate run lock must not be acquired")
        except RuntimeError as exc:
            assert "already running" in str(exc)


# The task polls independently of the host timezone; the launcher decides
# whether 21:30 UTC has arrived and whether the UTC day already ran.
repo = Path(__file__).resolve().parent
installer = (repo / "install_scheduler.ps1").read_text(encoding="utf-8")
launcher = (repo / "run_scheduled.ps1").read_text(encoding="utf-8")
assert "$minutes += 30" in installer
assert "13:30" not in installer and "14:30" not in installer
assert "[DateTime]::UtcNow" in launcher
assert "[TimeSpan]::FromHours(21.5)" in launcher

print("v2.6 temporal and transactional integrity checks passed")
