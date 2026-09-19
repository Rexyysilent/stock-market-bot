"""Offline regression tests for overlapping outcome windows and bad quotes."""
from datetime import date
import sqlite3
import unittest

from ledger.prices import cache_rows, ensure_window, get_open_close


class PriceCacheTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE prices (ticker TEXT, session TEXT, open REAL, close REAL, PRIMARY KEY(ticker, session))")
        self.start = date(2026, 9, 1)
        self.end = date(2026, 9, 8)

    def tearDown(self):
        self.conn.close()

    def test_partial_window_is_refetched(self):
        cache_rows(self.conn, "TEST", [{"session": "2026-09-01", "open": 10, "close": 11}])
        calls = []
        def provider(ticker, start, end):
            calls.append((ticker, start, end))
            return [{"session": "2026-09-01", "open": 5, "close": 5.5},
                    {"session": "2026-09-08", "open": 6, "close": 6.5}]
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, provider), "ok")
        self.assertEqual(len(calls), 1, "one row does not satisfy a later outcome window")
        # Both endpoints must come from the new adjusted-price acquisition.
        self.assertEqual(get_open_close(self.conn, "TEST", "2026-09-01", "2026-09-08"), (5, 6.5))

    def test_complete_endpoints_do_not_refetch(self):
        cache_rows(self.conn, "TEST", [{"session": "2026-09-01", "open": 10, "close": 11},
                                       {"session": "2026-09-08", "open": 12, "close": 13}])
        def forbidden(*args):
            self.fail("a complete cached endpoint pair must not fetch")
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, forbidden), "ok")

    def test_incomplete_refill_is_retryable_not_false_ok(self):
        partial = [{"session": "2026-09-01", "open": 10, "close": 11}]
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, lambda *a: partial), "incomplete")
        self.assertIsNone(get_open_close(self.conn, "TEST", "2026-09-01", "2026-09-08"))

    def test_new_exit_does_not_mix_with_old_adjustment_basis(self):
        cache_rows(self.conn, "TEST", [{"session": "2026-09-01", "open": 100, "close": 110}])
        new_exit_only = [{"session": "2026-09-08", "open": 6, "close": 6.5}]
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, lambda *a: new_exit_only), "incomplete")
        self.assertIsNone(get_open_close(self.conn, "TEST", "2026-09-01", "2026-09-08"))

    def test_invalid_prices_never_enter_outcomes(self):
        for value in (float("inf"), float("-inf"), float("nan"), 0, -1, True, "not-a-price"):
            with self.subTest(value=value):
                self.conn.execute("DELETE FROM prices")
                cache_rows(self.conn, "TEST", [{"session": "2026-09-01", "open": value, "close": 11},
                                               {"session": "2026-09-08", "open": 12, "close": 13}])
                self.assertIsNone(get_open_close(self.conn, "TEST", "2026-09-01", "2026-09-08"))

    def test_provider_failure_and_empty_are_distinct(self):
        def failed(*args):
            raise TimeoutError("synthetic timeout")
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, failed), "error")
        self.assertEqual(ensure_window(self.conn, "TEST", self.start, self.end, lambda *a: []), "empty")

    def test_reversed_window_is_rejected(self):
        with self.assertRaises(ValueError):
            ensure_window(self.conn, "TEST", self.end, self.start, lambda *a: [])


if __name__ == "__main__":
    unittest.main()
