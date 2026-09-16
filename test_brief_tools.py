import copy
import json
from pathlib import Path
import tempfile
import unittest
from brief_tools import load_brief, inspect_brief, diff_briefs


class BriefToolsTests(unittest.TestCase):
    def setUp(self):
        self.brief = {"pipeline_version": "test", "schema_version": "test", "universe": {"tickers": ["AAA", "BBB"]},
                      "health": {"status": "WARN", "warnings": ["provider unavailable"]},
                      "sections": {"prices": [{"ticker": "AAA", "price": 0}, {"ticker": "BBB", "price": None}],
                                   "technicals": [{"ticker": "AAA", "rsi": 0}, {"ticker": "BBB", "rsi": None}]}}

    def test_missing_is_not_zero(self):
        coverage = inspect_brief(self.brief)["coverage"]
        self.assertEqual(coverage["numeric_price_records"], 1)
        self.assertEqual(coverage["missing_numeric_prices"], ["BBB"])
        self.assertEqual(coverage["rsi_unavailable"], ["BBB"])

    def test_unavailable_rotation_is_flagged(self):
        self.brief["sections"]["sector_rotation"] = {"signal": "NEUTRAL", "spread_5d": None}
        self.assertIn("derived_measurement_warning", inspect_brief(self.brief))

    def test_configuration_change_is_not_silent(self):
        current = copy.deepcopy(self.brief)
        current["pipeline_version"] = "next"
        current["health"] = {"status": "OK", "warnings": []}
        result = diff_briefs(self.brief, current)
        self.assertFalse(result["configuration_comparable"])
        self.assertEqual(result["warnings_removed"], ["provider unavailable"])
        self.assertTrue(diff_briefs(self.brief, self.brief)["configuration_comparable"])

    def test_missing_versions_not_assumed_comparable(self):
        self.assertFalse(diff_briefs({}, {})["configuration_comparable"])

    def test_real_schema_fixture_uses_sections(self):
        fixture = load_brief(Path(__file__).parent / "fixtures/exports/daily_brief.valid.json")
        result = diff_briefs({}, fixture)
        self.assertEqual(len(result["new_headline_records"]), 1)
        self.assertEqual(inspect_brief(fixture)["pipeline_version"], fixture["pipeline_version"])

    def test_read_only_strict_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "brief.json"
            for content in ('[]', '{"price":NaN}'):
                path.write_text(content)
                with self.assertRaises(ValueError): load_brief(path)
                self.assertEqual(path.read_text(), content)
            path.write_text(json.dumps(self.brief))
            self.assertEqual(load_brief(path), self.brief)


if __name__ == "__main__": unittest.main()
