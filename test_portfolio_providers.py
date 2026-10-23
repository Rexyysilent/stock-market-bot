"""Injected offline provider tests. These do not contact or verify Yahoo."""

import copy
from datetime import date, datetime, time, timedelta, timezone
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from portfolio_lab.data import validate_dataset
from portfolio_lab.providers import fetch_yahoo


NOW = datetime(2024, 1, 9, 23, tzinfo=timezone.utc)
DAYS = [date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]


def study(calendar="XNYS", currency="USD"):
    return {
        "schema_version": "portfolio-lab.study.v1", "study_id": "offline-provider-test",
        "strategy_sha256": "a" * 64, "currency": currency, "calendar": calendar,
        "assets": {"ALFA": {"kind": "equity"}, "BETA": {"kind": "equity_etf"}},
        "universe_basis": "fixed_operator_selection", "universe_declared_at": "2024-01-01T00:00:00Z",
        "initial_cash": 1000, "benchmark_weights": {"BETA": 1},
        "cost_model": {"kind": "generic", "buy_bps": 0, "sell_bps": 0, "fixed_per_order": 0},
        "slippage_bps": 0, "annual_fixed_cost": 0, "benchmark_annual_fixed_cost": 0,
        "execution": {"latency_seconds": 60, "extra_delay_sessions": 0, "max_decision_age_days": 35, "min_trade_value": 0},
        "limits": {"max_weight": 1, "max_gross": 1},
        "evaluation": {"holdout_start": "2024-01-08", "declared_at": "2024-01-01T00:00:00Z", "minimum_holdout_sessions": 1, "minimum_paper_decisions": 1, "objective": "Synthetic offline acquisition checks"},
        "contributions": [],
    }


class Table:
    def __init__(self, rows, columns):
        self.rows = rows
        self.columns = columns

    def __len__(self):
        return len(self.rows)

    def iterrows(self):
        return iter(self.rows)


class Calendar:
    def __init__(self, zone="America/New_York", days=None):
        self.tz = ZoneInfo(zone)
        dates = DAYS if days is None else days
        is_india = zone in ("Asia/Kolkata", "Asia/Calcutta")
        opening = time(9, 15) if is_india else time(9, 30)
        closing = time(15, 30) if is_india else time(16)
        self.table = Table([
            (day, {"market_open": datetime.combine(day, opening, self.tz).astimezone(timezone.utc),
                   "market_close": datetime.combine(day, closing, self.tz).astimezone(timezone.utc)})
            for day in dates
        ], ["market_open", "market_close"])
        self.requests = []

    def schedule(self, **kwargs):
        self.requests.append(kwargs)
        return self.table


class Ticker:
    def __init__(self, zone="America/New_York", currency="USD", days=None):
        self.metadata = {"currency": currency, "exchangeTimezoneName": zone}
        self.requests = []
        self.metadata_reads = 0
        self.table = Table([
            (datetime.combine(day, time(0), ZoneInfo(zone)), {"Open": 100.0 + index, "Close": 101.0 + index})
            for index, day in enumerate(DAYS if days is None else days)
        ], ["Open", "Close"])

    def history(self, **kwargs):
        self.requests.append(kwargs)
        return self.table

    def get_history_metadata(self):
        self.metadata_reads += 1
        return self.metadata


