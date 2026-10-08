"""Partial or unavailable benchmarks stop being retried after a bounded age.

Every daily run re-downloaded prices for each outcome whose frozen-cohort
benchmark was partial or unavailable, forever: a delisted cohort member
(yfinance "TWO ... possibly delisted") was fetched again on every run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ledger.config import BENCHMARK_RETRY_DAYS
from ledger.db import connect
from ledger.ingest import ingest_file
from ledger.outcomes import _needs_work, mature_outcomes
from test_ledger_benchmark_revisions import MutableProvider, _brief, _schedule

EXIT = "2026-09-22"


class CountingProvider(MutableProvider):
    def __init__(self, available):
        super().__init__(available)
        self.calls = []

    def __call__(self, ticker, start, end):
        self.calls.append(ticker)
        return super().__call__(ticker, start, end)


def _latest(status, exit_session=EXIT):
    return {"basis_run_id": "run", "benchmark_status": status, "exit_session": exit_session}


def _row(status="filled"):
    return {"status": status, "basis_run_id": "run"}


class NeedsWorkTests(unittest.TestCase):
    def at(self, days_after_exit):
        return datetime(2026, 9, 22, tzinfo=timezone.utc) + timedelta(days=days_after_exit)

    def test_retry_window_is_fourteen_days(self):
        self.assertEqual(BENCHMARK_RETRY_DAYS, 14)

    def test_recent_partial_and_unavailable_benchmarks_are_retried(self):
        for status in ("partial", "unavailable"):
            self.assertTrue(_needs_work(_row(), _latest(status), self.at(3)), status)

    def test_old_partial_and_unavailable_benchmarks_are_not_retried(self):
        for status in ("partial", "unavailable"):
            self.assertFalse(_needs_work(_row(), _latest(status), self.at(20)), status)

    def test_complete_benchmarks_are_never_retried(self):
        self.assertFalse(_needs_work(_row(), _latest("complete"), self.at(3)))

    def test_pending_outcomes_are_worked_whatever_their_age(self):
        self.assertTrue(_needs_work(_row("pending"), _latest("partial"), self.at(60)))
        self.assertTrue(_needs_work(_row("pending"), None, self.at(60)))

    def test_a_basis_change_is_worked_whatever_its_age(self):
        latest = dict(_latest("complete"), basis_run_id="older-run")
        self.assertTrue(_needs_work(_row(), latest, self.at(60)))


class RetryCapIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "ledger.db"
        path = self.root / "2026-09-21_120000Z.json"
        path.write_text(json.dumps(_brief("2026-09-21T12:00:00Z")), encoding="utf-8")
        conn = connect(self.db_path)
        ingest_file(path, conn)
        conn.close()
        # First maturation: BBB (a cohort member) has no prices -> partial.
        # One provider class throughout: the price cache keys on provider
        # identity, and a different class would skip the fetch on its own.
        mature_outcomes(self.db_path, now=datetime(2026, 9, 24, tzinfo=timezone.utc),
                        provider=CountingProvider({"AAA", "SPY"}), schedule_provider=_schedule)

    def tearDown(self):
        self.tempdir.cleanup()

    def revisions(self):
        conn = connect(self.db_path)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT benchmark_status FROM outcome_revisions WHERE horizon=1 ORDER BY revision_number")]
        finally:
            conn.close()

    def test_recent_partial_benchmark_recovers(self):
        provider = CountingProvider({"AAA", "BBB", "SPY"})
        mature_outcomes(self.db_path, now=datetime(2026, 9, 25, tzinfo=timezone.utc),
                        provider=provider, schedule_provider=_schedule)
        self.assertIn("BBB", provider.calls)
        self.assertEqual([r["benchmark_status"] for r in self.revisions()], ["partial", "complete"])

    def test_old_partial_benchmark_is_not_fetched_again(self):
        provider = CountingProvider({"AAA", "BBB", "SPY"})
        mature_outcomes(self.db_path, now=datetime(2026, 10, 20, tzinfo=timezone.utc),
                        provider=provider, schedule_provider=_schedule)
        self.assertEqual([r["benchmark_status"] for r in self.revisions()], ["partial"])
        self.assertNotIn("BBB", provider.calls)


if __name__ == "__main__":
    unittest.main()
