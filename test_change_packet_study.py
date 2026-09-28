"""T8 study kit: matched synthetic cases, answer-key agreement and gate scoring."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import event_history as eh
from change_packet import build_packet
from change_packet_study import CASES_DIR, build_case, score_sheet

ROOT = Path(__file__).resolve().parent


class CaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def packet(self, case):
        built = build_case(CASES_DIR / case, self.root / case)
        conn = eh.connect(built["history"])
        try:
            return built, build_packet(conn, built["previous"], built["current"], built["subject"])
        finally:
            conn.close()

    def key(self, case):
        return json.loads((CASES_DIR / case / "answer_key.json").read_text(encoding="utf-8"))

    def test_each_packet_agrees_with_its_answer_key(self):
        for case in ("case_a", "case_b"):
            with self.subTest(case=case):
                _, packet = self.packet(case)
                checks = self.key(case)["machine_checks"]
                corrected = next(c for c in packet["changes"] if c["kind"] == "claim_corrected")
                self.assertEqual(
                    (corrected["claim"], corrected["previous_values"][0]["value"],
                     corrected["values"][0]["value"], corrected["values"][0]["source_key"]),
                    tuple(checks["corrected"][k] for k in ("claim", "from", "to", "source_key")))
                unchanged = {u["claim"]: u for u in packet["unchanged_claims"]}
                claim = checks["unchanged"]["claim"]
                self.assertEqual(unchanged[claim]["values"][0]["value"], checks["unchanged"]["value"])
                self.assertEqual(unchanged[claim]["values"][0]["source_key"],
                                 checks["unchanged"]["source_key"])
                superseded_origin = next(c for c in packet["changes"]
                                         if c["kind"] == "claim_corrected")["superseded"][0]
                self.assertEqual(superseded_origin["source_key"], checks["corrected"]["superseded_source_key"])
                conflict = next(c for c in packet["changes"] if c["kind"] == "conflict")
                self.assertEqual(sorted(s["source_key"] for s in conflict["sources"]),
                                 sorted(checks["conflict"]))
                notice = next(c for c in packet["changes"] if c["kind"] == "new_record"
                              and c["sources"][0]["source_key"] == checks["notice"]["source_key"])
                self.assertTrue(set(checks["notice"]["limitations"])
                                <= {l["code"] for l in notice["limitations"]})
                self.assertEqual(packet["coverage"]["sources"][checks["gap"]["source"]],
                                 checks["gap"]["state"])
                self.assertIn("not evidence that nothing happened", packet["scope_statement"])
                self.assertEqual(packet["price_context"]["status"], "descriptive")
                self.assertAlmostEqual(packet["price_context"]["change_pct"], checks["price_change_pct"])
                self.assertTrue(packet["comparability"]["configuration_comparable"],
                                packet["comparability"])

    def test_origins_before_the_amendment_are_one_disclosure_and_two_reports(self):
        for case in ("case_a", "case_b"):
            with self.subTest(case=case):
                built, _ = self.packet(case)
                conn = eh.connect(built["history"])
                try:
                    view = eh.knowledge_view(conn, self.key(case)["machine_checks"]["origin_cutoff"])
                finally:
                    conn.close()
                claim = self.key(case)["machine_checks"]["corrected"]["claim"]
                ref = next(v["attributed_to"] for c in view["claims"] if c["claim"] == claim
                           for v in c["values"])
                summary = eh.origin_summary(view, ref)
                self.assertEqual((summary["primary_origins"], summary["coverage_reports"]), (1, 2))

    def test_cases_are_structurally_matched(self):
        shapes = []
        for case in ("case_a", "case_b"):
            _, packet = self.packet(case)
            shapes.append(sorted(c["kind"] for c in packet["changes"]))
        self.assertEqual(shapes[0], shapes[1])
        self.assertEqual(shapes[0], ["claim_corrected", "conflict", "new_record", "new_record"])

    def test_participant_folder_has_sources_but_never_the_answer_key(self):
        built = build_case(CASES_DIR / "case_a", self.root / "a")
        names = {p.name for p in (self.root / "a").rglob("*")}
        self.assertNotIn("answer_key.json", names)
        self.assertNotIn("answer", " ".join(names).lower())
        self.assertTrue(Path(built["previous"]).is_file() and Path(built["current"]).is_file())
        self.assertIn("packet", (self.root / "a" / "README.txt").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            build_case(CASES_DIR / "case_a", self.root / "a")

    def test_case_briefs_are_schema_valid_synthetic_demos(self):
        built = build_case(CASES_DIR / "case_b", self.root / "b")
        for path in (built["previous"], built["current"]):
            brief = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertIs(brief["demo"], True)
            self.assertIn("SYNTHETIC", brief["health"]["warnings"][0])
            subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_export_schema.py"),
                            str(path)], check=True, timeout=30, capture_output=True)


def row(participant, case, condition, minutes, correct=True, critical=False, order=1):
    return {"participant_id": participant, "role": "issuer_researcher", "consent_recorded": "yes",
            "order_position": str(order), "case": case, "condition": condition,
            "minutes": str(minutes), "completed_unaided": "yes" if correct else "no",
            "all_four_correct": "yes" if correct else "no",
            "critical_false_conclusion": "yes" if critical else "no",
            "critical_attributable_to_interface": "yes" if critical else "no",
            "help_requests": "0", "citation_retraced": "yes",
            "missed_critical_caveats": "0", "incorrect_assertions": "0",
            "install_friction_minutes": "", "notes": ""}


class ScoreTests(unittest.TestCase):
    def sheet(self, rows):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="",
                                          encoding="utf-8")
        with tmp:
            writer = csv.DictWriter(tmp, fieldnames=list(rows[0]) if rows else ["participant_id"])
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def full(self, packet_minutes, manual_minutes, correct=(True,) * 6, critical=(False,) * 6):
        rows = []
        for i in range(6):
            rows.append(row(f"P{i + 1}", "A", "packet", packet_minutes[i], correct[i], critical[i]))
            rows.append(row(f"P{i + 1}", "B", "manual", manual_minutes[i], order=2))
        return rows

    def test_template_and_partial_sheets_are_pending(self):
        template = ROOT / "docs" / "study" / "recording_sheet.csv"
        self.assertEqual(score_sheet(template)["overall"], "pending")
        partial = self.full([10] * 6, [14] * 6)[:6]
        result = score_sheet(self.sheet(partial))
        self.assertEqual(result["overall"], "pending")
        self.assertIn("6 participants", result["reason"])

    def test_passing_study(self):
        result = score_sheet(self.sheet(self.full([10, 9, 11, 12, 10, 8], [14, 13, 15, 14, 16, 12])))
        self.assertEqual(result["gates"]["unaided_completion"]["status"], "pass")
        self.assertEqual(result["gates"]["median_time_reduction"]["status"], "pass")
        self.assertEqual(result["gates"]["no_critical_interface_errors"]["status"], "pass")
        self.assertEqual(result["overall"], "pass")

    def test_each_gate_can_fail(self):
        slow = score_sheet(self.sheet(self.full([13] * 6, [14] * 6)))
        self.assertEqual(slow["gates"]["median_time_reduction"]["status"], "fail")
        few = score_sheet(self.sheet(self.full([10] * 6, [14] * 6,
                                               correct=(True, True, True, False, False, False))))
        self.assertEqual(few["gates"]["unaided_completion"]["status"], "fail")
        critical = score_sheet(self.sheet(self.full([10] * 6, [14] * 6,
                                                    critical=(True,) + (False,) * 5)))
        self.assertEqual(critical["gates"]["no_critical_interface_errors"]["status"], "fail")
        self.assertEqual(critical["overall"], "fail")

    def test_over_fifteen_minutes_is_not_a_completion(self):
        result = score_sheet(self.sheet(self.full([16, 16, 16, 10, 10, 10], [20] * 6)))
        self.assertEqual(result["gates"]["unaided_completion"]["count"], 3)
        self.assertEqual(result["gates"]["unaided_completion"]["status"], "fail")

    def test_rows_without_consent_are_excluded(self):
        rows = self.full([10] * 6, [14] * 6)
        rows[0]["consent_recorded"] = "no"
        result = score_sheet(self.sheet(rows))
        self.assertEqual(result["overall"], "pending")
        self.assertEqual(result["excluded_without_consent"], 1)


if __name__ == "__main__":
    unittest.main()
