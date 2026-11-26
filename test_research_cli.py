"""Offline public-entry-point checks for listing and verification research."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ledger.db import connect
from ledger.verification import VerificationPolicy


ROOT = Path(__file__).resolve().parent


class ResearchCLI(unittest.TestCase):
    def invoke(self, *args, success=True):
        result = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_listings_without_snapshot_has_no_market_denominator(self):
        report = self.invoke("marketbot.py", "listings")
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertFalse(report["historical_market_recall"]["available"])
        self.assertEqual(report["activations"], [])
        self.assertEqual(report["inferred_delistings"], [])

    def test_missing_database_does_not_become_empty_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy = VerificationPolicy("cli-missing", datetime.now(timezone.utc).isoformat(),
                                        0.05, 20)
            path = root / "policy.json"
            path.write_text(json.dumps(policy.to_dict()), encoding="utf-8")
            database = root / "absent.db"
            self.invoke("-m", "ledger", "verify", "--db", database,
                        "--policy", path, success=False)
            self.assertFalse(database.exists())
            database.write_bytes(b"not a database")
            self.invoke("-m", "ledger", "verify", "--db", database,
                        "--policy", path, success=False)
            self.assertEqual(database.read_bytes(), b"not a database")

    def test_empty_database_reports_no_evidence_and_preserves_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "empty.db"
            conn = connect(database)
            conn.close()
            policy = VerificationPolicy("cli-empty", datetime.now(timezone.utc).isoformat(),
                                        0.05, 20)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy.to_dict()), encoding="utf-8")
            output = root / "review.json"
            self.invoke("-m", "ledger", "verify", "--db", database,
                        "--policy", policy_path, "--out", output)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["summary"]["observed"], 0)
            self.invoke("-m", "ledger", "verify", "--db", database,
                        "--policy", policy_path, "--out", output, success=False)
            self.assertEqual(output.read_bytes(), saved)

    def test_monitor_survives_archive_relocation_without_rewriting_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, original, relocated = root / "study", root / "original", root / "relocated"
            relocated.mkdir()
            frozen = self.invoke("-m", "ledger", "monitor-init", "--workspace", workspace,
                                 "--archives", original, "--policy-id", "cli-monitor",
                                 "--move-threshold", "0.05", "--round-trip-cost-bps", "20")
            before = (workspace / "policy.json").read_bytes()
            repeated = self.invoke("-m", "ledger", "monitor-init", "--workspace", workspace,
                                   "--archives", original, "--policy-id", "cli-monitor",
                                   "--move-threshold", "0.05", "--round-trip-cost-bps", "20")
            self.assertEqual(repeated["policy_sha256"], frozen["policy_sha256"])
            self.invoke("-m", "ledger", "monitor-init", "--workspace", workspace,
                        "--archives", original, "--policy-id", "cli-monitor",
                        "--move-threshold", "0.06", "--round-trip-cost-bps", "20", success=False)
            reviewed = self.invoke("-m", "ledger", "monitor-run", "--workspace", workspace,
                                   "--archives", relocated)
            self.assertEqual(reviewed["status"], "awaiting_archives")
            self.assertEqual(reviewed["report"]["archive_inventory"]["state"], "available")
            self.assertEqual(reviewed["receipt"]["archive_dir"], str(relocated.resolve()))
            self.assertEqual((workspace / "policy.json").read_bytes(), before)
            status = self.invoke("-m", "ledger", "monitor-status", "--workspace", workspace)
            self.assertEqual(status["policy_sha256"], frozen["policy_sha256"])
            self.assertEqual(status["review_count"], 1)
            self.assertFalse(status["independent_timestamp_attestation"])

    def test_planner_cli_preserves_output_and_rejects_unknown_problem(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "planning.json"
            self.invoke("-m", "portfolio_lab", "planning-demo", "--simulations", "64",
                        "--seed", "17", "--out", output)
            saved = output.read_bytes()
            self.assertIsInstance(json.loads(saved), dict)
            self.invoke("-m", "portfolio_lab", "planning-demo", "--out", output, success=False)
            self.assertEqual(output.read_bytes(), saved)
            problem = root / "bad.json"
            problem.write_text('{"realized_returns":[0.4]}', encoding="utf-8")
            self.invoke("-m", "portfolio_lab", "planning-compare", "--problem", problem,
                        success=False)


if __name__ == "__main__":
    unittest.main()
