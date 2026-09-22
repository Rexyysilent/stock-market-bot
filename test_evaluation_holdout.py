"""M11 (first part): a prospectively declared holdout with purged boundaries."""
from datetime import date
import unittest

from ledger.config import (
    EVALUATION_DESIGN_VERSION, EVALUATION_HOLDOUT_DECLARED_AT,
    EVALUATION_HOLDOUT_START,
)
from ledger.evaluation import evaluation_design, evaluation_partition
from ledger.stats import _cell


class EvaluationHoldoutTests(unittest.TestCase):
    def test_m11_label_crossing_into_holdout_is_purged(self):
        # M11: train label exits 2026-10-05, validation starts 2026-10-01.
        self.assertEqual(
            evaluation_partition("2026-09-28", "2026-10-05", "2026-10-01"), "purged")

    def test_partition_boundaries(self):
        self.assertEqual(
            evaluation_partition("2026-09-29", "2026-09-30", "2026-10-01"), "development")
        self.assertEqual(
            evaluation_partition("2026-10-01", "2026-10-02", "2026-10-01"), "holdout")
        self.assertEqual(
            evaluation_partition("2026-09-30", "2026-10-01", "2026-10-01"), "purged")
        self.assertIsNone(evaluation_partition(None, "2026-10-01", "2026-10-01"))
        self.assertIsNone(evaluation_partition("2026-10-02", "2026-10-01", "2026-10-01"))

    def test_declaration_precedes_holdout_and_is_pinned_to_its_version(self):
        declared = date.fromisoformat(EVALUATION_HOLDOUT_DECLARED_AT)
        start = date.fromisoformat(EVALUATION_HOLDOUT_START)
        self.assertLess(declared, start)
        # Moving the holdout is a new design with a new version, never an edit.
        pinned = {"holdout-1": ("2026-10-01", "2026-09-22")}
        self.assertEqual(
            pinned[EVALUATION_DESIGN_VERSION],
            (EVALUATION_HOLDOUT_START, EVALUATION_HOLDOUT_DECLARED_AT),
        )
        design = evaluation_design()
        self.assertEqual(design["version"], EVALUATION_DESIGN_VERSION)
        self.assertEqual(design["holdout_start"], EVALUATION_HOLDOUT_START)
        self.assertIn("purged", design["rule"])

    def test_stats_cells_report_partition_counts(self):
        def row(i, entry, exit_):
            return {"status": "filled", "ticker": f"T{i}", "entry_session": entry,
                    "exit_session": exit_, "ret": 0.01, "univ_ret": 0.01,
                    "excess": 0.0, "benchmark_status": "complete",
                    "direction": "none", "asset_class": "equity"}
        rows = [row(0, "2026-09-20", "2026-09-21"),
                row(1, "2026-09-29", "2026-10-02"),
                row(2, "2026-10-01", "2026-10-02"),
                row(3, "2026-10-05", "2026-10-06")]
        cell = _cell(rows, 20)
        self.assertEqual(
            (cell["n_development"], cell["n_purged"], cell["n_holdout"]), (1, 1, 2))
        self.assertEqual(cell["n"], 4)


if __name__ == "__main__":
    unittest.main()
