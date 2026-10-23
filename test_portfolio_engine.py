"""Synthetic financial-invariant tests, not strategy-performance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from portfolio_lab.engine import simulate


def fixture(prices=None, *, symbols=("ALFA",), initial_cash=1000.0):
    prices = prices or [(100.0, 100.0), (100.0, 100.0), (100.0, 100.0)]
    first = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = []
    for index, pair in enumerate(prices):
        opening = first + timedelta(days=index)
        rows.append({
            "session": opening.date().isoformat(), "open_at": opening.isoformat(),
            "close_at": (opening + timedelta(hours=6, minutes=30)).isoformat(),
            "prices": {symbol: {"open": pair[0], "close": pair[1]} for symbol in symbols},
        })
    study = {
        "schema_version": "portfolio-lab.study.v1", "study_id": "synthetic-invariant",
        "strategy_sha256": "a" * 64, "currency": "USD", "calendar": "XNYS",
        "assets": {symbol: {"kind": "equity"} for symbol in symbols},
        "universe_basis": "fixed_operator_selection", "universe_declared_at": "2026-01-01T00:00:00Z",
        "initial_cash": initial_cash, "benchmark_weights": {symbols[0]: 1.0},
        "cost_model": {"kind": "generic", "buy_bps": 0.0, "sell_bps": 0.0, "fixed_per_order": 0.0},
        "slippage_bps": 0.0, "annual_fixed_cost": 0.0, "benchmark_annual_fixed_cost": 0.0,
        "execution": {"latency_seconds": 60, "extra_delay_sessions": 0,
                      "max_decision_age_days": 35, "min_trade_value": 0.0},
        "limits": {"max_weight": 1.0, "max_gross": 1.0},
        "evaluation": {"holdout_start": rows[1]["session"] if len(rows) > 1 else rows[0]["session"],
                       "declared_at": "2026-01-01T00:00:00Z", "minimum_holdout_sessions": 2,
                       "minimum_paper_decisions": 2, "objective": "Synthetic accounting test only"},
        "contributions": [],
    }
    dataset = {
        "schema_version": "portfolio-lab.dataset.v1", "dataset_id": "synthetic-path",
        "synthetic": True, "currency": "USD", "calendar": "XNYS",
        "price_basis": "total_return_proxy", "adjustment_policy": "Synthetic coherent total-return units; dividends are already reinvested in ratios.",
        "acquired_at": "2026-12-31T23:00:00Z",
        "source": {"name": "synthetic", "url": "https://example.invalid/synthetic",
                   "rights_note": "Fabricated test data", "calendar_source": "Explicit synthetic session schedule"},
        "expected_sessions": [row["session"] for row in rows], "sessions": rows,
    }
    decision = {
        "decision_id": "d1", "strategy_sha256": study["strategy_sha256"],
        "decided_at": "2026-01-05T13:00:00Z", "information_cutoff": "2026-01-05T13:00:00Z",
        "weights": {symbols[0]: 1.0}, "origin": "retrospective", "evidence": [],
    }
    return study, dataset, [decision]


class PortfolioEngineTests(unittest.TestCase):
    def test_contribution_is_not_a_return(self):
        study, dataset, decisions = fixture()
        study["contributions"] = [{"session": "2026-01-06", "amount": 2000.0}]
        result = simulate(study, dataset, decisions)
        for account in ("strategy", "benchmark"):
            metrics = result[account]["metrics"]
            self.assertAlmostEqual(metrics["ending_value"], 3000.0)
            self.assertAlmostEqual(metrics["profit"], 0.0)
            self.assertAlmostEqual(metrics["total_return"], 0.0)
            self.assertAlmostEqual(metrics["max_drawdown"], 0.0)

    def test_flow_after_overnight_move_uses_correct_twr(self):
        study, dataset, decisions = fixture([(100, 100), (200, 200), (200, 200)])
        study["contributions"] = [{"session": "2026-01-06", "amount": 1000.0}]
        result = simulate(study, dataset, decisions)
        self.assertAlmostEqual(result["strategy"]["metrics"]["total_return"], 1.0)
        self.assertAlmostEqual(result["strategy"]["daily_path"][1]["daily_return"], 1.0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["profit"], 1000.0)

    def test_no_return_before_next_open_fill(self):
        study, dataset, decisions = fixture([(100, 200), (300, 330), (330, 330)])
        decisions[0]["decided_at"] = decisions[0]["information_cutoff"] = dataset["sessions"][0]["close_at"]
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["strategy"]["trades"][0]["session"], "2026-01-06")
        self.assertAlmostEqual(result["strategy"]["metrics"]["total_return"], 0.1)
        self.assertAlmostEqual(result["strategy"]["daily_path"][0]["daily_return"], 0.0)

    def test_open_strictly_after_eligibility(self):
        study, dataset, decisions = fixture()
        decisions[0]["decided_at"] = decisions[0]["information_cutoff"] = "2026-01-05T14:29:00Z"
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["decision_executions"][0]["effective_session"], "2026-01-06")

    def test_paper_recording_time_cannot_be_backdated_for_fill(self):
        study, dataset, decisions = fixture()
        decisions[0].update(origin="paper", recorded_at="2026-01-06T15:00:00Z")
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["decision_executions"][0]["effective_session"], "2026-01-07")

    def test_extra_delay_and_pending_decision(self):
        study, dataset, decisions = fixture()
        delayed = simulate(study, dataset, decisions, delay_sessions=1)
        self.assertEqual(delayed["decision_executions"][0]["effective_session"], "2026-01-06")
        pending = simulate(study, dataset, decisions, delay_sessions=3)
        self.assertEqual(pending["decision_executions"][0]["status"], "pending")
        self.assertEqual(pending["strategy"]["trades"], [])

    def test_stale_decision_is_explicitly_rejected(self):
        study, dataset, decisions = fixture()
        decisions[0]["decided_at"] = decisions[0]["information_cutoff"] = "2025-11-01T13:00:00Z"
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["decision_executions"][0]["status"], "rejected_stale")
        self.assertEqual(result["strategy"]["trades"], [])

    def test_conflicting_same_session_decisions_fail(self):
        study, dataset, decisions = fixture()
        duplicate_session = deepcopy(decisions[0])
        duplicate_session["decision_id"] = "d2"
        with self.assertRaisesRegex(ValueError, "same effective session"):
            simulate(study, dataset, decisions + [duplicate_session])

    def test_affordability_includes_fixed_proportional_and_slippage_costs(self):
        study, dataset, decisions = fixture()
        study["cost_model"].update(buy_bps=100.0, fixed_per_order=10.0)
        study["slippage_bps"] = 50.0
        result = simulate(study, dataset, decisions)
        trade = result["strategy"]["trades"][0]
        expected_notional = (1000.0 - 10.0) / 1.015
        self.assertAlmostEqual(trade["notional"], expected_notional)
        self.assertAlmostEqual(trade["notional"] + trade["total_cost"], 1000.0)
        self.assertGreaterEqual(result["strategy"]["daily_path"][0]["cash"], 0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["profit"], -trade["total_cost"])
        self.assertAlmostEqual(result["gross_strategy"]["metrics"]["profit"], 0)

    def test_sale_costs_do_not_create_negative_cash(self):
        study, dataset, decisions = fixture()
        study["cost_model"].update(buy_bps=10.0, sell_bps=20.0, fixed_per_order=2.0)
        sell = deepcopy(decisions[0])
        sell.update(decision_id="d2", decided_at="2026-01-05T22:00:00Z",
                    information_cutoff="2026-01-05T22:00:00Z", weights={})
        result = simulate(study, dataset, decisions + [sell])
        self.assertEqual([trade["side"] for trade in result["strategy"]["trades"]], ["BUY", "SELL"])
        final = result["strategy"]["daily_path"][-1]
        self.assertGreaterEqual(final["cash"], 0.0)
        self.assertAlmostEqual(final["holdings_value"], 0.0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["profit"], -result["strategy"]["metrics"]["costs"]["total"])

    def test_tiny_uneconomic_order_and_minimum_skip_are_visible(self):
        study, dataset, decisions = fixture(initial_cash=10.0)
        study["cost_model"]["fixed_per_order"] = 11.0
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["strategy"]["trades"], [])
        self.assertEqual(result["strategy"]["skipped_trades"][0]["reason"], "uneconomic_order_cost")
        study["cost_model"]["fixed_per_order"] = 0.0
        study["execution"]["min_trade_value"] = 11.0
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["strategy"]["skipped_trades"][0]["reason"], "below_minimum_trade")

    def test_future_data_does_not_change_prior_accounting(self):
        study, dataset, decisions = fixture()
        baseline = simulate(study, dataset, decisions)
        extended = deepcopy(dataset)
        last = deepcopy(extended["sessions"][-1])
        last.update(session="2026-01-08", open_at="2026-01-08T14:30:00Z", close_at="2026-01-08T21:00:00Z")
        last["prices"]["ALFA"] = {"open": 900.0, "close": 1.0}
        extended["sessions"].append(last)
        extended["expected_sessions"].append(last["session"])
        updated = simulate(study, extended, decisions)
        self.assertEqual(baseline["strategy"]["daily_path"], updated["strategy"]["daily_path"][:3])
        self.assertEqual(baseline["strategy"]["trades"], updated["strategy"]["trades"])

    def test_current_close_cannot_change_already_executed_open_order(self):
        study, dataset, decisions = fixture()
        baseline = simulate(study, dataset, decisions)
        dataset["sessions"][0]["prices"]["ALFA"]["close"] = 500.0
        updated = simulate(study, dataset, decisions)
        self.assertEqual(baseline["strategy"]["trades"][0], updated["strategy"]["trades"][0])

    def test_full_path_drawdown_is_not_replaced_by_endpoints(self):
        study, dataset, decisions = fixture([(100, 100), (50, 50), (100, 100)])
        result = simulate(study, dataset, decisions)
        self.assertAlmostEqual(result["strategy"]["metrics"]["total_return"], 0.0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["max_drawdown"], -0.5)
        self.assertAlmostEqual(result["evaluation"]["partitions"]["holdout"]["strategy"]["max_drawdown"], -0.5)

    def test_holdout_return_excludes_development_gain(self):
        study, dataset, decisions = fixture([(100, 200), (200, 180), (180, 180)])
        result = simulate(study, dataset, decisions)
        self.assertAlmostEqual(result["strategy"]["metrics"]["total_return"], 0.8)
        partitions = result["evaluation"]["partitions"]
        self.assertAlmostEqual(partitions["development"]["strategy"]["total_return"], 1.0)
        self.assertAlmostEqual(partitions["holdout"]["strategy"]["total_return"], -0.1)
        self.assertFalse(result["evaluation"]["statistical_or_investment_approval"])
        self.assertEqual(result["evaluation"]["status"], "synthetic_accounting_demonstration")

    def test_overhead_uses_calendar_time_including_weekend(self):
        study, dataset, decisions = fixture(prices=[(100, 100), (100, 100)])
        for index, day in enumerate(("2026-01-09", "2026-01-12")):
            dataset["sessions"][index].update(session=day, open_at=day + "T14:30:00Z", close_at=day + "T21:00:00Z")
        dataset["expected_sessions"] = ["2026-01-09", "2026-01-12"]
        study["annual_fixed_cost"] = 365.25
        result = simulate(study, dataset, decisions)
        expected = 3 + 6.5 / 24.0
        self.assertAlmostEqual(result["strategy"]["metrics"]["costs"]["overhead"], expected)
        self.assertAlmostEqual(result["strategy"]["metrics"]["profit"], -expected)
        self.assertAlmostEqual(result["gross_strategy"]["metrics"]["costs"]["total"], 0.0)

    def test_fixed_cost_depletion_fails_without_borrowing(self):
        study, dataset, decisions = fixture(initial_cash=1.0)
        study["annual_fixed_cost"] = 1e6
        with self.assertRaisesRegex(ValueError, "deplete"):
            simulate(study, dataset, decisions)

    def test_late_dataset_cannot_retroactively_make_holdout_declaration_prior(self):
        study, dataset, decisions = fixture()
        dataset["synthetic"] = False  # Does not certify these fabricated values.
        dataset["acquired_at"] = "2026-01-08T00:00:00Z"
        study["evaluation"].update(holdout_start="2025-01-01", declared_at="2026-01-01T00:00:00Z")
        result = simulate(study, dataset, decisions)
        self.assertFalse(result["evaluation"]["declared_before_holdout"])
        self.assertEqual(result["evaluation"]["status"], "holdout_declaration_not_prior_to_observed_period")

    def test_retrospective_development_position_does_not_become_paper_holdout(self):
        study, dataset, decisions = fixture()
        dataset["synthetic"] = False
        dataset["acquired_at"] = "2026-01-08T00:00:00Z"
        result = simulate(study, dataset, decisions)
        self.assertEqual(result["evaluation"]["holdout_retrospective_decisions"], 0)
        self.assertEqual(result["evaluation"]["status"], "retrospective_reconstruction_not_independent_validation")

    def test_benchmark_is_buy_hold_without_daily_rebalance(self):
        study, dataset, decisions = fixture(symbols=("ALFA", "BETA"))
        study["benchmark_weights"] = {"ALFA": 0.5, "BETA": 0.5}
        dataset["sessions"][1]["prices"]["ALFA"] = {"open": 200.0, "close": 200.0}
        dataset["sessions"][2]["prices"]["ALFA"] = {"open": 200.0, "close": 200.0}
        result = simulate(study, dataset, decisions)
        self.assertEqual(len(result["benchmark"]["trades"]), 2)
        self.assertAlmostEqual(result["benchmark"]["metrics"]["ending_value"], 1500.0)

    def test_cash_benchmark_and_no_decisions_are_supported(self):
        study, dataset, _ = fixture()
        study["benchmark_weights"] = {}
        result = simulate(study, dataset, [])
        self.assertEqual(result["benchmark"]["trades"], [])
        self.assertEqual(result["strategy"]["trades"], [])
        self.assertEqual(result["strategy"]["metrics"]["total_return"], 0.0)
        self.assertIsNone(result["strategy"]["metrics"]["annualized_return"])

    def test_invalid_prices_and_missing_path_fail(self):
        study, dataset, decisions = fixture()
        dataset["sessions"][1]["prices"]["ALFA"]["open"] = float("nan")
        with self.assertRaises(ValueError):
            simulate(study, dataset, decisions)
        study, dataset, decisions = fixture()
        dataset["sessions"].pop(1)
        with self.assertRaises(ValueError):
            simulate(study, dataset, decisions)

    def test_proxy_split_and_dividend_not_double_counted(self):
        study, dataset, decisions = fixture([(100, 100), (100, 100), (100, 101)])
        # A raw split has already been removed from this coherent return proxy.
        result = simulate(study, dataset, decisions)
        self.assertAlmostEqual(result["strategy"]["daily_path"][1]["closing_value"], 1000.0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["ending_value"], 1010.0)
        self.assertAlmostEqual(result["strategy"]["metrics"]["total_return"], 0.01)

    def test_inputs_unchanged_and_output_deterministic(self):
        inputs = fixture()
        saved = deepcopy(inputs)
        first = simulate(*inputs)
        self.assertEqual(inputs, saved)
        self.assertEqual(first, simulate(*inputs))

    def test_cost_stress_reduces_flat_price_wealth(self):
        study, dataset, decisions = fixture()
        study["cost_model"].update(buy_bps=25.0, fixed_per_order=1.0)
        study["slippage_bps"] = 10.0
        study["annual_fixed_cost"] = 100.0
        normal = simulate(study, dataset, decisions)
        stressed = simulate(study, dataset, decisions, cost_multiplier=2.0)
        self.assertLess(stressed["strategy"]["metrics"]["ending_value"], normal["strategy"]["metrics"]["ending_value"])


if __name__ == "__main__":
    unittest.main()
