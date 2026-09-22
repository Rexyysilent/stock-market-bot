"""The model-share file is a labelled, lean, faithful projection of a brief."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from model_share import (
    MODEL_SHARE_CONTRACT, build_model_share, main, write_model_share,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "exports" / "daily_brief.valid.json"


class ModelShareTests(unittest.TestCase):
    def setUp(self):
        self.brief = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_labelled_and_lean(self):
        share = build_model_share(self.brief, source_sha256="abc")
        self.assertEqual(share["artifact"]["kind"], "model_share_projection")
        self.assertEqual(share["artifact"]["contract"], MODEL_SHARE_CONTRACT)
        self.assertIn("not for public redistribution", share["artifact"]["label"])
        self.assertEqual(share["artifact"]["source_brief_sha256"], "abc")
        self.assertNotIn("data_quality", share)
        self.assertNotIn("health", share)
        self.assertTrue(share["reading_rules"])

    def test_market_content_is_unchanged(self):
        brief = deepcopy(self.brief)
        brief["conventions"] = {"nan_policy": "null means unknown"}
        share = build_model_share(brief)
        for key in ("sections", "summary", "universe", "conventions",
                    "schema_version", "pipeline_version", "generated_at"):
            self.assertEqual(share[key], brief[key], key)
        # Optional blocks absent from the brief are not invented.
        self.assertNotIn("conventions", build_model_share(self.brief))

    def test_warnings_survive_but_are_compacted(self):
        brief = deepcopy(self.brief)
        brief["health"]["status"] = "WARN"
        brief["health"]["warnings"] = [
            "OpenInsider timed out: https://openinsider.com/screener?x=1 retry",
            "url: /screener?" + "a=&" * 60,
            "Options flow unavailable for 1/17 expected tickers: STTDF.",
        ]
        brief["health"]["errors"] = ["boom " + "x" * 400]
        summary = build_model_share(brief)["health_summary"]
        self.assertEqual(summary["status"], "WARN")
        self.assertEqual(len(summary["warnings"]), 3)
        self.assertEqual(summary["warnings"][0],
                         "OpenInsider timed out: <url> retry")
        self.assertEqual(summary["warnings"][1], "url: <long>")
        self.assertEqual(summary["warnings"][2],
                         "Options flow unavailable for 1/17 expected tickers: STTDF.")
        self.assertLessEqual(len(summary["errors"][0]), 240)

    def test_input_is_not_modified(self):
        before = json.dumps(self.brief, sort_keys=True)
        share = build_model_share(self.brief)
        share["sections"]["headlines"] = "mutated"
        self.assertEqual(json.dumps(self.brief, sort_keys=True), before)

    def test_write_records_exact_source_bytes_and_is_deterministic(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "share.json"
            write_model_share(FIXTURE, out)
            first = out.read_bytes()
            write_model_share(FIXTURE, out)
            self.assertEqual(out.read_bytes(), first)
            share = json.loads(first)
            self.assertEqual(
                share["artifact"]["source_brief_sha256"],
                hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            )
            self.assertEqual(main([str(FIXTURE), "-o", str(out)]), 0)
            self.assertLess(out.stat().st_size, FIXTURE.stat().st_size)


if __name__ == "__main__":
    unittest.main()
