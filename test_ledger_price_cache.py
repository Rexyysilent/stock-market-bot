"""Offline regression tests for overlapping outcome windows and bad quotes."""
from datetime import date
import sqlite3
import unittest

from ledger.prices import (
    cache_rows,
    ensure_window,
    get_open_close,
    get_window_metadata,
)


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
        cache_rows(
            self.conn,
            "TEST",
            [{"session": "2026-09-01", "open": 10, "close": 11},
             {"session": "2026-09-08", "open": 12, "close": 13}],
            provider="synthetic-vendor",
        )
        def forbidden(*args):
            self.fail("a complete cached endpoint pair must not fetch")
        self.assertEqual(
            ensure_window(
                self.conn, "TEST", self.start, self.end, forbidden,
                provider_name="synthetic-vendor",
            ),
            "ok",
        )

    def test_incompatible_basis_cache_hit_is_rejected_without_refetch(self):
        cache_rows(
            self.conn,
            "TEST",
            [{"session": "2026-09-01", "open": 10, "close": 11},
             {"session": "2026-09-08", "open": 12, "close": 13}],
            provider="synthetic-vendor",
            adjustment_basis="split_dividend_adjusted",
        )
        refetches = []
        self.assertEqual(
            ensure_window(
                self.conn, "TEST", self.start, self.end,
                lambda *args: refetches.append(args),
                provider_name="synthetic-vendor",
                adjustment_basis="raw",
            ),
            "error",
        )
        self.assertEqual(refetches, [])

    def test_overlapping_refill_preserves_first_window_acquisition(self):
        first_start = date(2026, 9, 21)
        first_end = date(2026, 9, 22)
        first_id = cache_rows(
            self.conn,
            "TEST",
            [
                {"session": "2026-09-21", "open": 100, "close": 102},
                {"session": "2026-09-22", "open": 105, "close": 110},
            ],
            provider="synthetic-vendor",
            adjustment_basis="split_dividend_adjusted",
            currency="USD",
            acquired_at="2026-09-22T21:00:00Z",
        )
        self.assertEqual(
            get_open_close(self.conn, "TEST", "2026-09-21", "2026-09-22"),
            (100, 110),
        )

        def revised_overlap(ticker, start, end):
            self.assertEqual((ticker, start, end), (
                "TEST", date(2026, 9, 18), date(2026, 9, 21)
            ))
            return [
                {"session": "2026-09-18", "open": 49, "close": 50},
                {"session": "2026-09-21", "open": 50, "close": 51},
            ]

        self.assertEqual(
            ensure_window(
                self.conn, "TEST", date(2026, 9, 18), first_start,
                revised_overlap,
                provider_name="synthetic-vendor",
                adjustment_basis="split_dividend_adjusted",
                currency="USD",
                acquired_at="2026-09-23T21:00:00Z",
            ),
            "ok",
        )
        refetches = []
        self.assertEqual(
            ensure_window(
                self.conn, "TEST", first_start, first_end,
                lambda *args: refetches.append(args),
                provider_name="synthetic-vendor",
                adjustment_basis="split_dividend_adjusted",
                currency="USD",
            ),
            "ok",
        )
        self.assertEqual(refetches, [])
        self.assertEqual(
            get_open_close(self.conn, "TEST", "2026-09-21", "2026-09-22"),
            (100, 110),
        )
        first_metadata = get_window_metadata(
            self.conn, "TEST", "2026-09-21", "2026-09-22"
        )
        overlap_metadata = get_window_metadata(
            self.conn, "TEST", "2026-09-18", "2026-09-21"
        )
        self.assertEqual(first_metadata["acquisition_id"], first_id)
        self.assertEqual(first_metadata["provider"], "synthetic-vendor")
        self.assertEqual(first_metadata["interval"], "1d")
        self.assertEqual(first_metadata["requested_start"], "2026-09-21")
        self.assertEqual(first_metadata["requested_end"], "2026-09-22")
        self.assertEqual(first_metadata["adjustment_basis"], "split_dividend_adjusted")
        self.assertEqual(first_metadata["currency"], "USD")
        self.assertEqual(first_metadata["acquired_at"], "2026-09-22T21:00:00Z")
        self.assertNotEqual(
            first_metadata["acquisition_id"], overlap_metadata["acquisition_id"]
        )
        self.assertNotEqual(
            first_metadata["content_sha256"], overlap_metadata["content_sha256"]
        )

    def test_split_and_dividend_conventions_have_distinct_identities(self):
        rows_by_basis = {
            "raw": [
                {"session": "2026-09-01", "open": 100, "close": 100},
                {"session": "2026-09-08", "open": 50, "close": 50},
            ],
            "split_adjusted": [
                {"session": "2026-09-01", "open": 50, "close": 50},
                {"session": "2026-09-08", "open": 50, "close": 50},
            ],
            "split_dividend_adjusted": [
                {"session": "2026-09-01", "open": 49, "close": 49},
                {"session": "2026-09-08", "open": 50, "close": 50},
            ],
        }
        acquisition_ids = {}
        for index, (basis, rows) in enumerate(rows_by_basis.items(), start=1):
            acquisition_ids[basis] = cache_rows(
                self.conn,
                "SPLIT",
                rows,
                provider="synthetic-vendor",
                adjustment_basis=basis,
                currency="USD",
                acquired_at=f"2026-09-0{index}T21:00:00Z",
            )

        self.assertEqual(len(set(acquisition_ids.values())), 3)
        stored = {
            row["adjustment_basis"]: row
            for row in self.conn.execute(
                """SELECT acquisition_id,adjustment_basis,content_sha256
                   FROM price_acquisitions WHERE ticker='SPLIT'"""
            )
        }
        self.assertEqual(set(stored), set(rows_by_basis))
        self.assertEqual(len({row["content_sha256"] for row in stored.values()}), 3)

        def pair(acquisition_id):
            rows = self.conn.execute(
                """SELECT session,open,close FROM price_points
                   WHERE acquisition_id=? ORDER BY session""",
                (acquisition_id,),
            ).fetchall()
            return rows[0]["open"], rows[-1]["close"]

        raw_pair = pair(acquisition_ids["raw"])
        split_pair = pair(acquisition_ids["split_adjusted"])
        dividend_pair = pair(acquisition_ids["split_dividend_adjusted"])
        self.assertEqual(raw_pair[1] / raw_pair[0] - 1, -0.5)
        self.assertEqual(split_pair[1] / split_pair[0] - 1, 0.0)
        self.assertAlmostEqual(dividend_pair[1] / dividend_pair[0] - 1, 1 / 49)

        flat_rows = [
            {"session": "2026-09-01", "open": 25, "close": 25},
            {"session": "2026-09-08", "open": 26, "close": 26},
        ]
        flat_raw_id = cache_rows(
            self.conn,
            "FLAT",
            flat_rows,
            provider="synthetic-vendor",
            adjustment_basis="raw",
            acquired_at="2026-09-09T21:00:00Z",
        )
        flat_adjusted_id = cache_rows(
            self.conn,
            "FLAT",
            flat_rows,
            provider="synthetic-vendor",
            adjustment_basis="split_dividend_adjusted",
            acquired_at="2026-09-09T21:00:00Z",
        )
        self.assertNotEqual(flat_raw_id, flat_adjusted_id)
        flat_hashes = self.conn.execute(
            """SELECT DISTINCT content_sha256 FROM price_acquisitions
               WHERE ticker='FLAT'"""
        ).fetchall()
        self.assertEqual(len(flat_hashes), 1)

    def test_vendor_revision_is_new_content_and_does_not_rebind_window(self):
        original_id = cache_rows(
            self.conn,
            "REVISED",
            [
                {"session": "2026-09-01", "open": 100, "close": 101},
                {"session": "2026-09-08", "open": 109, "close": 110},
            ],
            provider="synthetic-vendor",
            adjustment_basis="split_dividend_adjusted",
            currency="USD",
            acquired_at="2026-09-09T21:00:00Z",
        )
        revision_id = cache_rows(
            self.conn,
            "REVISED",
            [
                {"session": "2026-09-01", "open": 98, "close": 99},
                {"session": "2026-09-08", "open": 107, "close": 108},
            ],
            provider="synthetic-vendor",
            adjustment_basis="split_dividend_adjusted",
            currency="USD",
            acquired_at="2026-09-10T21:00:00Z",
        )
        self.assertNotEqual(original_id, revision_id)
        revision_hashes = self.conn.execute(
            """SELECT DISTINCT content_sha256 FROM price_acquisitions
               WHERE ticker='REVISED'"""
        ).fetchall()
        self.assertEqual(len(revision_hashes), 2)
        metadata = get_window_metadata(
            self.conn, "REVISED", "2026-09-01", "2026-09-08"
        )
        self.assertEqual(metadata["acquisition_id"], original_id)
        self.assertEqual(
            get_open_close(self.conn, "REVISED", "2026-09-01", "2026-09-08"),
            (100, 110),
        )

    def test_legacy_unknown_basis_is_refetched_as_one_window(self):
        self.conn.executemany(
            "INSERT INTO prices(ticker,session,open,close) VALUES(?,?,?,?)",
            [
                ("LEGACY", "2026-09-01", 100, 101),
                ("LEGACY", "2026-09-08", 109, 110),
            ],
        )
        calls = []

        def provider(ticker, start, end):
            calls.append((ticker, start, end))
            return [
                {"session": "2026-09-01", "open": 50, "close": 50.5},
                {"session": "2026-09-08", "open": 54.5, "close": 55},
            ]

        self.assertEqual(
            ensure_window(self.conn, "LEGACY", self.start, self.end, lambda *_: []),
            "empty",
        )
        self.assertIsNone(
            get_open_close(self.conn, "LEGACY", "2026-09-01", "2026-09-08")
        )
        self.assertEqual(
            ensure_window(self.conn, "LEGACY", self.start, self.end, provider),
            "ok",
        )
        self.assertEqual(calls, [("LEGACY", self.start, self.end)])
        self.assertEqual(
            get_open_close(self.conn, "LEGACY", "2026-09-01", "2026-09-08"),
            (50, 55),
        )

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
