"""Offline arithmetic and failure-boundary tests; no live broker checks."""

import copy
import json
import math
import unittest

from portfolio_lab.costs import trade_cost, validate_cost_model
from portfolio_lab.economics import income_scenarios


def india(broker="zerodha", dp=15.34):
    return {"kind": "india_delivery", "broker": broker, "as_of": "2026-10-23", "dp_sell_fee": dp}


class PortfolioCostTests(unittest.TestCase):
    def test_generic_direction_and_fixed_ticket(self):
        model = {"kind": "generic", "buy_bps": 3.0, "sell_bps": 7.0, "fixed_per_order": 2.0}
        self.assertAlmostEqual(trade_cost(model, "equity", "BUY", 10_000)["total"], 5.0)
        self.assertAlmostEqual(trade_cost(model, "equity_etf", "sell", 10_000)["total"], 9.0)

    def test_share_versus_fund_stt_and_stamp(self):
        # Hand-computed INR 100,000 delivery: 3.07 exchange/IPFT + 0.10 SEBI,
        # GST 0.5706, plus side/instrument-specific taxes and the explicit DP.
        share_buy = trade_cost(india(), "equity", "BUY", 100_000)
        fund_buy = trade_cost(india(), "equity_etf", "BUY", 100_000)
        fund_sell = trade_cost(india(), "equity_etf", "SELL", 100_000)
        other_sell = trade_cost(india(), "non_equity_etf", "SELL", 100_000)
        self.assertAlmostEqual(share_buy["total"], 118.7406)
        self.assertAlmostEqual(fund_buy["total"], 18.7406)
        self.assertAlmostEqual(fund_sell["total"], 20.0806)
        self.assertAlmostEqual(other_sell["total"], 19.0806)
        self.assertEqual(fund_buy["stt"], 0)
        self.assertEqual(fund_sell["stt"], 1)
        self.assertEqual(fund_sell["stamp_duty"], 0)

    def test_dp_is_already_tax_inclusive_and_user_supplied(self):
        low = trade_cost(india(dp=0), "equity", "SELL", 10_000)
        high = trade_cost(india(dp=50), "equity", "SELL", 10_000)
        self.assertAlmostEqual(high["total"] - low["total"], 50)
        self.assertEqual(high["gst"], low["gst"])
        self.assertEqual(trade_cost(india(dp=50), "equity", "BUY", 10_000)["dp_fee_including_gst"], 0)

    def test_broker_ticket_gst_and_no_double_ipft(self):
        free = trade_cost(india("zerodha"), "equity_etf", "BUY", 100_000)
        paid = trade_cost(india("upstox"), "equity_etf", "BUY", 100_000)
        self.assertAlmostEqual(paid["total"] - free["total"], 23.6)
        self.assertAlmostEqual(paid["transaction_and_ipft"], 3.07)
        self.assertAlmostEqual(paid["total"], 42.3406)

    def test_zero_trades_and_zero_sensitivity_have_no_fees(self):
        for kind in ("equity", "equity_etf", "non_equity_etf"):
            for side in ("BUY", "SELL"):
                with self.subTest(kind=kind, side=side):
                    self.assertTrue(all(v == 0 for v in trade_cost(india("upstox"), kind, side, 0).values()))
                    self.assertTrue(all(v == 0 for v in trade_cost(india("upstox"), kind, side, 1000, multiplier=0).values()))

    def test_sensitivity_scales_every_component(self):
        normal = trade_cost(india("upstox"), "equity", "SELL", 100_000)
        stressed = trade_cost(india("upstox"), "equity", "SELL", 100_000, multiplier=2.5)
        for key in normal:
            self.assertAlmostEqual(stressed[key], 2.5 * normal[key])
        self.assertAlmostEqual(normal["total"], math.fsum(v for k, v in normal.items() if k != "total"))

    def test_fixed_cost_can_exceed_tiny_ticket_without_negative_fee(self):
        cost = trade_cost(india("upstox"), "non_equity_etf", "SELL", 1)
        self.assertGreater(cost["total"], 1)
        self.assertTrue(all(v >= 0 for v in cost.values()))

    def test_model_validation_rejects_currency_date_and_unknown_fields(self):
        bad_models = [
            {**india(), "as_of": "2024-01-01"},
            {**india(), "as_of": "2027-01-01"},
            {**india(), "broker": "unknown"},
            {k: v for k, v in india().items() if k != "dp_sell_fee"},
            {**india(), "dp_includes_gst": False},
            {"kind": "generic", "buy_bps": 1, "sell_bps": 1},
            {"kind": "unknown"},
        ]
        for model in bad_models:
            with self.subTest(model=model), self.assertRaises(ValueError):
                validate_cost_model(model, "INR")
        with self.assertRaises(ValueError):
            validate_cost_model(india(), "USD")
        with self.assertRaises(ValueError):
            validate_cost_model(india(), "EUR")

    def test_invalid_numerics_fail_even_on_zero_trades(self):
        invalid = [True, False, "0", None, -1, float("nan"), float("inf"), -float("inf")]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    trade_cost(india(), "equity", "BUY", value)
                with self.assertRaises(ValueError):
                    trade_cost(india(), "equity", "BUY", 0, multiplier=value)
                with self.assertRaises(ValueError):
                    validate_cost_model({**india(), "dp_sell_fee": value}, "INR")

    def test_asset_and_side_are_not_silently_coerced(self):
        for asset in ("option", "fund", [], None):
            with self.subTest(asset=asset), self.assertRaises(ValueError):
                trade_cost(india(), asset, "BUY", 1)
        for side in ("SHORT", " BUY", [], 1):
            with self.subTest(side=side), self.assertRaises(ValueError):
                trade_cost(india(), "equity", side, 1)

    def test_no_mutation_and_finite_json(self):
        model = india("upstox")
        original = copy.deepcopy(model)
        result = trade_cost(model, "equity", "SELL", 100_000)
        self.assertEqual(model, original)
        json.dumps(result, allow_nan=False)

    def test_derived_overflow_rejected(self):
        model = {"kind": "generic", "buy_bps": 1e308, "sell_bps": 0, "fixed_per_order": 0}
        with self.assertRaises(ValueError):
            trade_cost(model, "equity", "BUY", 1e308)


