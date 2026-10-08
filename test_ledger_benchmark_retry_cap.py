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


def _latest(status, revised_at="2026-09-22T21:00:00Z"):
    return {"basis_run_id": "run", "benchmark_status": status, "exit_session": EXIT,
            "revised_at": revised_at}


def _row(status="filled"):
    return {"status": status, "basis_run_id": "run"}


class NeedsWorkTests(unittest.TestCase):
    def at(self, days_after_exit):
        return datetime(2026, 9, 22, tzinfo=timezone.utc) + timedelta(days=days_after_exit)

    def test_the_window_counts_from_publication_not_from_exit(self):
        # First matured (partial) 20 days after exit, e.g. after weeks without
        # runs: it still gets its retry window.
        late = _latest("partial", revised_at="2026-10-12T12:00:00Z")
        self.assertTrue(_needs_work(_row(), late, self.at(21)))
        self.assertFalse(_needs_work(_row(), late, self.at(20 + BENCHMARK_RETRY_DAYS + 1)))

    def test_recent_partial_and_unavailable_benchmarks_are_retried(self):
        for status in ("partial", "unavailable"):
            self.assertTrue(_needs_work(_row(), _latest(status), self.at(3)), status)

    def test_old_partial_and_unavailable_benchmarks_are_not_retried(self):
        for status in ("partial", "unavailable"):
            self.assertFalse(_needs_work(_row(), _latest(status),
                                         self.at(BENCHMARK_RETRY_DAYS + 2)), status)

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

    def test_late_first_maturity_still_gets_retries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "ledger.db"
            path = root / "2026-09-21_120000Z.json"
            path.write_text(json.dumps(_brief("2026-09-21T12:00:00Z")), encoding="utf-8")
            conn = connect(db_path)
            ingest_file(path, conn)
            conn.close()
            mature_outcomes(db_path, now=datetime(2026, 10, 12, tzinfo=timezone.utc),
                            provider=CountingProvider({"AAA", "SPY"}), schedule_provider=_schedule)
            # The missing member's window ended weeks ago, so it is re-fetched
            # at the next weekly retry, still inside the benchmark retry window.
            mature_outcomes(db_path, now=datetime(2026, 10, 19, tzinfo=timezone.utc),
                            provider=CountingProvider({"AAA", "BBB", "SPY"}), schedule_provider=_schedule)
            conn = connect(db_path)
            try:
                statuses = [r[0] for r in conn.execute(
                    "SELECT benchmark_status FROM outcome_revisions WHERE horizon=1 ORDER BY revision_number")]
            finally:
                conn.close()
            self.assertEqual(statuses, ["partial", "complete"])

    def test_old_partial_benchmark_is_not_fetched_again(self):
        provider = CountingProvider({"AAA", "BBB", "SPY"})
        mature_outcomes(self.db_path, now=datetime(2026, 10, 20, tzinfo=timezone.utc),
                        provider=provider, schedule_provider=_schedule)
        self.assertEqual([r["benchmark_status"] for r in self.revisions()], ["partial"])
        self.assertNotIn("BBB", provider.calls)


if __name__ == "__main__":
    unittest.main()


class FirstPublicationAnchorTests(unittest.TestCase):
    def test_partial_improvements_do_not_restart_the_window(self):
        # First published partial on day 0; a member recovered on day 10 and
        # published another partial revision. Day 15 is past the window
        # measured from the first publication.
        latest = _latest("partial", revised_at="2026-10-02T12:00:00Z")
        now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc) + timedelta(days=BENCHMARK_RETRY_DAYS + 1)
        self.assertFalse(_needs_work(_row(), latest, now,
                                     first_published="2026-09-22T12:00:00Z"))
        self.assertTrue(_needs_work(_row(), latest, now))   # without the anchor: latest only
