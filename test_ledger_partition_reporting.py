"""Partitioned descriptions cannot disguise losing holdout observations."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ledger.db import connect
from ledger.evaluation import evaluation_design
from ledger.stats import _cell, _partitioned_cell, build_stats, render_markdown


def row(identifier, entry, exit_, excess, *, direction="long", benchmark_status="complete"):
    return {"status": "filled", "ticker": f"SYNTH{identifier}",
            "entry_session": entry, "exit_session": exit_, "ret": excess,
            "univ_ret": 0.01, "excess": excess, "benchmark_status": benchmark_status,
            "direction": direction, "asset_class": "equity"}


class LedgerPartitionReportingTests(unittest.TestCase):
    def rows(self):
        return [row(1, "2026-09-20", "2026-09-21", 0.5),
                row(2, "2026-09-30", "2026-10-02", 1.0),
                row(3, "2026-10-02", "2026-10-05", -0.2)]

    def test_positive_development_cannot_mask_negative_holdout(self):
        cell = _partitioned_cell(self.rows(), 1)
        self.assertAlmostEqual(cell["mean_signed_excess"], 0.43333333)
        development = cell["evaluation_partitions"]["development"]
        holdout = cell["evaluation_partitions"]["holdout"]
        self.assertEqual(development["mean_signed_excess"], 0.5)
        self.assertEqual(holdout["mean_signed_excess"], -0.2)
        self.assertEqual(holdout["hit_rate"], 0)
        self.assertEqual(development["n"], 1)
        self.assertEqual(holdout["n"], 1)
        self.assertEqual(cell["evaluation_exclusions"]["purged"]["rows"], 1)
        self.assertEqual(cell["evaluation_scope"], "pooled_descriptive_including_purged")

    def test_legacy_pooled_fields_are_unchanged(self):
        expected = _cell(self.rows(), 1)
        current = _partitioned_cell(self.rows(), 1)
        self.assertEqual({key: current[key] for key in expected}, expected)

    def test_a_large_pooled_sample_does_not_unlock_small_holdout(self):
        rows = [row(i, "2026-09-20", "2026-09-21", 0.1) for i in range(30)]
        rows.append(row(100, "2026-10-02", "2026-10-05", -0.2))
        cell = _partitioned_cell(rows, 20)
        self.assertEqual(cell["status"], "ready")
        holdout = cell["evaluation_partitions"]["holdout"]
        self.assertEqual(holdout["n"], 1)
        self.assertEqual(holdout["status"], "accumulating")
        self.assertIsNone(holdout["hit_rate"])
        self.assertIsNone(holdout["mean_signed_excess"])
        self.assertIsNone(holdout["energy"])

    def test_holdout_directional_and_benchmark_support_remain_separate(self):
        rows = [row(i, "2026-10-02", "2026-10-05", -0.2,
                    direction="long" if i == 0 else "none",
                    benchmark_status="complete" if i == 0 else "partial") for i in range(20)]
        holdout = _partitioned_cell(rows, 20)["evaluation_partitions"]["holdout"]
        self.assertEqual(holdout["status"], "ready")
        self.assertEqual(holdout["directional_status"], "accumulating")
        self.assertEqual(holdout["energy_status"], "accumulating")
        self.assertIsNone(holdout["hit_rate"])
        self.assertIsNone(holdout["energy"])
        self.assertEqual(holdout["inference_status"], "ineligible")

    def test_missing_and_invalid_windows_are_explicitly_unassigned(self):
        rows = self.rows()
        pending = deepcopy(rows[0])
        pending.update(status="pending", entry_session=None, exit_session=None, ret=None)
        invalid = deepcopy(pending)
        invalid.update(entry_session="2026-10-05", exit_session="2026-10-01")
        rows.extend((pending, invalid))
        cell = _partitioned_cell(rows, 1)
        self.assertEqual(cell["evaluation_exclusions"]["unassigned"]["rows"], 2)
        self.assertEqual(cell["evaluation_partitions"]["development"]["n"], 1)
        self.assertEqual(cell["evaluation_partitions"]["holdout"]["n"], 1)

    def test_empty_partitions_do_not_inherit_pooled_statistics(self):
        cell = _partitioned_cell([row(1, "2026-09-20", "2026-09-21", 0.1)], 1)
        empty = cell["evaluation_partitions"]["holdout"]
        self.assertEqual(empty["n"], 0)
        self.assertIsNone(empty["mean_signed_excess"])
        self.assertEqual(empty["status"], "accumulating")

    def test_markdown_labels_all_three_scopes_and_shows_negative_holdout(self):
        stats = {"as_of_session": "2026-10-05",
                 "config": {"evaluation_design": evaluation_design()},
                 "segments": [{"pipeline_version": "synthetic",
                               "groups": [{"grain": "family", "key": "synthetic",
                                           "horizons": {"1": _partitioned_cell(self.rows(), 1)}}]}]}
        rendered = render_markdown(stats)
        self.assertIn("Pooled descriptive outcomes", rendered)
        self.assertIn("Development observations (descriptive)", rendered)
        self.assertIn("Holdout observations (descriptive)", rendered)
        heldout_section = rendered.split("#### Holdout observations", 1)[1]
        self.assertIn("mean excess -20.00%", heldout_section)
        self.assertIn("1 purged rows; 0 unassigned rows", heldout_section)
        self.assertIn("Time labels alone do not prove", heldout_section)
        self.assertNotIn("mean excess 50.00%", heldout_section)

    def test_database_report_publishes_partitioned_results_for_each_horizon(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.db"
            conn = connect(path)
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?)",
                         ("synthetic-run", "2026-09-19T21:00:00Z", "unknown", "a" * 64, "synthetic"))
            for number, observation in enumerate(self.rows()):
                identifier = f"synthetic-{number}"
                conn.execute(
                    "INSERT INTO signals(record_id,source_record_id,family,ticker,direction,"
                    "first_seen_run,last_seen_run,asset_class,payload) VALUES(?,?,?,?,?,?,?,?,?)",
                    (identifier, identifier, "synthetic", observation["ticker"], "long",
                     "synthetic-run", "synthetic-run", "equity", "{}"))
                conn.execute(
                    "INSERT INTO outcomes(record_id,horizon,entry_session,exit_session,ret,excess,status) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (identifier, 1, observation["entry_session"], observation["exit_session"],
                     observation["ret"], observation["excess"], "filled"))
            conn.commit()
            conn.close()
            stats = build_stats(path, min_n=1)
        self.assertEqual(stats["config"]["evaluation_reporting_version"], "partitioned-descriptive-1")
        cell = stats["segments"][0]["groups"][0]["horizons"]["1"]
        self.assertEqual(cell["evaluation_partitions"]["holdout"]["mean_signed_excess"], -0.2)
        self.assertEqual(cell["evaluation_exclusions"]["purged"]["rows"], 1)


if __name__ == "__main__":
    unittest.main()