class PortfolioEconomicsTests(unittest.TestCase):
    def test_income_and_capital_growth_are_separate(self):
        report = income_scenarios(100_000, [0.06], annual_fixed_cost=1200, monthly_income_goal=300, currency="INR")
        row = report["scenarios"][0]
        self.assertEqual(row["gross_annual_change"], 6000)
        self.assertEqual(row["net_annual_change_after_fixed_cost"], 4800)
        self.assertEqual(row["monthly_equivalent_after_fixed_cost"], 400)
        self.assertEqual(row["ending_capital_if_reinvested"], 104_800)
        self.assertEqual(row["ending_capital_after_goal_withdrawal"], 101_200)
        self.assertEqual(row["required_capital_for_goal"], 80_000)
        self.assertEqual(report["fixed_cost_break_even_annual_rate"], 0.012)
        self.assertTrue(row["goal_covered_by_assumed_net_change"])

    def test_losses_and_zero_have_no_infinite_required_capital(self):
        report = income_scenarios(1000, [-0.2, 0], annual_fixed_cost=100, monthly_income_goal=20)
        losing, zero = report["scenarios"]
        self.assertEqual(losing["net_annual_change_after_fixed_cost"], -300)
        self.assertEqual(losing["monthly_equivalent_after_fixed_cost"], -25)
        self.assertEqual(losing["ending_capital_after_goal_withdrawal"], 460)
        for row in (losing, zero):
            self.assertIsNone(row["required_capital_for_goal"])
            self.assertEqual(row["required_capital_reason"], "nonpositive_assumed_rate")
            self.assertFalse(row["goal_covered_by_assumed_net_change"])
        json.dumps(report, allow_nan=False)

    def test_withdrawal_shortfall_is_visible_not_floor_clipped(self):
        row = income_scenarios(100, [-1], annual_fixed_cost=10, monthly_income_goal=5)["scenarios"][0]
        self.assertEqual(row["ending_capital_if_reinvested"], -10)
        self.assertEqual(row["ending_capital_after_goal_withdrawal"], -70)
        self.assertTrue(row["reinvestment_case_has_funding_shortfall"])
        self.assertTrue(row["withdrawal_case_has_funding_shortfall"])

    def test_required_capital_solves_supplied_equation(self):
        report = income_scenarios(50_000, [0.01, 0.04, 0.1], annual_fixed_cost=6000, monthly_income_goal=2000)
        for row in report["scenarios"]:
            self.assertAlmostEqual(row["required_capital_for_goal"] * row["annual_return_assumption"] - 6000, 24_000)

    def test_goal_does_not_change_the_assumed_return(self):
        low = income_scenarios(1000, [0.05], monthly_income_goal=0)["scenarios"][0]
        high = income_scenarios(1000, [0.05], monthly_income_goal=100)["scenarios"][0]
        self.assertEqual(low["net_annual_change_after_fixed_cost"], high["net_annual_change_after_fixed_cost"])
        self.assertEqual(low["ending_capital_if_reinvested"], high["ending_capital_if_reinvested"])

    def test_invalid_inputs_and_derived_overflow(self):
        cases = [
            (0, [0.05], {}), (-1, [0.05], {}), (True, [0.05], {}),
            (100, [], {}), (100, [float("nan")], {}), (100, [float("inf")], {}),
            (100, [-1.01], {}), (100, [True], {}), (100, "0.05", {}),
            (100, [0.05], {"annual_fixed_cost": -1}),
            (100, [0.05], {"monthly_income_goal": -1}),
            (100, [0.05], {"currency": "EUR"}),
            (1e308, [1e308], {}),
            (100, [0.05], {"monthly_income_goal": 1e308}),
            (100, [5e-324], {"monthly_income_goal": 100}),
        ]
        for capital, rates, kwargs in cases:
            with self.subTest(capital=capital, rates=rates, kwargs=kwargs), self.assertRaises(ValueError):
                income_scenarios(capital, rates, **kwargs)

    def test_generator_bounded_and_input_unmodified(self):
        rates = [0.02, 0.05]
        self.assertEqual(len(income_scenarios(1000, iter(rates))["scenarios"]), 2)
        self.assertEqual(rates, [0.02, 0.05])
        with self.assertRaises(ValueError):
            income_scenarios(1000, (0.01 for _ in range(1001)))


if __name__ == "__main__":
    unittest.main()
