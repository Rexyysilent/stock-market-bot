"""Transient empty price responses remain retryable in the outcome ledger."""
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ledger.config import BENCHMARK
from ledger.db import connect
from ledger.outcomes import mature_outcomes


class OutcomeRetryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "ledger.db"
        conn = connect(self.db_path)
        conn.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?)",
            ("run-1", "2026-09-01T12:00:00Z", None, "synthetic", "2.6.3"),
        )
        conn.execute(
            "INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("record-1", "source-1", "test", None, "TEST", "long", None,
             None, "run-1", "run-1", "2026-09-01T12:00:00Z", "equity", "{}"),
        )
        conn.execute("INSERT INTO outcomes(record_id,horizon,status) VALUES(?,?,?)",
                     ("record-1", 1, "pending"))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def schedule(*_args):
        return [
            ("2026-09-01", datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc),
             datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)),
            ("2026-09-02", datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc),
             datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)),
        ]

    def status(self):
        conn = connect(self.db_path)
        try:
            return conn.execute("SELECT status FROM outcomes").fetchone()["status"]
        finally:
            conn.close()

    def test_empty_then_available_response_fills_on_retry(self):
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        first = mature_outcomes(self.db_path, now=now, provider=lambda *_: [],
                                schedule_provider=self.schedule, universe=[])
        self.assertEqual(first, {"filled": 0, "unpriceable": 0})
        self.assertEqual(self.status(), "pending")

        def available(ticker, start, end):
            self.assertIn(ticker, ("TEST", BENCHMARK))
            self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 2)))
            return [
                {"session": "2026-09-01", "open": 100, "close": 101},
                {"session": "2026-09-02", "open": 102, "close": 110},
            ]

        second = mature_outcomes(self.db_path, now=now, provider=available,
                                 schedule_provider=self.schedule, universe=[])
        self.assertEqual(second, {"filled": 1, "unpriceable": 0})
        self.assertEqual(self.status(), "filled")


if __name__ == "__main__":
    unittest.main()
