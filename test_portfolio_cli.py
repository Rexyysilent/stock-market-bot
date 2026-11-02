"""Real local CLI workflows and immutable research deliverables.

Every subprocess uses temporary local files. No provider-acquisition command is
invoked; these are synthetic accounting and interface checks. Normal interpreter
startup permits the existing tzdata package on systems without an OS IANA store.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


def independent_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PortfolioCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")

    def process(self, *args, success=True):
        completed = subprocess.run(
            [sys.executable, "-m", "portfolio_lab", *map(str, args)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30,
        )
        if success:
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
        else:
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
        return completed

    def parsed(self, *args):
        return json.loads(self.process(*args).stdout)

    def demo(self, name="demo"):
        directory = self.root / name
        result = self.parsed("demo", "--out", directory)
        self.assertTrue(result["synthetic"])
        self.assertTrue(result["research_only"])
        return directory

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def write(path, value):
        Path(path).write_text(json.dumps(value), encoding="utf-8")

    def freeze_and_record(self):
        source = self.demo()
        workspace = self.root / "private-paper"
        frozen = self.parsed("freeze", "--study", source / "study.json", "--workspace", workspace)
        study = self.read(source / "study.json")
        request = self.root / "private-decision.json"
        self.write(request, {"decision_id": "paper-one", "strategy_sha256": study["strategy_sha256"],
                             "weights": {"ALFA": 0.4}})
        archive = self.root / "private-source.json"
        self.write(archive, {"schema_version": "2.8", "generated_at": "2024-01-01T00:00:00Z",
                             "health": {"status": "WARN"}, "sections": {}, "synthetic": True})
        recorded = self.parsed("record", "--workspace", workspace, "--decision", request,
                               "--archive", archive)
        return source, workspace, request, archive, frozen, recorded

    def test_demo_saves_hash_bound_inputs_and_reproducible_synthetic_result(self):
        first = self.demo("first")
        second = self.demo("second")
        required = {"study.json", "dataset.json", "decisions.json", "report.json", "report.md", "receipt.json"}
        self.assertEqual({item.name for item in first.iterdir()}, required)
        receipt = self.read(first / "receipt.json")
        self.assertTrue(receipt["research_only"])
        for filename, expected in receipt["inputs"].items():
            self.assertEqual(independent_digest(self.read(first / filename)), expected)
        # Receipt creation clocks differ; the exact model and source identities
        # and deterministic result must not change between identical runs.
        self.assertEqual(receipt["inputs"], self.read(second / "receipt.json")["inputs"])
        self.assertEqual((first / "report.json").read_bytes(), (second / "report.json").read_bytes())
        report = self.read(first / "report.json")
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["evaluation"]["statistical_or_investment_approval"])
        self.assertIn("SYNTHETIC DEMONSTRATION", (first / "report.md").read_text())

    def test_repeat_output_is_refused_and_prior_receipt_remains_exact(self):
        output = self.demo()
        original = {item.name: item.read_bytes() for item in output.iterdir()}
        self.process("demo", "--out", output, success=False)
        self.assertEqual({item.name: item.read_bytes() for item in output.iterdir()}, original)

    def test_private_input_validation_and_evaluation_roundtrip(self):
        source = self.demo()
        flags = ("--study", source / "study.json", "--dataset", source / "dataset.json",
                 "--decisions", source / "decisions.json")
        validation = self.parsed("validate", *flags)
        self.assertTrue(validation["contract_valid"])
        self.assertFalse(validation["provider_truth_verified"])
        output = self.root / "evaluated"
        result = self.parsed("evaluate", *flags, "--out", output)
        self.assertTrue(result["research_only"])
        for filename in ("study.json", "dataset.json", "decisions.json", "report.json"):
            self.assertEqual(self.read(source / filename), self.read(output / filename))

    def test_freeze_record_and_retry_keep_one_decision_and_original_time(self):
        source, workspace, request, archive, frozen, recorded = self.freeze_and_record()
        again = self.parsed("record", "--workspace", workspace, "--decision", request,
                            "--archive", archive)
        self.assertFalse(recorded["already_recorded"])
        self.assertTrue(again["already_recorded"])
        self.assertEqual(recorded["receipt_sha256"], again["receipt_sha256"])
        self.assertEqual(recorded["recorded_at"], again["recorded_at"])
        journal = self.parsed("journal", "--workspace", workspace)
        self.assertEqual(journal["decisions"], 1)
        self.assertTrue(journal["local_receipt_chain_verified"])
        self.assertEqual(journal["study_sha256"], frozen["study_sha256"])
        self.assertNotIn(str(archive), json.dumps(journal))
        same_freeze = self.parsed("freeze", "--study", source / "study.json", "--workspace", workspace)
        self.assertEqual(frozen, same_freeze)

    def test_conflicting_record_is_rejected_without_altering_journal(self):
        _, workspace, request, archive, _, recorded = self.freeze_and_record()
        changed = self.read(request)
        changed["weights"] = {"ALFA": 0.6}
        self.write(request, changed)
        self.process("record", "--workspace", workspace, "--decision", request,
                     "--archive", archive, success=False)
        journal = self.parsed("journal", "--workspace", workspace)
        self.assertEqual(journal["decisions"], 1)
        self.assertEqual(journal["latest_receipt"]["receipt_sha256"], recorded["receipt_sha256"])

    def test_journal_evaluation_preserves_provenance_without_backdated_fills(self):
        source, workspace, _, _, frozen, recorded = self.freeze_and_record()
        output = self.root / "paper-evaluation"
        self.parsed("evaluate-journal", "--workspace", workspace, "--dataset", source / "dataset.json",
                    "--out", output)
        report = self.read(output / "report.json")
        evaluation = report["evaluation"]
        self.assertTrue(evaluation["local_receipt_chain_verified"])
        self.assertFalse(evaluation["local_receipt_chain_is_external_attestation"])
        self.assertEqual(evaluation["journal_frozen_at"], frozen["frozen_at"])
        self.assertFalse(evaluation["statistical_or_investment_approval"])
        decisions = self.read(output / "decisions.json")["decisions"]
        self.assertEqual(decisions[0]["recorded_at"], recorded["recorded_at"])
        self.assertEqual(decisions[0]["evidence"][0]["health_status"], "WARN")
        # The fixture ended in 2024. A decision recorded now must remain pending
        # instead of acquiring historic returns from the source's older date.
        self.assertEqual(report["decision_executions"][0]["status"], "pending")
        self.assertEqual(report["strategy"]["trades"], [])
        receipt = self.read(output / "receipt.json")
        self.assertEqual(receipt["context"]["journal_genesis_sha256"], frozen["genesis_sha256"])

    def test_income_losses_and_zero_do_not_become_positive_income_claims(self):
        result = self.parsed("income", "--capital", "10000", "--currency", "INR",
                             "--annual-rates=-20,0,8", "--annual-fixed-cost", "1200", "--monthly-goal", "1000")
        self.assertTrue(result["research_only"])
        loss, zero, growth = result["scenarios"]
        self.assertLess(loss["monthly_equivalent_after_fixed_cost"], 0)
        self.assertEqual(zero["monthly_equivalent_after_fixed_cost"], -100)
        self.assertIsNone(loss["required_capital_for_goal"])
        self.assertIsNone(zero["required_capital_for_goal"])
        self.assertAlmostEqual(growth["net_annual_change_after_fixed_cost"], -400)
        self.assertTrue(all(not row["goal_covered_by_assumed_net_change"] for row in result["scenarios"]))
        self.assertIn("not a promised cash payout", " ".join(result["limitations"]))

    def test_invalid_currency_fails_before_output_without_echoing_input_value(self):
        source = self.demo()
        study = self.read(source / "study.json")
        sensitive_value = "private-value-that-should-not-appear-in-errors"
        study["currency"] = sensitive_value
        private = self.root / "invalid-study.json"
        self.write(private, study)
        output = self.root / "invalid-result"
        failure = self.process("evaluate", "--study", private, "--dataset", source / "dataset.json",
                               "--decisions", source / "decisions.json", "--out", output, success=False)
        self.assertEqual(failure.returncode, 2)
        self.assertEqual(failure.stdout, "")
        self.assertNotIn(sensitive_value, failure.stderr)
        self.assertFalse(output.exists())

    def test_missing_input_and_invalid_income_have_concise_errors(self):
        missing = self.process("freeze", "--study", self.root / "missing.json",
                               "--workspace", self.root / "not-created", success=False)
        self.assertEqual(missing.returncode, 2)
        self.assertFalse((self.root / "not-created").exists())
        invalid = self.process("income", "--capital", "nan", success=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")

    def test_public_repository_output_path_is_refused_without_creation(self):
        forbidden = ROOT / "fixtures" / "portfolio-cli-must-not-create"
        self.assertFalse(forbidden.exists())
        failure = self.process("demo", "--out", forbidden, success=False)
        self.assertEqual(failure.returncode, 2)
        self.assertFalse(forbidden.exists())

    def test_demo_does_not_import_market_network_or_scientific_packages(self):
        program = r'''
import contextlib
import io
import sys
before = set(sys.modules)
from portfolio_lab.__main__ import main
with contextlib.redirect_stdout(io.StringIO()):
    result = main(["demo", "--out", sys.argv[1]])
unexpected = sorted({name.split(".")[0] for name in set(sys.modules) - before} &
                    {"numpy", "pandas", "yfinance", "requests", "pandas_market_calendars",
                     "praw", "ollama", "discord"})
if result or unexpected:
    raise SystemExit("unexpected provider/scientific imports: " + repr(unexpected))
print("local core completed without provider/scientific packages")
'''
        completed = subprocess.run([sys.executable, "-c", program, str(self.root / "imports-demo")],
                                   cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("without provider/scientific packages", completed.stdout)

    def test_report_cannot_be_saved_against_mismatched_inputs_or_labels(self):
        # save_run is also a public Python API. Exercise that boundary in a
        # clean stdlib process: a hash-linked receipt must reject a stale report
        # paired with another study/data/decisions, or false evidence labels.
        program = r'''
from copy import deepcopy
from pathlib import Path
import sys
from portfolio_lab.demo import demo_inputs
from portfolio_lab.engine import simulate
from portfolio_lab.report import save_run

root = Path(sys.argv[1])
variants = ("study", "dataset", "decisions", "synthetic", "currency", "calendar",
            "rule", "basis", "dataset_id", "research_only")
for index, variant in enumerate(variants):
    study, dataset, decisions = demo_inputs()
    report = simulate(study, dataset, decisions)
    if variant == "study":
        study["initial_cash"] *= 2
    elif variant == "dataset":
        dataset["sessions"][0]["prices"]["ALFA"]["close"] += 1
    elif variant == "decisions":
        decisions[0]["weights"] = {"ALFA": 0.1}
    elif variant == "synthetic":
        report["synthetic"] = False
    elif variant == "currency":
        report["currency"] = "INR"
    elif variant == "calendar":
        report["calendar"] = "XNSE"
    elif variant == "rule":
        report["strategy_sha256"] = "0" * 64
    elif variant == "basis":
        report["price_basis"] = "raw_executable_share_price"
    elif variant == "dataset_id":
        report["dataset_id"] = "different-data"
    else:
        report["research_only"] = False
    target = root / str(index)
    try:
        save_run(target, study, dataset, decisions, report)
    except ValueError:
        if target.exists():
            raise SystemExit("mismatch created output before rejection: " + variant)
    else:
        raise SystemExit("mismatched report accepted: " + variant)
print("all mismatches refused before publication")
'''
        completed = subprocess.run([sys.executable, "-c", program, str(self.root / "mismatches")],
                                   cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("all mismatches refused", completed.stdout)


if __name__ == "__main__":
    unittest.main()
