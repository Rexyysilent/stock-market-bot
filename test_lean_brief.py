"""The lean brief is a labelled, lean, faithful projection of a brief."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lean_brief import (
    LEAN_BRIEF_CONTRACT, build_lean_brief, main, write_dated_lean_brief,
    write_lean_brief,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "exports" / "daily_brief.valid.json"


class LeanBriefTests(unittest.TestCase):
    def setUp(self):
        self.brief = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_labelled_and_lean(self):
        share = build_lean_brief(self.brief, source_sha256="abc")
        self.assertEqual(share["artifact"]["kind"], "lean_brief")
        self.assertEqual(share["artifact"]["contract"], LEAN_BRIEF_CONTRACT)
        self.assertIn("not for public redistribution", share["artifact"]["label"])
        self.assertEqual(share["artifact"]["source_brief_sha256"], "abc")
        self.assertNotIn("data_quality", share)
        self.assertNotIn("health", share)
        self.assertTrue(share["reading_rules"])

    def test_market_content_is_unchanged(self):
        brief = deepcopy(self.brief)
        brief["conventions"] = {"nan_policy": "null means unknown"}
        share = build_lean_brief(brief)
        for key in ("sections", "summary", "universe", "conventions",
                    "schema_version", "pipeline_version", "generated_at"):
            self.assertEqual(share[key], brief[key], key)
        # Optional blocks absent from the brief are not invented.
        self.assertNotIn("conventions", build_lean_brief(self.brief))

    def test_warnings_survive_but_are_compacted(self):
        brief = deepcopy(self.brief)
        brief["health"]["status"] = "WARN"
        brief["health"]["warnings"] = [
            "OpenInsider timed out: https://openinsider.com/screener?x=1 retry",
            "url: /screener?" + "a=&" * 60,
            "Options flow unavailable for 1/17 expected tickers: STTDF.",
        ]
        brief["health"]["errors"] = ["boom " + "x" * 400]
        summary = build_lean_brief(brief)["health_summary"]
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
        share = build_lean_brief(self.brief)
        share["sections"]["headlines"] = "mutated"
        self.assertEqual(json.dumps(self.brief, sort_keys=True), before)

    def test_write_records_exact_source_bytes_and_is_deterministic(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "share.json"
            write_lean_brief(FIXTURE, out)
            first = out.read_bytes()
            write_lean_brief(FIXTURE, out)
            self.assertEqual(out.read_bytes(), first)
            share = json.loads(first)
            self.assertEqual(
                share["artifact"]["source_brief_sha256"],
                hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            )
            self.assertEqual(main([str(FIXTURE), "-o", str(out)]), 0)
            self.assertLess(out.stat().st_size, FIXTURE.stat().st_size)


class DatedLeanBriefTests(unittest.TestCase):
    def write_lean(self, root, generated_at):
        brief = json.loads(FIXTURE.read_text(encoding="utf-8"))
        brief["generated_at"] = generated_at
        brief_path = root / f"brief-{generated_at[:10]}.json"
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        return write_lean_brief(brief_path, root / "lean_brief.json")

    def test_dated_copy_is_exact_and_named_by_generated_at(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lean = self.write_lean(root, "2026-09-22T10:31:36Z")
            target, pruned = write_dated_lean_brief(lean, root / "lean_briefs")
            self.assertEqual(target.name, "2026-09-22_103136Z.json")
            self.assertEqual(target.read_bytes(), Path(lean).read_bytes())
            self.assertEqual(pruned, [])

    def test_keeps_seven_days_relative_to_the_brief_not_the_clock(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "lean_briefs"
            folder.mkdir()
            for name in ("2026-09-14_235959Z.json",   # older than 7 days: pruned
                         "2026-09-15_103136Z.json",   # exactly 7 days: kept
                         "2026-09-20_080000Z.json",
                         "2026-09-30_080000Z.json",   # later than the brief: kept
                         "notes.txt", "keep-me.json"):  # not ours: untouched
                (folder / name).write_text("{}", encoding="utf-8")
            lean = self.write_lean(root, "2026-09-22T10:31:36Z")
            _, pruned = write_dated_lean_brief(lean, folder)
            self.assertEqual(pruned, ["2026-09-14_235959Z.json"])
            self.assertEqual(sorted(p.name for p in folder.iterdir()), [
                "2026-09-15_103136Z.json", "2026-09-20_080000Z.json",
                "2026-09-22_103136Z.json", "2026-09-30_080000Z.json",
                "keep-me.json", "notes.txt",
            ])

    def test_brief_without_generated_at_writes_no_dated_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lean = root / "lean_brief.json"
            lean.write_text(json.dumps({"artifact": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                write_dated_lean_brief(lean, root / "lean_briefs")
            self.assertFalse((root / "lean_briefs").exists())


if __name__ == "__main__":
    unittest.main()