class PortfolioProviderTests(unittest.TestCase):
    def harness(self, calendar=None, tickers=None, specification=None, now=NOW, start="2024-01-05", end="2024-01-09"):
        calendar = Calendar() if calendar is None else calendar
        tickers = {"ALFA": Ticker(), "BETA": Ticker()} if tickers is None else tickers
        specification = study() if specification is None else specification
        return fetch_yahoo(specification, start, end, now=now,
                           ticker_factory=tickers.__getitem__, calendar_factory=lambda name: calendar)

    def test_complete_dataset_and_inclusive_end(self):
        cal = Calendar()
        tickers = {"ALFA": Ticker(), "BETA": Ticker()}
        specification = study()
        original = copy.deepcopy(specification)
        result = self.harness(cal, tickers, specification)
        self.assertIs(validate_dataset(result, specification), result)
        self.assertEqual(result["expected_sessions"], [day.isoformat() for day in DAYS])
        self.assertEqual(result["sessions"][1]["prices"]["ALFA"]["open"], 101)
        self.assertTrue(result["synthetic"])
        self.assertIn("synthetic", result["source"]["name"])
        self.assertIn("dividend", result["adjustment_policy"])
        self.assertEqual(specification, original)
        self.assertEqual(cal.requests, [{"start_date": "2024-01-05", "end_date": "2024-01-09"}])
        for ticker in tickers.values():
            self.assertEqual(ticker.requests, [{"interval": "1d", "actions": False, "auto_adjust": True,
                                               "back_adjust": False, "repair": False, "keepna": True,
                                               "timeout": 20, "raise_errors": True,
                                               "start": "2024-01-05", "end": "2024-01-10"}])
            self.assertEqual(ticker.metadata_reads, 1)
        json.dumps(result, allow_nan=False)

    def test_india_timezone_alias_and_currency(self):
        tickers = {symbol: Ticker("Asia/Calcutta", "INR") for symbol in ("ALFA", "BETA")}
        result = self.harness(Calendar("Asia/Kolkata"), tickers, study("XNSE", "INR"))
        self.assertEqual(result["sessions"][0]["open_at"], "2024-01-05T03:45:00+00:00")
        self.assertEqual(result["currency"], "INR")

    def test_missing_extra_duplicate_and_unordered_bars_rejected(self):
        variants = [DAYS[:2], DAYS + [date(2024, 1, 10)], [DAYS[0], DAYS[0], DAYS[2]],
                    [DAYS[1], DAYS[0], DAYS[2]], [DAYS[0], date(2024, 1, 6), DAYS[2]]]
        for days in variants:
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.harness(tickers={"ALFA": Ticker(days=days), "BETA": Ticker()})

    def test_missing_or_mismatched_metadata_is_not_inferred(self):
        variants = [None, {}, {"currency": "EUR", "exchangeTimezoneName": "America/New_York"},
                    {"currency": "USD", "exchangeTimezoneName": "Asia/Kolkata"},
                    {"currency": "USD"}, {"exchangeTimezoneName": "America/New_York"},
                    {"currency": "USD", "exchangeTimezoneName": []}]
        for metadata in variants:
            with self.subTest(metadata=metadata):
                ticker = Ticker()
                ticker.metadata = metadata
                with self.assertRaises(ValueError):
                    self.harness(tickers={"ALFA": ticker, "BETA": Ticker()})

    def test_bar_timezone_and_midnight_are_verified(self):
        for timestamp in [datetime(2024, 1, 5), datetime(2024, 1, 5, tzinfo=timezone.utc),
                          datetime(2024, 1, 5, 9, tzinfo=ZoneInfo("America/New_York"))]:
            ticker = Ticker()
            ticker.table.rows[0] = (timestamp, ticker.table.rows[0][1])
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.harness(tickers={"ALFA": ticker, "BETA": Ticker()})

    def test_nan_zero_negative_and_nonnumeric_bars_are_not_filled(self):
        for value in [float("nan"), float("inf"), 0, -1, True, "100", None]:
            ticker = Ticker()
            ticker.table.rows[1][1]["Close"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.harness(tickers={"ALFA": ticker, "BETA": Ticker()})

    def test_future_and_incomplete_requests_reject_before_history(self):
        for now, end in [(NOW, "2024-01-10"),
                         (datetime(2024, 1, 9, 20, tzinfo=timezone.utc), "2024-01-09"),
                         (datetime(2024, 1, 9, 21, tzinfo=timezone.utc), "2024-01-09")]:
            tickers = {"ALFA": Ticker(), "BETA": Ticker()}
            with self.subTest(now=now, end=end), self.assertRaises(ValueError):
                self.harness(tickers=tickers, now=now, end=end)
            self.assertFalse(any(t.requests for t in tickers.values()))

    def test_holidays_are_not_replaced_with_weekdays(self):
        # Explicit calendar excludes Monday; a matching provider response is
        # accepted, while a weekday-only imagined schedule would be different.
        days = [DAYS[0], DAYS[2]]
        tickers = {symbol: Ticker(days=days) for symbol in ("ALFA", "BETA")}
        result = self.harness(Calendar(days=days), tickers)
        self.assertEqual(result["expected_sessions"], ["2024-01-05", "2024-01-09"])

    def test_early_close_uses_calendar_time(self):
        cal = Calendar()
        cal.table.rows[-1][1]["market_close"] = datetime(2024, 1, 9, 18, tzinfo=timezone.utc)
        result = self.harness(cal, now=datetime(2024, 1, 9, 19, tzinfo=timezone.utc))
        self.assertEqual(result["sessions"][-1]["close_at"], "2024-01-09T18:00:00+00:00")

    def test_calendar_defects_fail_before_history(self):
        calendars = [Calendar("UTC"), Calendar(days=[]), Calendar(days=[DAYS[0], DAYS[0]]),
                     Calendar(days=list(reversed(DAYS)))]
        overlapping = Calendar()
        overlapping.table.rows[1][1]["market_open"] = datetime(2024, 1, 5, 20, tzinfo=timezone.utc)
        calendars.append(overlapping)
        for cal in calendars:
            tickers = {"ALFA": Ticker(), "BETA": Ticker()}
            with self.subTest(cal=cal), self.assertRaises(ValueError):
                self.harness(cal, tickers)
            self.assertFalse(any(t.requests for t in tickers.values()))

    def test_cell_budget_rejects_before_any_ticker_creation_or_request(self):
        created = []
        def ticker_factory(symbol):
            created.append(symbol)
            return Ticker()
        with patch("portfolio_lab.providers.MAX_PRICE_CELLS", 5):
            with self.assertRaisesRegex(ValueError, "instrument-session cells"):
                fetch_yahoo(study(), "2024-01-05", "2024-01-09", now=NOW,
                            ticker_factory=ticker_factory, calendar_factory=lambda name: Calendar())
        self.assertEqual(created, [])
        # The exact boundary remains usable: three sessions times two assets.
        with patch("portfolio_lab.providers.MAX_PRICE_CELLS", 6):
            result = fetch_yahoo(study(), "2024-01-05", "2024-01-09", now=NOW,
                                 ticker_factory=ticker_factory, calendar_factory=lambda name: Calendar())
        self.assertEqual(len(result["sessions"]), 3)
        self.assertEqual(created, ["ALFA", "BETA"])

    def test_duplicate_or_missing_columns_are_rejected(self):
        for columns in [["Open"], ["Open", "Close", "Close"]]:
            ticker = Ticker()
            ticker.table.columns = columns
            with self.subTest(columns=columns), self.assertRaises(ValueError):
                self.harness(tickers={"ALFA": ticker, "BETA": Ticker()})

    def test_request_failure_never_returns_partial_dataset(self):
        ticker = Ticker()
        ticker.history = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("test provider unavailable"))
        with self.assertRaisesRegex(ValueError, "no dataset produced"):
            self.harness(tickers={"ALFA": Ticker(), "BETA": ticker})

    def test_acquisition_time_is_sampled_after_fetch(self):
        calls = []
        ticker = Ticker()
        original = ticker.history
        ticker.history = lambda **kw: (calls.append("history") or original(**kw))
        def clock(now):
            calls.append("clock")
            return NOW if len(calls) == 1 else NOW + timedelta(seconds=30)
        with patch("portfolio_lab.providers._clock", side_effect=clock):
            result = self.harness(tickers={"ALFA": ticker, "BETA": Ticker()})
        self.assertEqual(calls, ["clock", "history", "clock"])
        self.assertEqual(result["acquired_at"], "2024-01-09T23:00:30+00:00")

    def test_identity_changes_with_data_or_acquisition(self):
        first = self.harness()
        later = self.harness(now=NOW + timedelta(seconds=1))
        tickers = {"ALFA": Ticker(), "BETA": Ticker()}
        tickers["BETA"].table.rows[-1][1]["Close"] = 110
        revised = self.harness(tickers=tickers)
        self.assertEqual(len({first["dataset_id"], later["dataset_id"], revised["dataset_id"]}), 3)

    def test_invalid_study_dates_and_contributions_fail_before_network(self):
        invalid = study()
        invalid["contributions"] = [{"session": "2024-01-11", "amount": 1}]
        with self.assertRaises(ValueError):
            self.harness(specification=invalid)
        for start, end in [("2024-01-10", "2024-01-09"), ("2024-1-5", "2024-01-09"),
                           ("1800-01-01", "2024-01-09")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.harness(start=start, end=end)
        with self.assertRaises(ValueError):
            self.harness(now=datetime(2024, 1, 10))

    def test_import_has_no_optional_dependency_or_network_side_effects(self):
        script = "import sys; import portfolio_lab.providers; assert 'yfinance' not in sys.modules; assert 'pandas_market_calendars' not in sys.modules; assert 'pandas' not in sys.modules"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
