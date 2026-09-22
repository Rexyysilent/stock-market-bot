"""Raw outcome and frozen-benchmark revision regressions."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ledger.db import connect
from ledger.ingest import ingest_file
from ledger.outcomes import mature_outcomes


def _brief(generated_at):
    return {
        "schema_version": "2.8",
        "pipeline_version": "2.6.5",
        "generated_at": generated_at,
        "universe": {"instrumented_tickers": ["AAA", "BBB"]},
        "sections": {
            "insider_clusters": [{
                "ticker": "AAA",
                "cluster_direction": "buy",
                "record_id": "shared-signal",
            }],
        },
    }


def _schedule(*_args):
    return [
        ("2026-09-21", datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc),
         datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)),
        ("2026-09-22", datetime(2026, 9, 22, 13, 30, tzinfo=timezone.utc),
         datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)),
        ("2026-09-23", datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc),
         datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)),
    ]


class MutableProvider:
    def __init__(self, available):
        self.available = set(available)

    def __call__(self, ticker, start, end):
        if ticker not in self.available:
            return []
        returns = {"AAA": 0.1, "BBB": -0.1, "SPY": 0.02}
        value = returns[ticker]
        return [
            {"session": start.isoformat(), "open": 100, "close": 100},
            {"session": end.isoformat(), "open": 100, "close": 100 * (1 + value)},
        ]


class WindowProvider:
    def __call__(self, ticker, start, end):
        value = 0.02 if ticker == "SPY" else (
            0.05 if start == date(2026, 9, 21) else 0.1
        )
        return [
            {"session": start.isoformat(), "open": 100, "close": 100},
            {"session": end.isoformat(), "open": 100, "close": 100 * (1 + value)},
        ]


class LedgerBenchmarkRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "ledger.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def write(self, name, document):
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def outcome(self):
        conn = connect(self.db_path)
        try:
            return dict(conn.execute(
                """SELECT * FROM outcomes WHERE horizon=1"""
            ).fetchone())
        finally:
            conn.close()

    def revisions(self):
        conn = connect(self.db_path)
        try:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM outcome_revisions
                   WHERE horizon=1 ORDER BY revision_number"""
            )]
        finally:
            conn.close()

    def test_partial_benchmark_then_complete_recovery_appends_revision(self):
        path = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z"),
        )
        conn = connect(self.db_path)
        ingest_file(path, conn)
        conn.close()
        provider = MutableProvider({"AAA", "SPY"})
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)

        first = mature_outcomes(
            self.db_path, now=now, provider=provider,
            schedule_provider=_schedule, universe=("CCC",),
        )
        self.assertEqual(first, {"filled": 1, "unpriceable": 0})
        partial_outcome = self.outcome()
        partial_revisions = self.revisions()
        self.assertEqual(partial_outcome["status"], "filled")
        self.assertAlmostEqual(partial_outcome["ret"], 0.1)
        self.assertAlmostEqual(partial_outcome["spy_ret"], 0.02)
        self.assertAlmostEqual(partial_outcome["excess"], 0.08)
        self.assertIsNone(partial_outcome["univ_ret"])
        self.assertEqual(len(partial_revisions), 1)
        partial = partial_revisions[0]
        self.assertEqual(partial["benchmark_status"], "partial")
        self.assertEqual((partial["expected_n"], partial["covered_n"]), (2, 1))
        self.assertAlmostEqual(partial["covered_weight"], 0.5)
        self.assertEqual(json.loads(partial["missing_members_json"]), ["BBB"])
        self.assertAlmostEqual(partial["partial_univ_ret"], 0.05)
        self.assertIsNone(partial["univ_ret"])
        original_revision = dict(partial)

        provider.available.add("BBB")
        second = mature_outcomes(
            self.db_path, now=now, provider=provider,
            schedule_provider=_schedule, universe=("CCC",),
        )
        self.assertEqual(second, {"filled": 0, "unpriceable": 0})
        complete_outcome = self.outcome()
        revisions = self.revisions()
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0], original_revision)
        complete = revisions[1]
        self.assertEqual(complete["benchmark_status"], "complete")
        self.assertEqual(complete["revision_reason"], "benchmark_recovery")
        self.assertEqual((complete["expected_n"], complete["covered_n"]), (2, 2))
        self.assertAlmostEqual(complete["covered_weight"], 1.0)
        self.assertEqual(json.loads(complete["missing_members_json"]), [])
        self.assertAlmostEqual(complete["univ_ret"], 0.0)
        self.assertAlmostEqual(complete_outcome["univ_ret"], 0.0)

    def test_late_earlier_sighting_creates_explicit_window_revision(self):
        later = self.write(
            "2026-09-22_120000Z.json",
            _brief("2026-09-22T12:00:00Z"),
        )
        earlier = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z"),
        )
        conn = connect(self.db_path)
        ingest_file(later, conn)
        conn.close()
        provider = WindowProvider()
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        mature_outcomes(
            self.db_path, now=now, provider=provider,
            schedule_provider=_schedule,
        )
        first_revision = self.revisions()[0]
        self.assertEqual(first_revision["basis_run_id"], later.stem)
        self.assertEqual(first_revision["entry_session"], "2026-09-22")
        self.assertAlmostEqual(first_revision["ret"], 0.1)

        conn = connect(self.db_path)
        ingest_file(earlier, conn)
        conn.close()
        mature_outcomes(
            self.db_path, now=now, provider=provider,
            schedule_provider=_schedule,
        )
        revisions = self.revisions()
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0], first_revision)
        revised = revisions[1]
        self.assertEqual(revised["revision_reason"], "sighting_basis_change")
        self.assertEqual(revised["basis_run_id"], earlier.stem)
        self.assertEqual(revised["entry_session"], "2026-09-21")
        self.assertAlmostEqual(revised["ret"], 0.05)
        projected = self.outcome()
        self.assertEqual(projected["entry_session"], "2026-09-21")
        self.assertAlmostEqual(projected["ret"], 0.05)

    def test_unchanged_retry_is_noop_and_complete_is_not_revisited(self):
        path = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z"),
        )
        conn = connect(self.db_path)
        ingest_file(path, conn)
        conn.close()
        calls = []
        provider = MutableProvider({"AAA", "SPY"})

        def counted(ticker, start, end):
            calls.append(ticker)
            return provider(ticker, start, end)

        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        mature_outcomes(self.db_path, now=now, provider=counted,
                        schedule_provider=_schedule)
        mature_outcomes(self.db_path, now=now, provider=counted,
                        schedule_provider=_schedule)
        self.assertEqual(len(self.revisions()), 1)

        provider.available.add("BBB")
        mature_outcomes(self.db_path, now=now, provider=counted,
                        schedule_provider=_schedule)
        revisions = self.revisions()
        self.assertEqual([r["benchmark_status"] for r in revisions],
                         ["partial", "complete"])
        # Recovery reuses the cached AAA acquisition; only BBB gained a basis.
        first_members = json.loads(revisions[0]["member_results_json"])
        second_members = json.loads(revisions[1]["member_results_json"])
        self.assertEqual(first_members["AAA"]["acquisition_id"],
                         second_members["AAA"]["acquisition_id"])
        self.assertIsNotNone(second_members["BBB"]["acquisition_id"])

        calls.clear()
        mature_outcomes(self.db_path, now=now, provider=counted,
                        schedule_provider=_schedule)
        self.assertEqual(len(self.revisions()), 2)
        self.assertEqual(calls, [])

    def test_legacy_unfrozen_cohort_refuses_benchmark_not_raw(self):
        legacy = _brief("2026-09-21T12:00:00Z")
        legacy["pipeline_version"] = "2.5.1"
        del legacy["universe"]
        path = self.write("2026-09-21_120000Z.json", legacy)
        conn = connect(self.db_path)
        ingest_file(path, conn)
        conn.close()
        calls = []

        def counted(ticker, start, end):
            calls.append(ticker)
            return MutableProvider({"AAA", "BBB", "SPY"})(ticker, start, end)

        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        result = mature_outcomes(
            self.db_path, now=now, provider=counted,
            schedule_provider=_schedule, universe=("AAA", "BBB"),
        )
        self.assertEqual(result, {"filled": 1, "unpriceable": 0})
        revision = self.revisions()[0]
        self.assertEqual(revision["benchmark_status"], "refused")
        self.assertEqual(revision["benchmark_reason"], "legacy_unfrozen_cohort")
        self.assertEqual(revision["expected_n"], 0)
        self.assertIsNone(revision["univ_ret"])
        self.assertAlmostEqual(revision["ret"], 0.1)
        self.assertAlmostEqual(revision["excess"], 0.08)
        self.assertIsNone(self.outcome()["univ_ret"])
        # The runtime universe is never consulted as a substitute cohort.
        self.assertNotIn("BBB", calls)

    def test_filled_outcome_without_revision_is_preserved(self):
        path = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z"),
        )
        conn = connect(self.db_path)
        ingest_file(path, conn)
        conn.execute(
            """UPDATE outcomes SET entry_session='2026-09-22',
                 exit_session='2026-09-23',entry_open=100,exit_close=101,
                 ret=0.01,spy_ret=0.0,excess=0.01,univ_ret=0.5,status='filled'
               WHERE horizon=1"""
        )
        conn.commit()
        conn.close()
        before = self.outcome()
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        mature_outcomes(self.db_path, now=now,
                        provider=MutableProvider({"AAA", "BBB", "SPY"}),
                        schedule_provider=_schedule)
        self.assertEqual(self.outcome(), before)
        self.assertEqual(self.revisions(), [])


if __name__ == "__main__":
    unittest.main()
