"""Synthetic regressions for complete-population archived-cohort verification."""
from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ledger.db import connect
from ledger.ingest import ingest_file
from ledger.prices import _provider_identity, cache_rows
from ledger.verification import VerificationPolicy, build_verification, render_markdown

UTC = timezone.utc
NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
CUTOFF = datetime(2026, 9, 18, 22, tzinfo=UTC)


def no_network(*args):
    raise AssertionError("verification must not contact a provider")


def synthetic_schedule(start, end):
    """A fabricated weekday schedule; holiday behavior uses a separate fixture."""
    rows = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            rows.append((current.isoformat(),
                         datetime.combine(current, time(13, 30), UTC),
                         datetime.combine(current, time(20), UTC)))
        current += timedelta(days=1)
    return rows


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = self.root / "ledger.db"
        self.conn = connect(self.db)
        self.policy = VerificationPolicy(
            "test-policy", "2026-09-01T00:00:00Z", 0.05, 10, horizons=(1,))

    def tearDown(self):
        self.conn.close()
        self.temporary.cleanup()

    def archive(self, name="run-a", generated="2026-09-14T12:00:00Z", *,
                members=("AAA", "BBB"), signals=(), era="2.6.5", changes=None):
        # All these documents and prices are fabricated. Healthy input assertions
        # exercise the accepted branch; dedicated tests exercise declared demos.
        document = {
            "schema_version": "2.8", "pipeline_version": era,
            "generated_at": generated,
            "health": {"status": "OK", "errors": []},
            "universe": {"instrumented_tickers": list(members)},
            "sections": {"baseline_alerts": list(signals)},
        }
        if changes:
            document.update(changes)
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        ingest_file(path, self.conn)
        return path

    def signal(self, ticker="AAA", direction="long", *, record_id=None, as_of=None):
        signal_name = {"long": "BULLISH", "short": "BEARISH", "none": "NEUTRAL"}[direction]
        return {
            "ticker": ticker, "signal": signal_name, "metric": "synthetic",
            "z_score": 3, "record_id": record_id or f"{ticker}-{direction}",
            "as_of": as_of or "2026-09-14T11:00:00Z",
        }

    def prices(self, ticker, ret=0.0, *, entry="2026-09-14", exit_="2026-09-15",
               acquired="2026-09-16T00:00:00Z", currency=None, provider=no_network):
        cache_rows(
            self.conn, ticker,
            [{"session": entry, "open": 100, "close": 100},
             {"session": exit_, "open": 100, "close": 100 * (1 + ret)}],
            requested_start=entry, requested_end=exit_,
            provider=_provider_identity(provider), acquired_at=acquired, currency=currency)
        self.conn.commit()

    def report(self, **kwargs):
        args = {"policy": self.policy, "as_of": CUTOFF,
                "provider": no_network, "schedule_provider": synthetic_schedule}
        args.update(kwargs)
        with patch("ledger.verification._now", return_value=NOW):
            return build_verification(self.db, **args)

    def complete(self):
        self.prices("AAA", 0.12)
        self.prices("BBB", 0.02)
        self.prices("SPY", 0.02)

    def test_no_signal_run_still_contains_large_mover_and_full_population(self):
        self.archive()
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["expected_observations"], 2)
        self.assertEqual(report["summary"]["observed"], 2)
        self.assertEqual(report["summary"]["uncaptured_large_moves"], 1)
        row = next(row for row in report["observations"] if row["ticker"] == "AAA")
        self.assertEqual(row["capture_class"], "no_recorded_signal")
        self.assertAlmostEqual(row["absolute_excess_after_friction"], 0.099)
        self.assertEqual(row["entry_session"], "2026-09-14")
        self.assertEqual(row["exit_session"], "2026-09-15")
        self.assertIn("following session close", report["definitions"]["horizon"])
        self.assertIn("| +1 | 2 / 2 | 0 | 0 | 1 | 0 / 1", render_markdown(report))
        self.assertIn("no_recorded_signal | 2 / 2 | 1 / 2 | 1 / 1", render_markdown(report))
        self.assertEqual(report["family_diagnostics"]["observation_count"], 2)

    def test_missing_member_never_disappears_from_denominator_or_becomes_zero(self):
        self.archive()
        self.prices("AAA", 0.12)
        self.prices("SPY", 0.02)
        report = self.report()
        self.assertEqual(report["summary"]["matured_expected"], 2)
        self.assertEqual(report["summary"]["observed"], 1)
        self.assertEqual(report["summary"]["unavailable"], 1)
        self.assertEqual(report["summary"]["outcome_coverage"], 0.5)
        row = next(row for row in report["observations"] if row["ticker"] == "BBB")
        self.assertIsNone(row["raw_return"])
        self.assertIsNone(row["large_move"])
        self.assertIsNone(row["capture_class"])

    def test_price_bound_before_exit_close_cannot_supply_a_completed_return(self):
        self.archive(members=("AAA",))
        self.prices("AAA", .5, acquired="2026-09-15T19:59:59Z")
        self.prices("SPY", .01, acquired="2026-09-15T20:00:00Z")
        row = self.report()["observations"][0]
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["price"]["status"], "price_acquired_before_exit_close")
        self.assertIsNone(row["raw_return"])
        self.assertEqual(row["benchmark_price"]["status"], "ok")

    def test_subwindow_uses_earliest_acquisition_with_a_completed_exit(self):
        self.archive(members=("AAA",))
        self.prices("SPY")
        for acquired, close in (("2026-09-15T18:00:00Z", 110), ("2026-09-16T12:00:00Z", 120)):
            cache_rows(self.conn, "AAA", [{"session": "2026-09-14", "open": 100},
                                          {"session": "2026-09-15", "close": close}],
                       requested_start="2026-09-11", requested_end="2026-09-15",
                       acquired_at=acquired, provider=_provider_identity(no_network))
        self.conn.commit()
        row = self.report()["observations"][0]
        self.assertAlmostEqual(row["raw_return"], .2)
        self.assertEqual(row["price"]["metadata"]["acquired_at"], "2026-09-16T12:00:00Z")

    def test_fetch_can_replace_an_unbound_preclose_candidate(self):
        calls = []
        def provider(ticker, start, end):
            calls.append(ticker)
            return [{"session": start.isoformat(), "open": 100},
                    {"session": end.isoformat(), "close": 120}]
        self.archive(members=("AAA",))
        self.prices("SPY", provider=provider)
        cache_rows(self.conn, "AAA", [{"session": "2026-09-14", "open": 100},
                                      {"session": "2026-09-15", "close": 110}],
                   requested_start="2026-09-11", requested_end="2026-09-15",
                   provider=_provider_identity(provider), acquired_at="2026-09-15T18:00:00Z")
        self.conn.commit()
        row = self.report(as_of=None, fetch=True, provider=provider)["observations"][0]
        self.assertEqual(calls, ["AAA"])
        self.assertAlmostEqual(row["raw_return"], .2)

    def test_missing_benchmark_retains_raw_but_withholds_relative_label(self):
        self.archive()
        self.prices("AAA", 0.30)
        self.prices("BBB", 0.02)
        report = self.report()
        self.assertEqual(report["summary"]["observed"], 0)
        self.assertEqual(report["summary"]["unavailable"], 2)
        self.assertEqual(report["summary"]["large_moves"], 0)
        row = report["observations"][0]
        self.assertIsNotNone(row["raw_return"])
        self.assertIsNone(row["excess_return"])

    def test_same_entry_reruns_use_last_archive_and_receipts_point_to_final_winner(self):
        self.archive("run-a", signals=[self.signal()])
        self.archive("run-b", "2026-09-14T12:10:00Z")
        self.archive("run-c", "2026-09-14T12:20:00Z")
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["selected_runs"], 1)
        self.assertEqual(report["summary"]["expected_observations"], 2)
        self.assertEqual(report["selected_runs"][0]["run_id"], "run-c")
        self.assertEqual({row["selected_run_id"] for row in report["excluded_runs"]}, {"run-c"})
        self.assertEqual(report["summary"]["captured_large_moves"], 0)

    def test_after_open_run_cannot_replace_same_days_preopen_snapshot(self):
        self.archive("before-open", "2026-09-14T13:29:59Z", members=("AAA",))
        self.archive("at-open", "2026-09-14T13:30:00Z", members=("AAA",), signals=[self.signal()])
        self.prices("AAA", 0.12)
        self.prices("SPY", 0.02)
        report = self.report()
        selected = {row["run_id"]: row["entry_session"] for row in report["selected_runs"]}
        self.assertEqual(selected, {"before-open": "2026-09-14", "at-open": "2026-09-15"})
        first = next(row for row in report["observations"] if row["run_id"] == "before-open")
        self.assertEqual(first["capture_class"], "no_recorded_signal")

    def test_declaration_between_reruns_does_not_duplicate_same_entry_session(self):
        self.archive("before-declaration", "2026-09-14T12:00:00Z")
        self.archive("after-declaration", "2026-09-14T12:20:00Z")
        policy = VerificationPolicy("between-runs", "2026-09-14T12:10:00Z", 0.05, 10, horizons=(1,))
        report = self.report(policy=policy)
        self.assertEqual(report["summary"]["selected_runs"], 1)
        self.assertEqual(report["selected_runs"][0]["run_id"], "after-declaration")
        self.assertEqual(report["selected_runs"][0]["policy_timing"], "predeclared_policy")

    def test_latency_can_move_entry_to_next_session(self):
        self.archive(generated="2026-09-14T13:29:59Z", members=("AAA",))
        policy = VerificationPolicy("latency", self.policy.declared_at, 0.05, 10,
                                    horizons=(1,), entry_latency_seconds=1)
        report = self.report(policy=policy)
        self.assertEqual(report["selected_runs"][0]["entry_session"], "2026-09-15")

    def test_era_and_archived_cohort_never_pool_accuracy(self):
        self.archive("old", era="2.6.4", members=("AAA",))
        self.archive("new", era="2.6.5", members=("AAA",))
        self.archive("different-members", era="2.6.5", members=("AAA", "BBB"))
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["n_segments"], 3)
        self.assertFalse(report["summary"]["accuracy_rates_pooled"])
        self.assertNotIn("directional_hit_rate_observed", report["summary"])

    def test_directionless_capture_is_not_directional_accuracy(self):
        self.archive(signals=[self.signal(direction="none")])
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["captured_large_moves"], 1)
        self.assertEqual(report["summary"]["direction_matched_large_moves"], 0)
        self.assertEqual(report["summary"]["directional_observations"], 0)
        self.assertEqual(report["observations"][0]["capture_class"], "directionless_only")
        self.assertIsNone(report["segments"][0]["horizons"]["1"]["directional_hit_rate_observed"])

    def test_conflicting_directions_abstain_instead_of_counting_one_lucky_prediction(self):
        self.archive(signals=[self.signal(direction="long"), self.signal(direction="short")])
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["directional_observations"], 0)
        self.assertEqual(report["summary"]["conflicting_direction_observations"], 1)
        self.assertEqual(report["observations"][0]["capture_class"], "conflicting_directions")

    def test_repeated_and_multiple_signals_do_not_multiply_ticker_sample(self):
        first = self.signal(record_id="stable")
        self.archive("older", "2026-09-11T12:00:00Z", members=("AAA",), signals=[first])
        self.archive("latest", members=("AAA",), signals=[first, self.signal(record_id="new")])
        self.prices("AAA", 0.12)
        self.prices("SPY", 0.02)
        report = self.report()
        row = next(row for row in report["observations"] if row["run_id"] == "latest")
        self.assertEqual(row["signal"]["signal_count"], 2)
        self.assertEqual(row["signal"]["first_emission_count"], 1)
        self.assertEqual(row["signal"]["repeated_sighting_count"], 1)
        self.assertEqual(report["summary"]["directional_observations"], 1)

    def test_future_signal_and_future_archive_are_excluded(self):
        self.archive(signals=[self.signal(as_of="2026-09-14T12:00:01Z")])
        self.archive("future", "2026-09-21T12:00:00Z", signals=[self.signal()])
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["selected_runs"], 1)
        self.assertEqual(report["summary"]["rejected_signal_occurrences"], 1)
        self.assertEqual(report["summary"]["captured_large_moves"], 0)
        self.assertIn({"run_id": "future", "reason": "archive_after_cutoff"}, report["excluded_runs"])

    def test_unmatured_horizon_is_pending_even_if_future_prices_are_cached(self):
        self.archive()
        self.complete()
        report = self.report(as_of="2026-09-15T19:59:59Z")
        self.assertEqual(report["summary"]["pending"], 2)
        self.assertEqual(report["summary"]["matured_expected"], 0)
        self.assertEqual(report["summary"]["large_moves"], 0)
        self.assertTrue(all(row["price"] is None for row in report["observations"]))

    def test_acquisition_after_explicit_cutoff_is_unavailable(self):
        self.archive(members=("AAA",))
        self.prices("AAA", 0.12, acquired="2026-09-19T00:00:00Z")
        self.prices("SPY", 0.02)
        report = self.report()
        self.assertEqual(report["summary"]["unavailable"], 1)
        self.assertEqual(report["observations"][0]["price"]["status"], "price_acquired_after_cutoff")

    def test_non_equity_proxies_remain_visible_but_do_not_become_missed_trades(self):
        self.archive(members=("AAA", "SI=F", "^VIX", "STTDF"))
        self.prices("AAA", 0.12)
        self.prices("STTDF", 0.15)
        self.prices("SPY", 0.02)
        report = self.report()
        self.assertEqual(report["summary"]["expected_observations"], 4)
        self.assertEqual(report["summary"]["unsupported_asset"], 2)
        self.assertEqual(report["summary"]["matured_expected"], 2)
        self.assertEqual(report["summary"]["large_moves"], 2)
        otc = next(row for row in report["observations"] if row["ticker"] == "STTDF")
        self.assertIn("fillability_not_established", otc["listing_identity_status"])

    def test_known_currency_mismatch_is_not_a_comparable_return(self):
        self.archive(members=("AAA",))
        self.prices("AAA", 0.12, currency="INR")
        self.prices("SPY", 0.02, currency="USD")
        report = self.report()
        self.assertEqual(report["observations"][0]["reason"], "currency_basis_mismatch")
        self.assertIsNone(report["observations"][0]["large_move"])

    def test_friction_is_exact_declared_linear_hurdle(self):
        self.archive(members=("AAA",), signals=[self.signal()])
        self.prices("AAA", 0.051)
        self.prices("SPY", 0)
        policy = VerificationPolicy("hurdle", self.policy.declared_at, 0.05, 20, horizons=(1,))
        report = self.report(policy=policy)
        row = report["observations"][0]
        self.assertAlmostEqual(row["signed_excess_after_friction"], 0.049)
        self.assertFalse(row["large_move"])
        self.assertEqual(report["summary"]["directional_hits_after_hurdle"], 1)

    def test_declared_after_archive_remains_retrospective_policy(self):
        self.archive()
        self.complete()
        policy = VerificationPolicy("late-policy", "2026-09-16T00:00:00Z", 0.05, 10, horizons=(1,))
        report = self.report(policy=policy)
        self.assertEqual(report["segments"][0]["policy_timing"], "retrospective_policy")
        self.assertIn("neither independently attests", report["definitions"]["policy_timing"])

    def test_health_errors_and_declared_fixtures_are_excluded(self):
        self.archive("error", changes={"health": {"status": "ERROR", "errors": []}})
        self.archive("nonempty-errors", changes={"health": {"status": "OK", "errors": ["failed"]}})
        self.archive("stages", changes={"health": {"status": "WARN", "errors": [],
                                                    "sources": {"pipeline_stages": {"failed": ["sec"]}}}})
        self.archive("demo", changes={"demo": True})
        self.archive("metadata-fixture", changes={"metadata": {"kind": "synthetic"}})
        self.archive("invalid-marker", changes={"synthetic": "false"})
        self.archive("unknown-health", changes={"health": {}})
        report = self.report()
        self.assertEqual(report["summary"]["selected_runs"], 0)
        reasons = report["summary"]["exclusion_reasons"]
        self.assertEqual(reasons["archive_failed_health"], 2)
        self.assertEqual(reasons["archive_failed_stage"], 1)
        self.assertEqual(reasons["synthetic_or_invalid_archive_marker"], 3)
        self.assertEqual(reasons["archive_health_unknown"], 1)

    def test_warn_without_hard_error_is_eligible_and_quality_is_retained(self):
        self.archive(changes={"health": {"status": "WARN", "errors": [], "warnings": ["source gap"]}})
        self.complete()
        report = self.report()
        self.assertEqual(report["summary"]["observed"], 2)
        quality = report["selected_runs"][0]["quality_evidence"]
        self.assertEqual(quality["health_status"], "WARN")
        self.assertEqual(quality["synthetic_status"], "not_declared")

    def test_old_database_without_quality_table_is_unknown_until_exact_reingest(self):
        archive = self.archive()
        self.complete()
        self.conn.execute("DROP TABLE run_evidence")
        self.conn.commit()
        report = self.report()
        self.assertEqual(report["summary"]["selected_runs"], 0)
        self.assertEqual(report["excluded_runs"][0]["reason"], "run_quality_not_recorded")
        # Opening the current schema alone must not invent metadata.
        migration = connect(self.db)
        self.assertEqual(migration.execute("SELECT COUNT(*) FROM run_evidence").fetchone()[0], 0)
        ingest_file(archive, migration)
        migration.close()
        self.assertEqual(self.report()["summary"]["observed"], 2)

    def test_legacy_filename_recovered_time_cannot_claim_original_observation(self):
        self.archive("2026-09-14_120000Z", "2026-09-14T12:00:00")
        report = self.report()
        self.assertEqual(report["excluded_runs"][0]["reason"], "archive_timestamp_not_established")

    def test_missing_or_tampered_cohort_is_refused(self):
        self.archive()
        self.conn.execute("UPDATE run_cohorts SET cohort_sha256='bad'")
        self.conn.commit()
        report = self.report()
        self.assertEqual(report["summary"]["selected_runs"], 0)
        self.assertEqual(report["excluded_runs"][0]["reason"], "cohort_digest_mismatch")

    def test_cache_only_report_does_not_write_any_ledger_table(self):
        self.archive()
        self.complete()
        before = "\n".join(self.conn.iterdump())
        self.report()
        after = "\n".join(self.conn.iterdump())
        self.assertEqual(before, after)

    def test_fetch_uses_one_larger_acquisition_for_all_mature_horizons(self):
        self.archive(members=("AAA",))
        calls = []

        def provider(ticker, start, end):
            calls.append((ticker, start, end))
            return [{"session": day, "open": 100, "close": 105}
                    for day, _, _ in synthetic_schedule(start, end)]

        policy = VerificationPolicy("all-horizons", self.policy.declared_at, 0.05, 10)
        report = self.report(policy=policy, as_of=None, fetch=True, provider=provider)
        self.assertEqual(report["summary"]["observed"], 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual({ticker for ticker, _, _ in calls}, {"AAA", "SPY"})
        self.assertTrue(all(end == date(2026, 10, 12) for _, _, end in calls))
        ids = {row["price"]["metadata"]["acquisition_id"] for row in report["observations"]}
        self.assertEqual(len(ids), 1)
        replay = self.report(policy=policy, as_of=NOW, fetch=False, provider=provider)
        self.assertEqual(replay["summary"]["observed"], 3)
        self.assertEqual(len(calls), 2)

    def test_policy_is_complete_frozen_hashable_and_rejects_ambiguous_values(self):
        restored = VerificationPolicy.from_dict(self.policy.to_dict())
        self.assertEqual(restored, self.policy)
        self.assertEqual(restored.sha256, self.policy.sha256)
        with self.assertRaises(FrozenInstanceError):
            restored.move_threshold = 0.9
        for change in ({"move_threshold": float("nan")}, {"round_trip_cost_bps": True},
                       {"horizons": [1, 1]}, {"horizons": [True]},
                       {"declared_at": "2026-09-01"}, {"benchmark": "^VIX"},
                       {"undeclared_field": 1}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    VerificationPolicy.from_dict({**self.policy.to_dict(), **change})

    def test_future_cutoff_and_policy_declaration_are_refused(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.report(as_of="2026-11-01T00:00:00Z")
        late = VerificationPolicy("late", "2026-09-19T00:00:00Z", 0.05, 10)
        with self.assertRaisesRegex(ValueError, "declaration"):
            self.report(policy=late)

    def test_holiday_and_early_close_follow_injected_exchange_sessions(self):
        self.archive(generated="2026-09-04T21:00:00Z", members=("AAA",))
        base = synthetic_schedule(date(2026, 9, 4), date(2026, 10, 10))
        schedule = [row for row in base if row[0] != "2026-09-07"]
        schedule = [(session, opened, closed.replace(hour=17) if session == "2026-09-09" else closed)
                    for session, opened, closed in schedule]
        self.prices("AAA", 0.12, entry="2026-09-08", exit_="2026-09-09", acquired="2026-09-09T17:01:00Z")
        self.prices("SPY", 0.02, entry="2026-09-08", exit_="2026-09-09", acquired="2026-09-09T17:01:00Z")
        report = self.report(as_of="2026-09-09T17:01:00Z", schedule_provider=lambda *_: schedule)
        self.assertEqual(report["observations"][0]["entry_session"], "2026-09-08")
        self.assertEqual(report["observations"][0]["status"], "observed")
        self.assertEqual(report["observations"][0]["exit_close_at"], "2026-09-09T17:00:00Z")

    def test_empty_ledger_has_zero_counts_and_no_fabricated_accuracy(self):
        report = self.report()
        self.assertEqual(report["summary"]["observed"], 0)
        self.assertIsNone(report["summary"]["outcome_coverage"])
        self.assertEqual(report["segments"], [])
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
