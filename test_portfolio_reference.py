"""Synthetic checks of reference timing; no claims about investment returns."""
from copy import deepcopy
import unittest

from portfolio_lab.data import digest, utc
from portfolio_lab.engine import simulate
from portfolio_lab.reference import monthly_sma_decisions, reference_rule
from test_portfolio_engine import fixture


def monthly_fixture():
    study, dataset, _ = fixture()
    examples = [
        ("2026-01-30", 100.0, 100.0),
        ("2026-02-27", 100.0, 200.0),
        ("2026-03-02", 300.0, 330.0),
        ("2026-03-31", 330.0, 330.0),
    ]
    dataset["sessions"] = [
        {"session": day, "open_at": day + "T14:30:00Z", "close_at": day + "T21:00:00Z",
         "prices": {"ALFA": {"open": opening, "close": closing}}}
        for day, opening, closing in examples
    ]
    dataset["expected_sessions"] = [row["session"] for row in dataset["sessions"]]
    study["evaluation"]["holdout_start"] = "2026-03-01"
    study["strategy_sha256"] = digest(reference_rule(symbol="ALFA", months=2))
    month_ends = ["2026-01-30", "2026-02-27", "2026-03-31"]
    return study, dataset, month_ends


class PortfolioReferenceTests(unittest.TestCase):
    def test_full_warmup_and_completed_close_timestamp(self):
        study, dataset, schedule = monthly_fixture()
        decisions = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                          month_end_sessions=schedule)
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0]["decision_id"], "sma-ALFA-2026-02-27")
        self.assertEqual(decisions[0]["weights"], {"ALFA": 1.0})
        self.assertEqual((utc(decisions[0]["decided_at"]) - utc(decisions[0]["information_cutoff"])).total_seconds(), 1)
        self.assertTrue(all(decision["origin"] == "retrospective" for decision in decisions))

    def test_equality_and_downtrend_are_cash(self):
        study, dataset, schedule = monthly_fixture()
        dataset["sessions"][1]["prices"]["ALFA"]["close"] = 100.0
        dataset["sessions"][3]["prices"]["ALFA"]["close"] = 50.0
        decisions = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                          month_end_sessions=schedule)
        self.assertEqual([decision["weights"] for decision in decisions], [{}, {}])

    def test_future_prices_cannot_rewrite_prior_decisions(self):
        study, dataset, schedule = monthly_fixture()
        before = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                       month_end_sessions=schedule + ["2026-04-30"])
        extended = deepcopy(dataset)
        extended["sessions"].append({
            "session": "2026-04-30", "open_at": "2026-04-30T14:30:00Z",
            "close_at": "2026-04-30T21:00:00Z", "prices": {"ALFA": {"open": 0.1, "close": 0.1}},
        })
        extended["expected_sessions"].append("2026-04-30")
        after = monthly_sma_decisions(study, extended, symbol="ALFA", months=2,
                                      month_end_sessions=schedule + ["2026-04-30"])
        self.assertEqual(before, after[:len(before)])
        self.assertEqual(after[-1]["weights"], {})

    def test_partial_month_is_not_inferred_from_last_available_bar(self):
        study, dataset, schedule = monthly_fixture()
        dataset["sessions"] = dataset["sessions"][:3]
        dataset["expected_sessions"] = dataset["expected_sessions"][:3]
        decisions = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                          month_end_sessions=schedule)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision_id"], "sma-ALFA-2026-02-27")

    def test_engine_receives_no_same_close_or_preopen_return(self):
        study, dataset, schedule = monthly_fixture()
        decisions = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                          month_end_sessions=schedule)
        report = simulate(study, dataset, decisions)
        self.assertEqual(report["strategy"]["trades"][0]["session"], "2026-03-02")
        self.assertAlmostEqual(report["strategy"]["trades"][0]["reference_proxy_open"], 300.0)
        self.assertAlmostEqual(report["strategy"]["metrics"]["total_return"], 0.1)

    def test_rule_fingerprint_mismatch_fails(self):
        study, dataset, schedule = monthly_fixture()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            monthly_sma_decisions(study, dataset, symbol="ALFA", months=3,
                                   month_end_sessions=schedule)

    def test_invalid_and_conflicting_calendar_dates_fail(self):
        study, dataset, _ = monthly_fixture()
        for schedule in (
            ["2026-02-30"],
            ["2026-02-27", "2026-01-30"],
            ["2026-01-29", "2026-01-30"],
            ["2026-01-30", "2026-03-31"],
            ["2026-01-30", "2026-02-26", "2026-03-31"],
        ):
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                       month_end_sessions=schedule)

    def test_default_ten_month_rule_does_not_invent_warmup(self):
        study, dataset, schedule = monthly_fixture()
        study["strategy_sha256"] = digest(reference_rule(symbol="ALFA"))
        self.assertEqual(monthly_sma_decisions(study, dataset, symbol="ALFA",
                                              month_end_sessions=schedule), [])

    def test_invalid_parameters_and_risk_limits_fail(self):
        for kwargs in ({"months": True}, {"months": 0}, {"months": 1.5},
                       {"equity_weight": -0.1}, {"equity_weight": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                reference_rule(symbol="ALFA", **kwargs)
        study, dataset, schedule = monthly_fixture()
        study["limits"]["max_weight"] = 0.5
        with self.assertRaisesRegex(ValueError, "limits"):
            monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                   month_end_sessions=schedule)

    def test_rule_and_input_objects_are_stable(self):
        study, dataset, schedule = monthly_fixture()
        original = deepcopy((study, dataset, schedule))
        decisions = monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                          month_end_sessions=schedule)
        self.assertEqual((study, dataset, schedule), original)
        self.assertEqual(decisions, monthly_sma_decisions(study, dataset, symbol="ALFA", months=2,
                                                         month_end_sessions=schedule))


if __name__ == "__main__":
    unittest.main()
