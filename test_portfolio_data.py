"""Data integrity contracts for hypothetical portfolio measurement."""
import copy
from pathlib import Path
import tempfile
import unittest

from portfolio_lab.data import load_json, utc, validate_dataset, validate_decisions, validate_study
from portfolio_lab.demo import demo_inputs


class PortfolioDataTests(unittest.TestCase):
    def setUp(self):
        self.study, self.data, self.decisions = demo_inputs()

    def test_valid_synthetic_inputs(self):
        validate_study(self.study)
        validate_dataset(self.data, self.study)
        validate_decisions(self.decisions, self.study)

    def test_json_duplicate_and_nonfinite_fail(self):
        for payload in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "input.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_json(path)

    def test_timezone_is_required(self):
        with self.assertRaises(ValueError):
            utc("2024-01-02T12:00:00")

    def test_unknown_cost_or_execution_fields_not_silently_ignored(self):
        self.study["execution"]["latenccy_seconds"] = 0
        with self.assertRaises(ValueError):
            validate_study(self.study)

    def test_interior_missing_asset_is_not_zero_or_forward_fill(self):
        del self.data["sessions"][3]["prices"]["ALFA"]
        with self.assertRaises(ValueError):
            validate_dataset(self.data, self.study)

    def test_missing_entire_session_is_detected_against_schedule(self):
        del self.data["sessions"][3]
        with self.assertRaises(ValueError):
            validate_dataset(self.data, self.study)

    def test_duplicate_expected_sessions_are_rejected(self):
        self.data["expected_sessions"][1] = self.data["expected_sessions"][0]
        with self.assertRaises(ValueError):
            validate_dataset(self.data, self.study)

    def test_currency_and_calendar_and_raw_basis_not_interchangeable(self):
        for key, value in (("currency", "INR"), ("calendar", "XNSE"), ("price_basis", "raw")):
            altered = copy.deepcopy(self.data)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_dataset(altered, self.study)

    def test_future_or_nonfinite_close_cannot_make_a_valid_bar(self):
        for key, value in (("close", float("nan")), ("open", 0), ("close", True)):
            altered = copy.deepcopy(self.data)
            altered["sessions"][0]["prices"]["ALFA"][key] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_dataset(altered, self.study)
        self.data["acquired_at"] = "2024-01-10T00:00:00Z"
        with self.assertRaises(ValueError):
            validate_dataset(self.data, self.study)

    def test_future_knowledge_and_leverage_refused(self):
        for key, value in (("information_cutoff", "2099-01-01T00:00:00Z"), ("weights", {"ALFA": 1.1})):
            altered = copy.deepcopy(self.decisions)
            altered[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_decisions(altered, self.study)

    def test_error_source_and_unobserved_archive_refused(self):
        for known, health in (("2024-01-03T00:00:00Z", "OK"), ("2024-01-02T20:00:00Z", "ERROR")):
            altered = copy.deepcopy(self.decisions)
            altered[0]["evidence"] = [{"archive_sha256": "a" * 64, "generated_at": "2024-01-02T19:00:00Z",
                                      "known_at": known, "health_status": health}]
            with self.subTest(known=known, health=health), self.assertRaises(ValueError):
                validate_decisions(altered, self.study)

    def test_paper_timestamp_and_rule_fingerprint_cannot_be_missing(self):
        self.decisions[0]["origin"] = "paper"
        with self.assertRaises(ValueError):
            validate_decisions(self.decisions, self.study)
        self.decisions[0]["recorded_at"] = self.decisions[0]["decided_at"]
        self.decisions[0]["strategy_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            validate_decisions(self.decisions, self.study)

    def test_india_tariff_cannot_silently_apply_to_us_calendar(self):
        self.study["currency"] = "INR"
        self.study["cost_model"] = {"kind": "india_delivery", "broker": "zerodha",
                                     "as_of": "2026-10-23", "dp_sell_fee": 15.34}
        with self.assertRaisesRegex(ValueError, "XNSE"):
            validate_study(self.study)

    def test_evidence_cannot_postdate_claimed_information_cutoff(self):
        self.decisions[0]["information_cutoff"] = "2024-01-02T20:00:00Z"
        self.decisions[0]["evidence"] = [{"archive_sha256": "a" * 64,
                                         "generated_at": "2024-01-02T19:00:00Z",
                                         "known_at": "2024-01-02T20:01:00Z", "health_status": "OK"}]
        with self.assertRaisesRegex(ValueError, "cutoff"):
            validate_decisions(self.decisions, self.study)

    def test_future_real_dataset_and_huge_number_refused(self):
        self.data.update(synthetic=False, acquired_at="2199-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "future"):
            validate_dataset(self.data, self.study)
        self.study["initial_cash"] = 10 ** 1000
        with self.assertRaises(ValueError):
            validate_study(self.study)


if __name__ == "__main__":
    unittest.main()
