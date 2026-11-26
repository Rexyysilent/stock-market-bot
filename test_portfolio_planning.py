"""Synthetic economic and nonanticipativity checks; no market-performance test."""
from copy import deepcopy
import math
import unittest

from portfolio_lab.planning import (
    HOLD, MAX_SIMULATIONS, VERSION, compare_planners, evaluate_sequence,
    exhaustive_plan, hold_plan, mcts_plan, myopic_plan, random_shooting_plan,
    replay_sequence, synthetic_problem, validate_problem,
)


def problem(paths, *, probabilities=None, **changes):
    result = synthetic_problem()
    probabilities = probabilities or [1 / len(paths)] * len(paths)
    result["scenarios"] = [{"probability": p, "returns": path}
                           for p, path in zip(probabilities, paths)]
    result.update(changes)
    return result


class PortfolioPlanningTests(unittest.TestCase):
    def test_entry_and_terminal_liquidation_fund_costs_without_borrowing(self):
        example = problem([[0.0]], cost_bps=100.0)
        result = evaluate_sequence(example, [1.0])["scenario_results"][0]
        self.assertAlmostEqual(result["terminal_wealth"], (1 - .01) / (1 + .01))
        self.assertAlmostEqual(result["cost_per_initial_wealth"], 2 * .01 / 1.01)
        self.assertEqual(result["ending_risky_weight"], 0.0)
        example["liquidate_at_end"] = False
        result = evaluate_sequence(example, [1.0])["scenario_results"][0]
        self.assertAlmostEqual(result["terminal_wealth"], 1 / 1.01)
        self.assertEqual(result["ending_risky_weight"], 1.0)

    def test_sale_achieves_post_cost_target_weight(self):
        example = problem([[0.0]], initial_weight=1.0, cost_bps=100.0,
                          liquidate_at_end=False)
        result = evaluate_sequence(example, [0.5])["scenario_results"][0]
        amount = .5 / (1 - .5 * .01)
        self.assertAlmostEqual(result["terminal_wealth"], 1 - amount * .01)
        self.assertAlmostEqual(result["ending_risky_weight"], .5)

    def test_hold_allows_weight_drift_and_is_not_constant_weight_rebalancing(self):
        example = problem([[1.0, 0.0]], initial_weight=.5, cost_bps=100.0,
                          liquidate_at_end=False)
        held = hold_plan(example)["scenario_results"][0]
        rebalanced = evaluate_sequence(example, [HOLD, .5])["scenario_results"][0]
        self.assertAlmostEqual(held["terminal_wealth"], 1.5)
        self.assertAlmostEqual(held["ending_risky_weight"], 2 / 3)
        self.assertEqual(held["cost_per_initial_wealth"], 0)
        self.assertLess(rebalanced["terminal_wealth"], held["terminal_wealth"])
        self.assertAlmostEqual(rebalanced["ending_risky_weight"], .5)

    def test_unchanged_target_does_not_charge_ticket_or_turnover(self):
        example = problem([[0.0, 0.0]], initial_weight=.5, cost_bps=100.0,
                          liquidate_at_end=False)
        result = evaluate_sequence(example, [.5, .5])["scenario_results"][0]
        self.assertEqual(result["terminal_wealth"], 1)
        self.assertEqual(result["cost_per_initial_wealth"], 0)
        self.assertEqual(result["turnover_per_initial_wealth"], 0)

    def test_positive_costs_remove_tiny_cost_free_edge(self):
        example = problem([[.001]], cost_bps=100.0)
        self.assertEqual(exhaustive_plan(example)["first_action"], HOLD)
        self.assertEqual(mcts_plan(example, simulations=100, seed=2)["first_action"], HOLD)
        example["cost_bps"] = 0
        self.assertEqual(exhaustive_plan(example)["first_action"], 1.0)

    def test_mcts_and_random_search_match_exact_small_model(self):
        report = compare_planners(synthetic_problem(), simulations=512, seed=17)
        for name in ("mcts", "random_shooting"):
            with self.subTest(name=name):
                self.assertEqual(report["plans"][name]["actions"],
                                 report["plans"]["exhaustive"]["actions"])
                self.assertAlmostEqual(report["finite_model_log_growth_gap"][name], 0)
        self.assertGreater(report["finite_model_log_growth_gap"]["myopic"], 0)
        self.assertEqual(report["plans"]["exhaustive"]["evaluated_sequences"], 4 ** 3)
        self.assertFalse(report["plans"]["mcts"]["predictive_accuracy_established"])

    def test_myopic_is_exact_for_one_step_with_same_terminal_cost(self):
        example = problem([[.02], [-.03]], probabilities=[.8, .2], cost_bps=15)
        greedy, exact = myopic_plan(example), exhaustive_plan(example)
        self.assertEqual(greedy["actions"], exact["actions"])
        self.assertAlmostEqual(greedy["scenario_expected"]["log_growth"],
                               exact["scenario_expected"]["log_growth"])
        self.assertEqual(greedy["evaluated_sequences"], len(example["actions"]))

    def test_shared_sequence_cannot_collect_pathwise_hindsight_profit(self):
        paths = [[.5, -.5], [-.5, .5]]
        example = problem(paths, cost_bps=0, actions=[HOLD, 0.0, 1.0])
        shared = exhaustive_plan(example)
        self.assertAlmostEqual(shared["scenario_expected"]["log_growth"], 0)
        self.assertEqual(shared["actions"], [HOLD, HOLD])
        # A forbidden path-specific maximization would manufacture this gain.
        hindsight = sum(exhaustive_plan(problem([path], cost_bps=0,
                                               actions=[HOLD, 0.0, 1.0]))
                        ["scenario_expected"]["log_growth"] / 2 for path in paths)
        self.assertAlmostEqual(hindsight, math.log(1.5))
        self.assertGreater(hindsight, shared["scenario_expected"]["log_growth"])

    def test_realized_path_is_separate_and_cannot_rewrite_a_frozen_plan(self):
        example = problem([[.02, .02]], cost_bps=0)
        plan = mcts_plan(example, simulations=100, seed=11)
        copy = deepcopy(plan)
        favorable = replay_sequence(example, plan["actions"], [.1, .1])
        unfavorable = replay_sequence(example, plan["actions"], [-.1, -.1])
        self.assertGreater(favorable["net_return"], unfavorable["net_return"])
        self.assertEqual(plan, copy)
        self.assertEqual(plan, mcts_plan(example, simulations=100, seed=11))
        self.assertEqual(unfavorable["evaluation_kind"], "retrospective_supplied_path")
        self.assertFalse(unfavorable["point_in_time_availability_verified"])
        example["realized_returns"] = [.1, .1]
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            mcts_plan(example)

    def test_probabilities_are_used_and_not_treated_as_equal_samples(self):
        negative = problem([[.1], [-.1]], probabilities=[.1, .9], cost_bps=0)
        positive = problem([[.1], [-.1]], probabilities=[.9, .1], cost_bps=0)
        self.assertEqual(exhaustive_plan(negative)["first_action"], HOLD)
        self.assertEqual(exhaustive_plan(positive)["first_action"], 1.0)

    def test_reproducibility_identity_and_no_input_mutation(self):
        example = synthetic_problem()
        original = deepcopy(example)
        first = compare_planners(example, simulations=73, seed=42)
        second = compare_planners(example, simulations=73, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(example, original)
        changed = deepcopy(example)
        changed["cost_bps"] += 1
        self.assertNotEqual(first["problem_sha256"],
                            compare_planners(changed, simulations=73, seed=42)["problem_sha256"])

    def test_budget_one_evaluates_only_no_transaction_control(self):
        for planner in (mcts_plan, random_shooting_plan):
            plan = planner(synthetic_problem(), simulations=1, seed=0)
            self.assertEqual(plan["actions"], [HOLD] * 3)
            self.assertEqual(plan["evaluated_sequences"], 1)
            self.assertEqual(plan["simulations"], 1)
            self.assertFalse(plan["finite_model_optimum_proven"])

    def test_unknown_missing_and_invalid_schema_fields_fail(self):
        example = synthetic_problem()
        for key, value in (("schema_version", "unknown"), ("scenario_kind", "realized_prices"),
                           ("scenario_id", ""), ("liquidate_at_end", 1),
                           ("cost_bps", True), ("cost_bps", -1), ("cost_bps", 10000),
                           ("initial_weight", 1.01), ("actions", [HOLD, True]),
                           ("actions", [HOLD, .5, .5]), ("actions", [0, 1])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_problem({**example, key: value})
        del example["data_through"]
        with self.assertRaises(ValueError):
            validate_problem(example)

    def test_invalid_scenario_data_fail_instead_of_filling(self):
        for scenarios in ([], [{"probability": 1, "returns": [-1]}],
                          [{"probability": 1, "returns": [None]}],
                          [{"probability": 1, "returns": [float("nan")]}],
                          [{"probability": 0, "returns": [.1]}],
                          [{"probability": .3, "returns": [.1]}],
                          [{"probability": .5, "returns": [.1]},
                           {"probability": .5, "returns": [.1, .2]}]):
            with self.subTest(scenarios=scenarios), self.assertRaises(ValueError):
                validate_problem({**synthetic_problem(), "scenarios": scenarios})

    def test_future_training_or_late_forecast_is_rejected(self):
        example = synthetic_problem()
        for key, value in (("data_through", "2026-01-03T21:00:00Z"),
                           ("created_at", "2026-01-02T21:03:00Z"),
                           ("created_at", "2026-01-02T20:59:00Z"),
                           ("information_cutoff", "2026-01-02T21:00:00")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_problem({**example, key: value})

    def test_search_size_and_exhaustive_work_are_bounded(self):
        example = synthetic_problem()
        for kwargs in ({"simulations": 0}, {"simulations": True},
                       {"simulations": MAX_SIMULATIONS + 1}, {"seed": -1},
                       {"exploration": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                mcts_plan(example, **kwargs)
        with self.assertRaisesRegex(ValueError, "max_sequences"):
            exhaustive_plan(example, max_sequences=1)
        large = problem([[0.0] * 20] * 128)
        with self.assertRaisesRegex(ValueError, "scenario-step budget"):
            mcts_plan(large, simulations=MAX_SIMULATIONS)
        with self.assertRaisesRegex(ValueError, "max_sequences"):
            exhaustive_plan(large)

    def test_sequences_and_replay_require_full_horizon_and_valid_actions(self):
        example = synthetic_problem()
        for sequence in ([HOLD], [HOLD, HOLD, True], [HOLD, HOLD, .25]):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                evaluate_sequence(example, sequence)
        with self.assertRaises(ValueError):
            replay_sequence(example, [HOLD] * 3, [.1])

    def test_optional_exhaustive_comparison_is_explicit(self):
        result = compare_planners(synthetic_problem(), simulations=8, include_exhaustive=False)
        self.assertNotIn("exhaustive", result["plans"])
        self.assertNotIn("finite_model_log_growth_gap", result)
        self.assertEqual(result["schema_version"], VERSION)


if __name__ == "__main__":
    unittest.main()
