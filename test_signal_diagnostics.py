"""Descriptive family diagnostics keep coverage and overlapping events visible."""
from copy import deepcopy
import json
import math
import unittest

from ledger.diagnostics import build_family_diagnostics


def observation(ticker, *, families=(), status="observed", excess=.1, large=True,
                entry="2026-09-02", count=None, **changes):
    return {
        "run_id": "archive-" + entry, "ticker": ticker, "entry_session": entry,
        "pipeline_version": "2.6.6", "cohort_sha256": "cohort-a",
        "policy_timing": "predeclared_policy", "horizon": 1, "status": status,
        "excess_return": excess if status == "observed" else None,
        "large_move": large if status == "observed" else None,
        "signal": {"signal_count": len(families) if count is None else count,
                   "families": list(families), "direction": "long",
                   "direction_conflict": False},
        **changes,
    }


def report(rows):
    return {"schema_version": "signal-verification-report-1", "observations": rows,
            "as_of": "2026-10-01T00:00:00Z", "policy_sha256": "declared-policy"}


class SignalDiagnosticsTests(unittest.TestCase):
    def example(self):
        return report([
            observation("AAA", families=("technical", "insider"), count=3),
            observation("BBB", excess=-.08),
            observation("CCC", families=("technical",), excess=.02, large=False),
            observation("DDD", families=("technical",), status="unavailable"),
            observation("EEE", families=("insider",), status="pending"),
            observation("^INDEX", families=("technical",), status="unsupported_asset"),
            observation("FFF", excess=.01, large=False, entry="2026-09-03"),
        ])

    def test_complementary_cohorts_retain_missing_pending_and_unsupported_rows(self):
        result = build_family_diagnostics(self.example())
        group = result["groups"][0]
        population = group["population"]
        self.assertEqual(result["observation_count"], 7)
        self.assertEqual([population[key] for key in (
            "expected_observations", "supported_observations", "matured_expected",
            "observed", "unavailable", "pending", "unsupported_asset")], [7, 6, 5, 4, 1, 1, 1])
        absent, present = (group["cohorts"][name] for name in ("no_recorded_signal", "any_recorded_signal"))
        self.assertEqual(absent["expected_observations"] + present["expected_observations"], 7)
        self.assertEqual(present["expected_observations"], 5)
        self.assertEqual(present["matured_expected"], 3)
        self.assertEqual(present["observed"], 2)
        self.assertAlmostEqual(present["outcome_coverage"], 2 / 3)
        self.assertAlmostEqual(present["mean_absolute_excess_return_observed"], .06)
        self.assertAlmostEqual(absent["mean_absolute_excess_return_observed"], .045)
        self.assertEqual(population["distinct_entry_sessions"], 2)

    def test_yield_and_event_capture_have_different_explicit_denominators(self):
        group = build_family_diagnostics(self.example())["groups"][0]
        insider = group["families"]["insider"]
        self.assertEqual(insider["large_moves"], 1)
        self.assertEqual(insider["observed"], 1)
        self.assertEqual(insider["large_move_yield_observed"], 1)
        self.assertEqual(insider["group_observed_large_moves"], 2)
        self.assertEqual(insider["event_capture_rate_observed"], .5)
        absent = group["cohorts"]["no_recorded_signal"]
        self.assertEqual(absent["large_move_share_observed"], .5)
        self.assertEqual(absent["captured_large_moves"], 0)
        self.assertEqual(absent["event_capture_rate_observed"], 0)

    def test_shared_event_and_repeated_family_records_are_not_independent_hits(self):
        group = build_family_diagnostics(self.example())["groups"][0]
        self.assertEqual(group["multi_family_observations"], 1)
        self.assertEqual(group["family_observation_memberships"], 6)
        self.assertEqual(group["families"]["technical"]["expected_observations"], 4)
        self.assertEqual(sum(item["captured_large_moves"] for item in group["families"].values()), 2)
        self.assertEqual(group["cohorts"]["any_recorded_signal"]["captured_large_moves"], 1)
        self.assertEqual(group["population"]["captured_large_moves"], 1)

    def test_combined_direction_and_directional_returns_are_never_attributed(self):
        original = self.example()
        changed = deepcopy(original)
        for row in changed["observations"]:
            row["signal"].update(direction="short", direction_conflict=True,
                                 repeated_sighting_count=500, first_emission_count=0)
            row["signed_excess_after_friction"] = -123.0
            row["capture_class"] = "opposite_direction_only"
        self.assertEqual(build_family_diagnostics(original), build_family_diagnostics(changed))
        self.assertFalse(build_family_diagnostics(original)["directional_attribution"])

    def test_eras_cohorts_policy_timing_and_horizons_are_never_pooled(self):
        rows = [observation("AAA", families=("technical",))]
        for change in ({"pipeline_version": "2.6.5"}, {"cohort_sha256": "cohort-b"},
                       {"policy_timing": "retrospective_policy"}, {"horizon": 5}):
            rows.append(observation("AAA", families=("insider",), **change))
        result = build_family_diagnostics(report(rows))
        self.assertEqual(result["group_count"], 5)
        self.assertFalse(result["rates_pooled"])
        self.assertNotIn("event_capture_rate_observed", result)
        self.assertTrue(all(group["population"]["observed"] == 1 for group in result["groups"]))
        self.assertTrue(all(len(group["families"]) == 1 for group in result["groups"]))

    def test_missing_outcomes_cannot_dilute_a_mean_or_become_confirmed_non_events(self):
        rows = [observation("A", families=("technical",), excess=.2)]
        rows.extend(observation(ticker, families=("technical",), status="unavailable")
                    for ticker in ("B", "C", "D", "E"))
        group = build_family_diagnostics(report(rows))["groups"][0]
        family = group["families"]["technical"]
        self.assertEqual(family["large_move_yield_observed"], 1)
        self.assertEqual(family["outcome_coverage"], .2)
        self.assertEqual(family["mean_absolute_excess_return_observed"], .2)
        self.assertEqual(family["unavailable"], 4)

    def test_no_observed_outcomes_have_null_yield_capture_and_average(self):
        group = build_family_diagnostics(report([
            observation("A", families=("technical",), status="pending"),
            observation("B", families=("technical",), status="unavailable"),
        ]))["groups"][0]
        family = group["families"]["technical"]
        for key in ("large_move_yield_observed", "large_move_share_observed",
                    "event_capture_rate_observed", "mean_absolute_excess_return_observed"):
            self.assertIsNone(family[key])
        self.assertEqual(family["outcome_coverage"], 0)
        self.assertEqual(family["expected_entry_sessions"], 1)
        self.assertEqual(family["distinct_entry_sessions"], 0)

    def test_absent_families_and_empty_reports_do_not_get_fabricated_denominators(self):
        result = build_family_diagnostics(report([observation("A", large=False)]))
        self.assertEqual(result["groups"][0]["families"], {})
        present = result["groups"][0]["cohorts"]["any_recorded_signal"]
        self.assertEqual(present["expected_observations"], 0)
        self.assertIsNone(present["large_move_yield_observed"])
        self.assertIsNone(present["event_capture_rate_observed"])
        empty = build_family_diagnostics(report([]))
        self.assertEqual(empty["observation_count"], 0)
        self.assertEqual(empty["groups"], [])

    def test_every_input_row_is_retained_and_result_is_deterministic_json(self):
        source = self.example()
        before = deepcopy(source)
        first = build_family_diagnostics(source)
        self.assertEqual(source, before)
        self.assertEqual(first, build_family_diagnostics(source))
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        self.assertEqual(sum(group["population"]["expected_observations"] for group in first["groups"]),
                         len(source["observations"]))

    def test_real_verifier_shape_retains_an_unpriced_no_signal_member(self):
        # Reuse local synthetic ingestion and prices; no provider request occurs.
        from test_signal_verification import VerificationTests
        fixture = VerificationTests()
        fixture.setUp()
        try:
            fixture.archive(signals=[fixture.signal()])
            fixture.prices("AAA", .12)
            fixture.prices("SPY", .02)
            source = fixture.report()
            before = deepcopy(source)
            result = build_family_diagnostics(source)
        finally:
            fixture.tearDown()
        self.assertEqual(source, before)
        self.assertEqual(result["observation_count"], 2)
        group = result["groups"][0]
        self.assertEqual(group["cohorts"]["no_recorded_signal"]["unavailable"], 1)
        self.assertEqual(group["cohorts"]["any_recorded_signal"]["observed"], 1)
        self.assertEqual(group["population"]["outcome_coverage"], .5)

    def test_duplicate_entry_units_are_refused_instead_of_inflating_sample_size(self):
        rows = [observation("A"), observation("A", run_id="a-different-rerun")]
        with self.assertRaisesRegex(ValueError, "duplicate ticker/entry"):
            build_family_diagnostics(report(rows))

    def test_invalid_or_inconsistent_outcomes_are_not_silently_counted(self):
        for changes in ({"status": "failed"}, {"excess_return": math.nan},
                        {"large_move": 1}, {"horizon": True},
                        {"status": "unavailable", "excess_return": .1, "large_move": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_family_diagnostics(report([observation("A", **changes)]))
        bad = observation("A")
        bad["signal"]["families"] = ["technical"]
        with self.assertRaisesRegex(ValueError, "inconsistent signal"):
            build_family_diagnostics(report([bad]))


if __name__ == "__main__":
    unittest.main()
