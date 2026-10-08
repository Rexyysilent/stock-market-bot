"""Empty or incomplete price windows are re-fetched on a bounded schedule.

A pending outcome whose own ticker has no data (yfinance "TWO ... possibly
delisted", an attention signal from 2026-08-20) stays pending by design: an
empty provider response does not establish terminal absence. But it was
re-downloaded on every daily run, and a cohort member shared by many outcome
rows was fetched once per row within one run. Misses are now remembered:
a window that ended in the last PRICE_MISS_RECENT_DAYS days is retried every
run, an older one at most every PRICE_MISS_BACKOFF_DAYS days, and the same
window is never fetched twice within one run.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ledger.config import PRICE_MISS_BACKOFF_DAYS, PRICE_MISS_RECENT_DAYS
from ledger.db import connect
from ledger.ingest import ingest_file
from ledger.outcomes import mature_outcomes
from ledger.prices import ensure_window
from test_ledger_benchmark_revisions import _brief, _schedule

START, END = date(2026, 8, 20), date(2026, 8, 27)


class Recorder:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = 0

    def __call__(self, ticker, start, end):
        self.calls += 1
        return list(self.rows)


def _at(days_after_end):
    return datetime(2026, 8, 27, 21, tzinfo=timezone.utc) + timedelta(days=days_after_end)


class FetchBackoffTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.conn = connect(Path(self.tempdir.name) / "ledger.db")

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_old_empty_window_is_retried_at_most_once_per_backoff(self):
        provider = Recorder()
        first = _at(30)
        self.assertEqual(ensure_window(self.conn, "TWO", START, END, provider, now=first), "empty")
        for day in range(1, PRICE_MISS_BACKOFF_DAYS):
            self.assertEqual(ensure_window(self.conn, "TWO", START, END, provider,
                                           now=first + timedelta(days=day)), "empty")
        self.assertEqual(provider.calls, 1)
        ensure_window(self.conn, "TWO", START, END, provider,
                      now=first + timedelta(days=PRICE_MISS_BACKOFF_DAYS))
        self.assertEqual(provider.calls, 2)

    def test_recent_empty_window_is_retried_every_run_but_once_per_run(self):
        provider = Recorder()
        for day in (1, 2, 3):
            run_memo = {}  # one run
            ensure_window(self.conn, "AAA", START, END, provider, now=_at(day), run_memo=run_memo)
            ensure_window(self.conn, "AAA", START, END, provider, now=_at(day), run_memo=run_memo)
        self.assertEqual(provider.calls, 3)
        self.assertLessEqual(3, PRICE_MISS_RECENT_DAYS)

    def test_data_arriving_after_a_miss_is_used_and_clears_the_miss(self):
        ensure_window(self.conn, "AAA", START, END, Recorder(), now=_at(1))
        good = Recorder([{"session": "2026-08-20", "open": 10, "close": 10},
                         {"session": "2026-08-27", "open": 11, "close": 11}])
        self.assertEqual(ensure_window(self.conn, "AAA", START, END, good, now=_at(2)), "ok")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM price_fetch_misses").fetchone()[0], 0)

    def test_callers_without_a_clock_keep_retrying(self):
        provider = Recorder()
        ensure_window(self.conn, "TWO", START, END, provider)
        ensure_window(self.conn, "TWO", START, END, provider)
        self.assertEqual(provider.calls, 2)

    def test_errors_are_not_remembered_as_misses(self):
        def broken(ticker, start, end):
            raise RuntimeError("network")
        self.assertEqual(ensure_window(self.conn, "TWO", START, END, broken, now=_at(30)), "error")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM price_fetch_misses").fetchone()[0], 0)


class PendingOutcomeBackoffTests(unittest.TestCase):
    """End to end: a pending outcome whose ticker has no prices."""

    def test_daily_runs_fetch_a_dead_ticker_weekly_not_daily(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "ledger.db"
            path = root / "2026-09-21_120000Z.json"
            path.write_text(json.dumps(_brief("2026-09-21T12:00:00Z")), encoding="utf-8")
            conn = connect(db_path)
            ingest_file(path, conn)
            conn.close()
            calls = []

            def provider(ticker, start, end):
                calls.append(ticker)
                if ticker == "AAA":
                    return []          # the signal's own ticker never has data
                return [{"session": start.isoformat(), "open": 100, "close": 100},
                        {"session": end.isoformat(), "open": 100, "close": 101}]

            first = datetime(2026, 10, 20, tzinfo=timezone.utc)
            for day in range(14):
                mature_outcomes(db_path, now=first + timedelta(days=day),
                                provider=provider, schedule_provider=_schedule)
            # The horizon-1 window ended 2026-09-22, long ago: two attempts in
            # fourteen daily runs (days 0 and 7) instead of fourteen.
            self.assertEqual(calls.count("AAA"), 2)
            conn = connect(db_path)
            try:
                status = conn.execute(
                    "SELECT status FROM outcomes WHERE horizon=1").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(status, "pending")   # still not declared unpriceable


if __name__ == "__main__":
    unittest.main()
