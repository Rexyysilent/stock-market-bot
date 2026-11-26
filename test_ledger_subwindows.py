"""A full coherent acquisition can satisfy smaller windows without new calls."""
from datetime import date, datetime, timedelta, timezone
import sqlite3
import unittest
from unittest.mock import patch
from ledger.prices import (
    bind_cached_window, cache_rows, ensure_window, get_open_close,
    get_window_metadata, resolve_cached_window,
)


class CoherentSubwindowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.rows = [{"session": f"2026-09-{day:02d}", "open": 100 + day, "close": 101 + day}
                     for day in (1, 2, 3, 4, 8)]

    def tearDown(self):
        self.conn.close()

    def store(self, **changes):
        settings = dict(provider="synthetic", currency="USD", acquired_at="2026-09-09T12:00:00Z")
        settings.update(changes)
        return cache_rows(self.conn, "ALFA", self.rows, **settings)

    def test_full_capture_reused_with_original_identity_and_no_request(self):
        original = self.store()
        def forbidden(*args):
            self.fail("subwindow must reuse the complete acquisition")
        self.assertEqual(ensure_window(self.conn, "ALFA", date(2026, 9, 2), date(2026, 9, 4),
                                       forbidden, provider_name="synthetic", currency="USD"), "ok")
        self.assertEqual(get_open_close(self.conn, "ALFA", "2026-09-02", "2026-09-04"), (102, 105))
        metadata = get_window_metadata(self.conn, "ALFA", "2026-09-02", "2026-09-04")
        self.assertEqual(metadata["acquisition_id"], original)
        self.assertEqual(metadata["acquired_at"], "2026-09-09T12:00:00Z")

    def test_provider_currency_and_adjustment_identity_never_blend(self):
        self.store()
        for changed in ({"provider": "other"}, {"currency": "EUR"}, {"adjustment_basis": "raw"}):
            settings = dict(provider="synthetic", currency="USD")
            settings.update(changed)
            self.assertFalse(bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04", **settings))

    def test_two_partial_acquisitions_do_not_create_a_complete_window(self):
        self.rows = self.rows[:2]
        self.store()
        self.rows = [{"session": "2026-09-04", "open": 10, "close": 11}]
        self.store(acquired_at="2026-09-10T12:00:00Z")
        self.assertFalse(bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                                            provider="synthetic", currency="USD"))

    def test_new_vendor_revision_never_rebinds_old_endpoints(self):
        first = self.store()
        self.assertTrue(bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                                           provider="synthetic", currency="USD"))
        self.rows = [{**r, "open": r["open"] / 2, "close": r["close"] / 2} for r in self.rows]
        self.store(acquired_at="2026-09-10T12:00:00Z")
        self.assertFalse(bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                                            provider="synthetic", currency="USD"))
        self.assertEqual(get_window_metadata(self.conn, "ALFA", "2026-09-02", "2026-09-04")["acquisition_id"], first)

    def test_earliest_compatible_capture_is_selected_independent_of_insert_order(self):
        self.store(acquired_at="2026-09-10T12:00:00Z")
        first = self.store(acquired_at="2026-09-09T12:00:00Z")
        bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                           provider="synthetic", currency="USD")
        self.assertEqual(get_window_metadata(self.conn, "ALFA", "2026-09-02", "2026-09-04")["acquisition_id"], first)

    def test_read_only_resolution_uses_original_capture_without_binding_or_writes(self):
        original = self.store()
        self.conn.commit()
        changes = self.conn.total_changes
        windows = list(self.conn.execute("SELECT * FROM price_windows"))
        self.conn.execute("PRAGMA query_only=ON")
        result = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04",
            provider="synthetic", currency="USD",
        )
        self.assertFalse(result["bound"])
        self.assertEqual(result["open_close"], (102, 105))
        self.assertEqual(result["metadata"]["acquisition_id"], original)
        self.assertEqual(result["metadata"]["acquired_at"], "2026-09-09T12:00:00Z")
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(list(self.conn.execute("SELECT * FROM price_windows")), windows)
        self.assertIsNone(get_window_metadata(self.conn, "ALFA", "2026-09-02", "2026-09-04"))

    def test_read_only_resolution_does_not_create_missing_schema(self):
        self.conn.execute("PRAGMA query_only=ON")
        self.assertIsNone(resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="synthetic"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)

    def test_verifier_fetch_permission_does_not_change_cached_subwindow_or_write(self):
        from ledger.prices import _provider_identity
        from ledger.verification import _price_window
        def forbidden(*args):
            self.fail("all requested endpoints are already cached")
        self.store(provider=_provider_identity(forbidden))
        self.conn.commit()
        changes = self.conn.total_changes
        self.conn.execute("PRAGMA query_only=ON")
        results = [_price_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider=forbidden,
            fetch=fetch, cutoff=datetime(2026, 9, 10, tzinfo=timezone.utc),
            exit_close=datetime(2026, 9, 4, 20, tzinfo=timezone.utc),
            strict_price_cutoff=True, memo={},
        ) for fetch in (False, True)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["status"], "ok")
        self.assertAlmostEqual(results[0]["return"], 105 / 102 - 1)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertIsNone(get_window_metadata(self.conn, "ALFA", "2026-09-02", "2026-09-04"))

    def test_resolution_and_binding_select_the_same_earliest_acquisition(self):
        self.store(acquired_at="2026-09-10T12:00:00Z")
        original = self.store(acquired_at="2026-09-09T12:00:00Z")
        before = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="synthetic", currency="USD")
        self.assertEqual(before["metadata"]["acquisition_id"], original)
        bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                           provider="synthetic", currency="USD")
        after = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="synthetic", currency="USD")
        self.assertFalse(before["bound"])
        self.assertTrue(after["bound"])
        self.assertEqual(before["metadata"], after["metadata"])
        self.assertEqual(before["open_close"], after["open_close"])

    def test_existing_binding_wins_over_an_earlier_later_imported_acquisition(self):
        original = self.store(acquired_at="2026-09-10T12:00:00Z")
        bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                           provider="synthetic", currency="USD")
        self.rows = [{**row, "close": 50} for row in self.rows]
        earlier = self.store(acquired_at="2026-09-09T12:00:00Z")
        self.assertNotEqual(earlier, original)
        result = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="synthetic", currency="USD")
        self.assertTrue(result["bound"])
        self.assertEqual(result["metadata"]["acquisition_id"], original)
        self.assertEqual(result["open_close"], (102, 105))

    def test_incompatible_or_damaged_binding_is_exposed_instead_of_bypassed(self):
        original = self.store()
        bind_cached_window(self.conn, "ALFA", "2026-09-02", "2026-09-04",
                           provider="synthetic", currency="USD")
        self.store(provider="other", currency="EUR")
        result = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="other", currency="EUR")
        self.assertTrue(result["bound"])
        self.assertEqual(result["metadata"]["acquisition_id"], original)
        self.assertEqual(result["metadata"]["provider"], "synthetic")
        self.conn.execute("DELETE FROM price_points WHERE acquisition_id=? AND session=?",
                          (original, "2026-09-02"))
        damaged = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="other", currency="EUR")
        self.assertEqual(damaged["metadata"]["acquisition_id"], original)
        self.assertIsNone(damaged["open_close"])

    def test_unspecified_currency_retains_metadata_and_explicit_unknown_is_strict(self):
        self.store()
        result = resolve_cached_window(
            self.conn, "ALFA", "2026-09-02", "2026-09-04", provider="synthetic")
        self.assertEqual(result["metadata"]["currency"], "USD")
        for currency in (None, "EUR"):
            with self.subTest(currency=currency):
                self.assertIsNone(resolve_cached_window(
                    self.conn, "ALFA", "2026-09-02", "2026-09-04",
                    provider="synthetic", currency=currency))


class AcquisitionCompletionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.start, self.end = date(2026, 9, 2), date(2026, 9, 3)
        self.requested = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        self.clock = self.requested

    def tearDown(self):
        self.conn.close()

    def provider(self, ticker, start, end):
        self.clock += timedelta(minutes=10)
        return [{"session": start.isoformat(), "open": 100, "close": 100},
                {"session": end.isoformat(), "open": 110, "close": 110}]

    def test_success_records_response_completion_and_preserves_request_clock(self):
        seen = []
        def completion_clock():
            seen.append(self.clock)
            return self.clock
        result = ensure_window(
            self.conn, "ALFA", self.start, self.end, self.provider,
            now=self.requested, acquisition_clock=completion_clock,
        )
        self.assertEqual(result, "ok")
        metadata = get_window_metadata(self.conn, "ALFA", self.start.isoformat(), self.end.isoformat())
        self.assertEqual(metadata["acquired_at"], "2026-09-10T12:10:00Z")
        self.assertEqual(seen, [self.requested + timedelta(minutes=10)])
        self.assertEqual(self.requested.hour, 12)
        self.assertEqual(self.requested.minute, 0)

    def test_default_clock_is_read_after_response_not_backdated_to_request(self):
        test = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.clock.astimezone(tz) if tz else test.clock.replace(tzinfo=None)
        with patch("ledger.prices.datetime", Clock):
            ensure_window(self.conn, "ALFA", self.start, self.end,
                          self.provider, now=self.requested)
        self.assertEqual(get_window_metadata(
            self.conn, "ALFA", self.start.isoformat(), self.end.isoformat())["acquired_at"],
            "2026-09-10T12:10:00Z")

    def test_empty_response_still_records_miss_at_request_clock(self):
        def empty(*args):
            self.clock += timedelta(minutes=10)
            return []
        def forbidden_clock():
            self.fail("no acquisition was retained")
        state = ensure_window(self.conn, "ALFA", self.start, self.end, empty,
                              now=self.requested, acquisition_clock=forbidden_clock)
        self.assertEqual(state, "empty")
        miss = self.conn.execute("SELECT * FROM price_fetch_misses").fetchone()
        self.assertEqual(miss["last_attempt_at"], self.requested.isoformat())
        self.assertEqual(miss["attempts"], 1)

    def test_cached_response_does_not_consult_completion_clock(self):
        ensure_window(self.conn, "ALFA", self.start, self.end, self.provider,
                      acquisition_clock=lambda: self.clock)
        def forbidden_clock():
            self.fail("cache reuse must keep its original acquisition time")
        self.assertEqual(ensure_window(
            self.conn, "ALFA", self.start, self.end, self.provider,
            now=self.requested, acquisition_clock=forbidden_clock), "ok")
        self.assertEqual(self.clock, self.requested + timedelta(minutes=10))

    def test_explicit_retained_timestamp_is_supported_but_cannot_mix_with_clock(self):
        with self.assertRaisesRegex(ValueError, "choose acquired_at or acquisition_clock"):
            ensure_window(self.conn, "ALFA", self.start, self.end, self.provider,
                          acquired_at=self.requested, acquisition_clock=lambda: self.clock)
        self.assertEqual(self.clock, self.requested)
        stamp = "2026-09-10T12:10:00Z"
        ensure_window(self.conn, "ALFA", self.start, self.end, self.provider,
                      now=self.requested, acquired_at=stamp)
        self.assertEqual(get_window_metadata(
            self.conn, "ALFA", self.start.isoformat(), self.end.isoformat())["acquired_at"], stamp)

    def test_verifier_cutoff_between_request_and_response_rejects_new_bytes(self):
        from ledger.verification import _price_window
        settings = dict(provider=self.provider, memo={}, entry=self.start.isoformat(),
                        exit_=self.end.isoformat(), exit_close=datetime(2026, 9, 3, 20, tzinfo=timezone.utc))
        with patch("ledger.verification._now", side_effect=lambda: self.clock):
            acquired = _price_window(self.conn, "ALFA", fetch=True, cutoff=self.requested,
                                     strict_price_cutoff=False, **settings)
            before_response = _price_window(
                self.conn, "ALFA", fetch=False, cutoff=self.requested + timedelta(minutes=5),
                strict_price_cutoff=True, **{**settings, "memo": {}},
            )
            after_response = _price_window(
                self.conn, "ALFA", fetch=False, cutoff=self.requested + timedelta(minutes=15),
                strict_price_cutoff=True, **{**settings, "memo": {}},
            )
        self.assertEqual(acquired["metadata"]["acquired_at"], "2026-09-10T12:10:00Z")
        self.assertEqual(before_response["status"], "price_acquired_after_cutoff")
        self.assertIsNone(before_response["return"])
        self.assertEqual(after_response["status"], "ok")
        self.assertAlmostEqual(after_response["return"], .1)


if __name__ == "__main__":
    unittest.main()
