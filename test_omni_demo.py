"""Offline CLI/API synthetic demonstration; no provider, human or pilot credit."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from omni.lifecycle import resolve_reference
from omni.receipts import get_receipt, list_receipts
from scripts.omni_synthetic_demo import ROOT, run_demo

SCRIPT = ROOT / "scripts" / "omni_synthetic_demo.py"
OFFLINE_LAUNCHER = """
import runpy
import sys

def refuse_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                 'socket.bind', 'socket.sendto', 'urllib.Request', 'http.client.connect'}:
        raise RuntimeError('network attempt forbidden in synthetic demo test: ' + event)

sys.addaudithook(refuse_network)
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name='__main__')
"""


class SyntheticDemoTests(unittest.TestCase):
    def test_cli_demo_exports_synthetic_workflow_with_resolvable_refs_and_no_gate_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-demo"
            result = subprocess.run([sys.executable, "-c", OFFLINE_LAUNCHER, str(SCRIPT), "--output", str(output)],
                cwd=directory, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(status["status"], "completed_synthetic_demo")
            self.assertFalse(status["measured_actuals"])
            manifest = json.loads((output / "demo-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["synthetic"])
            self.assertEqual(manifest["human_review_status"], "pending_human_review")
            self.assertEqual(manifest["ai_domain_labels"], "pending_human_review")
            for key in ("measured_actuals", "signal_eligible", "automatic_activation", "automatic_membership_change",
                        "real_approvals", "prospective_credit", "customer_credit"):
                self.assertIs(manifest[key], False, key)
            self.assertEqual(manifest["network_requests"], 0)
            self.assertEqual(set(manifest["files"]), {path.name for path in output.iterdir()})
            self.assertFalse((output / "blocked-stale-export.json").exists())
            for line in (output / "artifacts.sha256").read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                expected, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), expected, name)

            baseline = json.loads((output / "rights-withheld-baseline.json").read_text(encoding="utf-8"))["payload"]
            self.assertEqual(baseline["cards"], [])
            self.assertEqual(len(baseline["withheld"]), 2)
            self.assertTrue(all("field_rights_pending" in row["reason_codes"] for row in baseline["withheld"]))
            initial = json.loads((output / "reviewed-brief.json").read_text(encoding="utf-8"))
            corrected = json.loads((output / "corrected-brief.json").read_text(encoding="utf-8"))
            for brief in (initial, corrected):
                self.assertEqual(len(brief["cards"]), 2)
                self.assertEqual(brief["evidence_kind"], "synthetic")
                self.assertEqual(brief["ai_domain_labels"], "pending_human_review")
                self.assertFalse(brief["signal_eligible"])
                for card in brief["cards"]:
                    self.assertTrue(card["source_attribution"])
                    self.assertTrue(all(source["url"].startswith("https://omni-demo.example/releases/")
                                        for source in card["source_attribution"]))
            alpha_id = manifest["correction_workflow"]["event_id"]
            corrected_alpha = next(card for card in corrected["cards"] if card["event_id"] == alpha_id)
            self.assertIn("not_met", {claim["value"] for claim in corrected_alpha["claims"]
                                      if claim["predicate"] == "primary_endpoint_status"})
            self.assertTrue(corrected_alpha["correction_refs"])
            for stem in ("reviewed-brief", "corrected-brief"):
                self.assertIn("Synthetic", (output / (stem + ".md")).read_text(encoding="utf-8"))
                self.assertIn("<!doctype html>", (output / (stem + ".html")).read_text(encoding="utf-8"))

            economics = json.loads((output / "economics.json").read_text(encoding="utf-8"))["payload"]
            self.assertEqual((economics["direct_cost"], economics["useful_event_count"], economics["cost_per_useful_event"]),
                             ("86", 2, "43"))
            self.assertEqual(economics["category_costs"]["other"], "-8")
            self.assertEqual(economics["review_effort_seconds"], 2700)
            self.assertEqual(economics["feedback_results"]["not_useful"], 1)
            self.assertFalse(economics["measured_actuals"])
            self.assertEqual(economics["evidence_kind"], "synthetic")
            after = json.loads((output / "economics-after-correction.json").read_text(encoding="utf-8"))["payload"]
            self.assertTrue(after["pending_reasons"])
            self.assertFalse(after["measured_actuals"])
            gates = json.loads((output / "readiness.json").read_text(encoding="utf-8"))["payload"]["report"]
            self.assertTrue(all(gates[key]["status"] == "pending" for key in ("OMNI-10", "OMNI-11", "OMNI-12")))
            self.assertFalse(gates["automatic_activation"])
            self.assertFalse(gates["automatic_membership_change"])

            # Read only: checking references must not mutate the delivered DB or
            # create SQLite sidecars after its checksum has been recorded.
            uri = "file:" + (output / manifest["database"]).as_posix() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                store = SimpleNamespace(conn=connection)
                for ref in list(manifest["receipt_refs"].values()) + manifest["export_refs"]:
                    self.assertEqual(get_receipt(store, ref)["receipt_id"], ref)
                for ref in manifest["document_refs"]:
                    resolved = resolve_reference(store, ref)
                    self.assertEqual(resolved["kind"], "document")
                    self.assertEqual(resolved["document"]["evidence_kind"], "synthetic")
                for ref in manifest["review_refs"]:
                    payload = json.loads(connection.execute("SELECT payload FROM reviews WHERE review_id=?", (ref,)).fetchone()[0])
                    self.assertTrue(payload["reviewer"].startswith("synthetic-"))
                    self.assertIn("synthetic", payload["note"])
                retained = list_receipts(store)
                saved = json.loads((output / "receipts.json").read_text(encoding="utf-8"))["receipts"]
                self.assertEqual(retained, saved)
                for envelope in retained:
                    for ref in envelope["refs"]:
                        get_receipt(store, ref)
                    if envelope["kind"] in {"operator_attestation", "field_rights", "operating_observation"}:
                        self.assertEqual(envelope["payload"]["evidence_kind"], "synthetic")
                feedback = get_receipt(store, manifest["receipt_refs"]["negative_feedback"])["payload"]
                consent = get_receipt(store, manifest["receipt_refs"]["prior_synthetic_consent"])["payload"]
                self.assertLessEqual(consent["reviewed_at"], feedback["session_started_at"])
                self.assertEqual(feedback["consent_ref"], manifest["receipt_refs"]["prior_synthetic_consent"])

            previous = (output / "demo-manifest.json").read_bytes()
            repeat = subprocess.run([sys.executable, str(SCRIPT), "--output", str(output)], cwd=directory,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertIn("must not already exist", repeat.stderr)
            self.assertEqual((output / "demo-manifest.json").read_bytes(), previous)

    def test_api_rejects_existing_or_checkout_output_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                run_demo(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        forbidden = ROOT / "synthetic-demo-output-must-never-be-created"
        self.assertFalse(forbidden.exists())
        with self.assertRaisesRegex(ValueError, "outside the checkout"):
            run_demo(forbidden)
        self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
