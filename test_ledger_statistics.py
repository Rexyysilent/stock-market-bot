"""A large non-directional sample cannot license a tiny directional estimate;
energy needs matched benchmark support; no inferential interval without a
dependence-aware sampling design (M09/M10)."""
import unittest
from ledger.stats import _cell


def _row(i, *, session="2026-09-01", ret=0.02, univ_ret=0.01,
         benchmark_status="complete", direction="long"):
    return {"status": "filled", "ticker": f"TEST{i}",
            "entry_session": session, "ret": ret,
            "univ_ret": univ_ret, "excess": 0.01,
            "benchmark_status": benchmark_status,
            "direction": direction, "asset_class": "equity"}


class LedgerStatisticsTests(unittest.TestCase):
    def rows(self, directional):
        return [_row(i, direction="long" if i < directional else "none")
                for i in range(20)]

    def test_directional_sample_has_its_own_gate(self):
        cell = _cell(self.rows(1), 20)
        self.assertEqual(cell["n"], 20)
        self.assertEqual(cell["n_directional"], 1)
        self.assertIsNone(cell["hit_rate"])
        self.assertIsNone(cell["mean_ci95"])
        self.assertEqual(cell["directional_status"], "accumulating")

    def test_adequate_directional_sample_preserves_descriptive_statistics(self):
        cell = _cell(self.rows(20), 20)
        self.assertEqual(cell["hit_rate"], 1)
        self.assertEqual(cell["mean_signed_excess"], 0.01)
        self.assertEqual(cell["directional_status"], "ready")
        self.assertEqual(cell["n_entry_sessions"], 1)
        self.assertIn("overlapping", cell["dependence_warning"])

    def test_m10_one_session_is_not_twenty_independent_episodes(self):
        cell = _cell(self.rows(20), 20)
        self.assertEqual((cell["n"], cell["n_entry_sessions"]), (20, 1))
        self.assertIsNone(cell["mean_ci95"])
        self.assertEqual(cell["inference_status"], "ineligible")
        self.assertIn("not independent", cell["dependence_warning"])

    def test_m09_energy_requires_matched_benchmark_pairs(self):
        rows = [_row(i, session=f"2026-09-{i + 1:02d}", univ_ret=None,
                     benchmark_status="partial") for i in range(19)]
        rows.append(_row(19, session="2026-09-20", univ_ret=0.001))
        cell = _cell(rows, 20)
        self.assertEqual(cell["status"], "ready")  # raw support is intact
        self.assertEqual(cell["n_benchmark_pairs"], 1)
        self.assertEqual(cell["energy_status"], "accumulating")
        self.assertIsNone(cell["energy"])

    def test_matched_energy_uses_same_pairs_on_both_sides(self):
        rows = [_row(i, session=f"2026-09-{i + 1:02d}", ret=0.02, univ_ret=0.01)
                for i in range(20)]
        # A large raw move without a complete benchmark must not enter the
        # numerator of the matched ratio.
        rows.append(_row(20, session="2026-09-21", ret=5.0, univ_ret=None,
                         benchmark_status="partial"))
        cell = _cell(rows, 20)
        self.assertEqual(cell["n_benchmark_pairs"], 20)
        self.assertEqual(cell["energy_status"], "ready")
        self.assertEqual(cell["energy"], 2.0)

    def test_zero_universe_move_leaves_energy_undefined(self):
        # 0.5 * 0.1 + 0.5 * -0.1 is 0 in exact arithmetic, ~5.6e-17 in floats.
        rows = [_row(i, session=f"2026-09-{i + 1:02d}",
                     univ_ret=0.5 * 0.1 + 0.5 * -0.1) for i in range(20)]
        cell = _cell(rows, 20)
        self.assertEqual(cell["n_benchmark_pairs"], 20)
        self.assertEqual(cell["energy_status"], "undefined_zero_benchmark")
        self.assertIsNone(cell["energy"])

    def test_legacy_benchmark_without_revision_is_not_matched(self):
        rows = [_row(i, session=f"2026-09-{i + 1:02d}", benchmark_status=None)
                for i in range(20)]
        cell = _cell(rows, 20)
        self.assertEqual(cell["n_benchmark_pairs"], 0)
        self.assertEqual(cell["energy_status"], "unavailable")
        self.assertIsNone(cell["energy"])


if __name__ == "__main__":
    unittest.main()
