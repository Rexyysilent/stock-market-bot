"""Regression checks for WatcherAgent earnings calendar parsing."""
import sys
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, ".")

import agents.watcher_agent as watcher_module
from agents.watcher_agent import WatcherAgent


REFERENCE_DATE = date(2026, 8, 17)


class FakeTicker:
    def __init__(self, ticker):
        self.ticker = ticker

    def get_earnings_dates(self, limit=12):
        if self.ticker != "TSLA":
            return pd.DataFrame()

        earnings_dt = pd.Timestamp(
            datetime.combine(REFERENCE_DATE + timedelta(days=3), time(13, 0)),
            tz="US/Eastern",
        )
        return pd.DataFrame(
            {
                "EPS Estimate": [0.45],
                "Reported EPS": [float("nan")],
                "Surprise(%)": [float("nan")],
            },
            index=pd.DatetimeIndex([earnings_dt], name="Earnings Date"),
        )

    def get_calendar(self):
        return {
            "Earnings Date": [REFERENCE_DATE + timedelta(days=5)],
            "Earnings Average": 0.56,
        }

    @property
    def calendar(self):
        return self.get_calendar()


original_ticker = watcher_module.yf.Ticker
original_stocks = watcher_module.WATCHLIST_STOCKS
original_vulture = watcher_module.WATCHLIST_VULTURE

try:
    watcher_module.yf.Ticker = FakeTicker
    watcher_module.WATCHLIST_STOCKS = ["TSLA", "ROKU"]
    watcher_module.WATCHLIST_VULTURE = []

    run_context = SimpleNamespace(utc_date=REFERENCE_DATE.isoformat())
    earnings = WatcherAgent(run_context=run_context).get_earnings_calendar()
    by_ticker = {entry["ticker"]: entry for entry in earnings}

    assert set(by_ticker) == {"TSLA", "ROKU"}, earnings
    assert by_ticker["TSLA"]["source"] == "yfinance.get_earnings_dates"
    assert by_ticker["TSLA"]["days_until"] == 3
    assert by_ticker["TSLA"]["timing"] == "DMT"
    assert by_ticker["ROKU"]["source"] == "yfinance.calendar"
    assert by_ticker["ROKU"]["days_until"] == 5
    assert by_ticker["ROKU"]["eps_estimate"] == 0.56
finally:
    watcher_module.yf.Ticker = original_ticker
    watcher_module.WATCHLIST_STOCKS = original_stocks
    watcher_module.WATCHLIST_VULTURE = original_vulture

print("earnings calendar regression checks passed")
