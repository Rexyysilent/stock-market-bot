"""M01-M03 temporal eligibility: strictly-earlier history, knowledge cutoff,
and market-cap cache vintage."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest

import export_for_gemini as exporter
import signals
from config import PIPELINE_VERSION


def _row(session, value, observed_at="auto"):
    if observed_at == "auto":
        observed_at = f"{session}T21:00:00Z"
    return {"session": session, "as_of": f"{session}T20:00:00Z",
            "observed_at": observed_at, "value": value}


PAST = [_row(f"2026-08-{day:02d}", 1.0) for day in range(1, 16)]
FUTURE = [_row(f"2026-09-{day:02d}", 100.0) for day in range(22, 27)]


class BaselineTemporalTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.original_state = exporter.BASELINE_STATE_FILE
        exporter.BASELINE_STATE_FILE = str(Path(self.tempdir.name) / "baselines.json")

    def tearDown(self):
        exporter.BASELINE_STATE_FILE = self.original_state
        self.tempdir.cleanup()

    def seed(self, rows):
        state = {"schema_version": 2, "versions": {
            PIPELINE_VERSION: {"TSLA": {"volume_ratio": rows}},
        }}
        Path(exporter.BASELINE_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

    def history(self):
        state = json.loads(Path(exporter.BASELINE_STATE_FILE).read_text(encoding="utf-8"))
        return state["versions"][PIPELINE_VERSION]["TSLA"]["volume_ratio"]

    def test_m01_future_rows_never_enter_live_score_and_are_retained(self):
        self.seed(FUTURE + PAST)  # deliberately out of chronological order
        z_scores, alerts = exporter.update_baselines_and_score({}, {
            "TSLA": {"volume_ratio": 1.0, "source_session": "2026-09-21",
                     "observed_at": "2026-09-21T21:00:00Z"},
        }, "2026-09-21")
        self.assertEqual(z_scores["TSLA"]["volume_ratio_baseline_n"], 15)
        self.assertEqual(z_scores["TSLA"]["volume_ratio_z"], 0.0)
        self.assertEqual(alerts, [])
        sessions = [row["session"] for row in self.history()]
        self.assertEqual(sessions, sorted(sessions))
        self.assertIn("2026-09-21", sessions)
        self.assertTrue(set(r["session"] for r in FUTURE) <= set(sessions))

    def test_m01_pure_score_is_unchanged_by_appended_future_rows(self):
        prior, reason = exporter.baseline_prior(PAST, "2026-09-21")
        before = exporter.score_against_baseline(1.0, prior)
        prior_after, _ = exporter.baseline_prior(PAST + FUTURE, "2026-09-21")
        after = exporter.score_against_baseline(1.0, prior_after)
        self.assertIsNone(reason)
        self.assertEqual(before, after)
        self.assertEqual(before["sample_size"], 15)
        self.assertEqual(len(before["input_sha256"]), 64)

    def test_m02_late_observed_old_session_is_excluded_before_observation(self):
        late = _row("2026-09-18", 50.0, observed_at="2026-09-23T12:00:00Z")
        history = PAST + [late]
        known, _ = exporter.baseline_prior(
            history, "2026-09-21", known_by="2026-09-21T12:00:00Z")
        self.assertNotIn(late, known)
        later, _ = exporter.baseline_prior(
            history, "2026-09-21", known_by="2026-09-24T00:00:00Z")
        self.assertIn(late, later)

    def test_strict_view_refuses_rows_without_knowledge_time(self):
        legacy = _row("2026-08-20", 1.0, observed_at=None)
        rows, reason = exporter.baseline_prior(
            PAST + [legacy], "2026-09-21",
            known_by="2026-09-21T12:00:00Z", strict=True)
        self.assertEqual((rows, reason), ([], "missing_knowledge_time"))
        with self.assertRaises(ValueError):
            exporter.baseline_prior(PAST, "2026-09-21", strict=True)
        # A later legacy row cannot block an earlier strict view.
        rows, reason = exporter.baseline_prior(
            PAST + [_row("2026-09-25", 1.0, observed_at=None)], "2026-09-21",
            known_by="2026-09-21T12:00:00Z", strict=True)
        self.assertIsNone(reason)
        self.assertEqual(len(rows), 15)


class SocialTemporalTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.original = signals.SOCIAL_HISTORY_FILE
        signals.SOCIAL_HISTORY_FILE = str(Path(self.tempdir.name) / "social.json")

    def tearDown(self):
        signals.SOCIAL_HISTORY_FILE = self.original
        self.tempdir.cleanup()

    def test_burst_uses_strictly_earlier_dates_and_keeps_future_rows(self):
        def entry(day, mentions):
            return {"date": day, "mentions": mentions, "upvotes": 1,
                    "observation_status": "observed"}
        past = [entry(f"2026-09-{d:02d}", 10) for d in range(10, 15)]
        future = [entry(f"2026-09-{d:02d}", 1000) for d in range(22, 28)]
        Path(signals.SOCIAL_HISTORY_FILE).write_text(json.dumps({
            "contract_version": signals.SOCIAL_STATE_CONTRACT_VERSION,
            "tickers": {"TSLA": future + past},
        }), encoding="utf-8")
        attention = [{
            "ticker": "TSLA", "filter": "all-stocks", "universe_member": True,
            "mentions": 30, "upvotes": 1, "observation_status": "observed",
            "collection_status": "observed",
        }]
        alerts = signals.update_social_signals(
            attention, {}, "2026-09-21", mcap_lookup=lambda *_: None,
            observed_at="2026-09-21T21:00:00Z")
        self.assertEqual(attention[0]["burst_ratio"], 3.0)
        self.assertEqual([a["tag"] for a in alerts], ["SOCIAL_BURST"])
        state = json.loads(Path(signals.SOCIAL_HISTORY_FILE).read_text(encoding="utf-8"))
        dates = [h["date"] for h in state["tickers"]["TSLA"]]
        self.assertEqual(dates, sorted(dates))
        self.assertIn("2026-09-21", dates)
        self.assertIn("2026-09-27", dates)


class MarketCapVintageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.original = signals.MCAP_CACHE_FILE
        signals.MCAP_CACHE_FILE = str(Path(self.tempdir.name) / "mcap.json")
        self.calls = []
        calls = self.calls

        class Ticker:
            def __init__(self, symbol):
                calls.append(symbol)
                self.info = {"marketCap": 9e9}

        self.original_yf = sys.modules.get("yfinance")
        sys.modules["yfinance"] = types.SimpleNamespace(Ticker=Ticker)

    def tearDown(self):
        signals.MCAP_CACHE_FILE = self.original
        if self.original_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = self.original_yf
        self.tempdir.cleanup()

    def seed(self, fetched_at):
        Path(signals.MCAP_CACHE_FILE).write_text(json.dumps({
            "TSLA": {"market_cap_musd": 500.0, "fetched_at": fetched_at},
        }), encoding="utf-8")

    def test_m03_future_cache_rejected_without_live_substitute(self):
        self.seed("2026-09-23T12:00:00Z")
        replay_at = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.assertIsNone(
            signals.get_mcap_musd("TSLA", now=replay_at, allow_fetch=False))
        self.assertEqual(self.calls, [])

    def test_future_cache_is_not_fresh_for_live_lookup(self):
        self.seed("2026-09-23T12:00:00Z")
        now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.assertEqual(signals.get_mcap_musd("TSLA", now=now), 9000.0)
        self.assertEqual(self.calls, ["TSLA"])

    def test_valid_past_cache_served_without_fetch(self):
        self.seed("2026-09-20T12:00:00Z")
        now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.assertEqual(
            signals.get_mcap_musd("TSLA", now=now, allow_fetch=False), 500.0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
