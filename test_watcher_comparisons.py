"""Watcher's real orchestration, with deterministic history/calendar providers."""
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
import pandas as pd

if importlib.util.find_spec("yfinance") is None:
    sys.modules["yfinance"] = types.ModuleType("yfinance")
from agents import watcher_agent as module


class WatcherComparisonTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2026-08-03", periods=21)
        self.history = pd.DataFrame({"Close": list(range(100, 121))}, index=self.dates)
        self.agent = module.WatcherAgent(types.SimpleNamespace(utc_date="2026-08-31", latest_completed_session="2026-08-31"))
        self.calls = []

    def provider(self, symbol):
        self.calls.append(symbol)
        return types.SimpleNamespace(history=lambda **kwargs: self.history.copy())

    def sessions(self, latest, horizon):
        return [d.date().isoformat() for d in self.dates[-(horizon + 1):]]

    def test_watchlist_deduplicated(self):
        self.assertEqual(len(self.agent.watchlist), len(set(self.agent.watchlist)))

    def test_one_history_per_member_for_both_horizons(self):
        with patch.object(module.yf, "Ticker", self.provider, create=True), \
             patch.object(module, "comparison_sessions", self.sessions), \
             patch.object(module, "GROWTH_BASKET", ["AAA"]), \
             patch.object(module, "DEFENSIVE_BASKET", ["BBB"]):
            result = self.agent.get_sector_rotation()
        self.assertEqual(self.calls, ["AAA", "BBB"])
        self.assertEqual(result["growth_20d"], 20)
        self.assertAlmostEqual(result["growth_5d"], 4.35)
        self.assertEqual(result["spread_5d"], 0)
        self.assertEqual(result["signal"], "NEUTRAL")
        self.assertEqual(result["coverage"]["5"]["growth"]["available"], 1)

    def test_threshold_notes_preserved_with_correct_units(self):
        flat = self.history.copy()
        flat["Close"] = 100.0
        with patch.object(self.agent, "_comparison_histories", return_value={"AAA": flat, "BBB": self.history}), \
             patch.object(module, "comparison_sessions", self.sessions), \
             patch.object(module, "GROWTH_BASKET", ["AAA"]), \
             patch.object(module, "DEFENSIVE_BASKET", ["BBB"]):
            result = self.agent.get_sector_rotation()
        self.assertEqual(result["signal"], "RISK_OFF")
        self.assertEqual(len(result["alerts"]), 2)
        self.assertIn("percentage points", result["alerts"][0])
        self.assertIn("not independent confirmation", result["alerts"][1])

    def test_unavailable_not_neutral(self):
        with patch.object(self.agent, "_comparison_histories", return_value={}), \
             patch.object(module, "comparison_sessions", self.sessions), \
             patch.object(module, "GROWTH_BASKET", ["AAA"]), \
             patch.object(module, "DEFENSIVE_BASKET", ["BBB"]):
            result = self.agent.get_sector_rotation()
        self.assertEqual(result["signal"], "UNAVAILABLE")
        self.assertIsNone(result["spread_5d"])

    def test_pairs_use_same_dates_and_explain_missing_legs(self):
        pairs = [{"physical": "AAA", "paper": "BBB", "commodity": "synthetic"}]
        with patch.object(module, "INSTRUMENT_RELATIVE_RETURN_PAIRS", pairs), \
             patch.object(module, "comparison_sessions", self.sessions), \
             patch.object(self.agent, "_comparison_histories", return_value={"AAA": self.history, "BBB": self.history}):
            result = self.agent.check_instrument_relative_return_spread()
        self.assertEqual(result["pairs"][0]["spread"], 0)
        self.assertEqual(result["pairs"][0]["horizon_sessions"], 5)
        with patch.object(module, "INSTRUMENT_RELATIVE_RETURN_PAIRS", pairs), \
             patch.object(module, "comparison_sessions", self.sessions), \
             patch.object(self.agent, "_comparison_histories", return_value={"AAA": self.history, "BBB": self.history.iloc[:-1]}):
            result = self.agent.check_instrument_relative_return_spread()
        self.assertEqual(result["pairs"], [])
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["excluded_pairs"][0]["paper_reason"], "missing_sessions")


if __name__ == "__main__": unittest.main()
