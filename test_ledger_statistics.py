"""A large non-directional sample cannot license a tiny directional estimate."""
import unittest
from ledger.stats import _cell


class LedgerStatisticsTests(unittest.TestCase):
    def rows(self, directional):
        return [{"status": "filled", "ticker": f"TEST{i}",
                 "entry_session": "2026-09-01", "ret": 0.02,
                 "univ_ret": 0.01, "excess": 0.01,
                 "direction": "long" if i < directional else "none",
                 "asset_class": "equity"} for i in range(20)]

    def test_directional_sample_has_its_own_gate(self):
        cell = _cell(self.rows(1), "test", 20, 100)
        self.assertEqual(cell["n"], 20)
        self.assertEqual(cell["n_directional"], 1)
        self.assertIsNone(cell["hit_rate"])
        self.assertIsNone(cell["mean_ci95"])
        self.assertEqual(cell["directional_status"], "accumulating")

    def test_adequate_directional_sample_preserves_descriptive_statistics(self):
        cell = _cell(self.rows(20), "test", 20, 100)
        self.assertEqual(cell["hit_rate"], 1)
        self.assertEqual(cell["directional_status"], "ready")
        self.assertEqual(cell["n_entry_sessions"], 1)
        self.assertIn("overlapping", cell["dependence_warning"])
        self.assertIn("iid", cell["ci_method"])


if __name__ == "__main__":
    unittest.main()
