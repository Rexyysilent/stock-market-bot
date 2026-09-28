"""T8 issuer-change packet: the six card scenarios, offline, synthetic data only."""
import json
from pathlib import Path
import tempfile
import unittest

import event_history as eh
from change_packet import build_packet, render_note, save_note

PREV = "2026-09-21T15:00:00Z"
CURR = "2026-09-22T22:00:00Z"


def claim(value, unit="USD_million", period="2026-Q2", **extra):
    return dict({"value": value, "unit": unit, "period": period}, **extra)


def price(ticker, value, session, source="yfinance", complete=True, **extra):
    return dict({"ticker": ticker, "price": value, "source_session": session,
                 "session_complete": complete, "source": source}, **extra)


def sec_row(accession, ticker="ALFA", form="8-K", filed_at="2026-09-22T14:00:00.000Z"):
    return {"ticker": ticker, "form_type": form, "description": form, "date": filed_at[:10],
            "filed_at": filed_at, "accession_number": accession,
            "primary_doc_url": f"https://www.sec.gov/Archives/{accession}.htm"}


class PacketCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = eh.connect(self.root / "history.db")
        eh.register_resolver(self.conn, "manual-1", introduced_at="2026-09-01T00:00:00Z",
                             description="manual review")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write(self, name, doc):
        path = self.root / name
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        return path

    def extract(self, name, generated_at, extracts):
        eh.import_archive(self.conn, self.write(name, {
            "artifact": "evidence-extract-1", "generated_at": generated_at,
            "capture_policy": {"recorded": True, "basis": "synthetic permitted extract"},
            "extracts": extracts}))

    def brief(self, name, generated_at, *, sec=(), prices=(), sources=None,
              tickers=("ALFA", "BETA"), index=True):
        doc = {"schema_version": "2.8", "pipeline_version": "2.6.5",
               "generated_at": generated_at,
               "universe": {"name": "synthetic", "version": "1", "tickers": list(tickers),
                            "instrumented_tickers": list(tickers)},
               "health": {"status": "OK", "warnings": [], "errors": [],
                          "sources": sources if sources is not None else {
                              "sec_edgar": {"exceptions": 0, "failed_requests": [],
                                            "no_cik_tickers": []},
                              "news": {"fetch_error": None}}},
               "sections": {"sec_filings": list(sec), "prices": list(prices), "headlines": []},
               "data_quality": {"headline_pool": {}}}
        path = self.write(name, doc)
        if index:
            eh.import_archive(self.conn, path)
        return path

    def version(self, source_key):
        return self.conn.execute("SELECT version_id FROM evidence_versions WHERE source_key=?",
                                 (source_key,)).fetchone()[0]

    def relate(self, source, target, kind, at, **claims):
        return eh.record_evidence_relation(self.conn, self.version(source), self.version(target),
                                           kind, decision=claims.pop("decision", "accepted"),
                                           decision_at=at, rule_version="manual-1", **claims)

    def packet(self, prev, curr, subject="ALFA"):
        return build_packet(self.conn, prev, curr, subject)

    def changes(self, packet, kind):
        return [c for c in packet["changes"] if c["kind"] == kind]


class FilingCoverageAmendmentTests(PacketCase):
    """Card: filing + two derivative reports + scoped amendment."""

    def build(self):
        self.extract("f1.json", "2026-09-21T14:10:00Z", [{
            "source_key": "synthetic:sec:F1", "kind": "filing", "subject_id": "issuer:ALFA",
            "origin_hint": "issuer-disclosure:ALFA:Q2", "published_at": "2026-09-21T14:00:00Z",
            "claims": {"revenue": claim("20"), "eps": claim("1.2", unit="USD_per_share")}}])
        self.extract("n.json", "2026-09-21T14:20:00Z", [
            {"source_key": f"synthetic:news:{n}", "kind": "news", "subject_id": "issuer:ALFA",
             "origin_hint": "issuer-disclosure:ALFA:Q2", "published_at": "2026-09-21T14:15:00Z",
             "claims": {"revenue": claim("20")}} for n in ("N1", "N2")])
        for n in ("N1", "N2"):
            self.relate(f"synthetic:news:{n}", "synthetic:sec:F1", "reproduces",
                        "2026-09-21T14:30:00Z", from_claim="/claims/revenue",
                        to_claim="/claims/revenue")
        self.first = self.brief("brief-0921.json", PREV)
        self.extract("f2.json", "2026-09-22T10:00:00Z", [{
            "source_key": "synthetic:sec:F2", "kind": "filing", "subject_id": "issuer:ALFA",
            "origin_hint": "issuer-disclosure:ALFA:Q2", "published_at": "2026-09-22T09:55:00Z",
            "claims": {"revenue": claim("18")}}])
        self.relate("synthetic:sec:F2", "synthetic:sec:F1", "supersedes_claim",
                    "2026-09-22T10:05:00Z", from_claim="/claims/revenue",
                    to_claim="/claims/revenue")
        self.second = self.brief("brief-0922.json", CURR)

    def test_one_disclosure_origin_with_two_coverage_reports(self):
        self.build()
        empty = self.brief("brief-0920.json", "2026-09-20T22:00:00Z")
        packet = self.packet(empty, self.first)
        revenue = next(c for c in packet["changes"] if c.get("claim") == "revenue")
        self.assertEqual(revenue["kind"], "claim_new")
        self.assertEqual([v["value"] for v in revenue["values"]], ["20"])
        origins = revenue["origins"][0]
        self.assertEqual((origins["primary_origins"], origins["coverage_reports"]), (1, 2))
        self.assertEqual(origins["independence"], "not_assessed")
        # Coverage reports are attached to the claim, not listed as separate developments.
        listed = {s["source_key"] for c in packet["changes"] for s in c["sources"]}
        self.assertNotIn("synthetic:news:N1", listed)
        # Every source is traceable to archive bytes and a JSON pointer.
        source = revenue["sources"][0]
        self.assertEqual(source["source_key"], "synthetic:sec:F1")
        self.assertEqual(source["sightings"][0]["pointer"], "/extracts/0")
        self.assertTrue(source["sightings"][0]["path"].endswith("f1.json"))

    def test_scoped_amendment_changes_revenue_and_keeps_eps_on_the_original(self):
        self.build()
        packet = self.packet(self.first, self.second)
        revenue = next(c for c in packet["changes"] if c.get("claim") == "revenue")
        self.assertEqual(revenue["kind"], "claim_corrected")
        self.assertEqual(revenue["previous_values"], [{"value": "20", "source_key": "synthetic:sec:F1"}])
        self.assertEqual([(v["value"], v["source_key"]) for v in revenue["values"]],
                         [("18", "synthetic:sec:F2")])
        self.assertEqual(revenue["superseded"][0]["source_key"], "synthetic:sec:F1")
        self.assertIn("coverage reports of the superseded value remain historical",
                      " ".join(l["text"] for l in revenue["limitations"]))
        unchanged = {u["claim"]: u for u in packet["unchanged_claims"]}
        self.assertEqual([v["source_key"] for v in unchanged["eps"]["values"]], ["synthetic:sec:F1"])
        self.assertFalse(any(c.get("claim") == "eps" for c in packet["changes"]))

    def test_later_evidence_does_not_change_an_earlier_packet(self):
        self.build()
        empty = self.brief("brief-0920.json", "2026-09-20T22:00:00Z")
        before = json.dumps(self.packet(empty, self.first), sort_keys=True)
        self.extract("late.json", "2026-09-23T09:00:00Z", [{
            "source_key": "synthetic:news:N3", "kind": "news", "subject_id": "issuer:ALFA",
            "claims": {"revenue": claim("25")}}])
        # The same filing captured again later adds a sighting, not earlier knowledge.
        f1 = json.loads((self.root / "f1.json").read_text(encoding="utf-8"))
        f1["generated_at"] = "2026-09-24T09:00:00Z"
        eh.import_archive(self.conn, self.write("f1-again.json", f1))
        self.assertEqual(json.dumps(self.packet(empty, self.first), sort_keys=True), before)


class SameDayTests(PacketCase):
    """Card: unrelated same-day developments stay separate."""

    def test_two_same_day_filings_are_two_changes(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sec=[
            sec_row("0000000001-26-000101"),
            sec_row("0000000001-26-000102", form="4", filed_at="2026-09-22T15:30:00.000Z")])
        records = self.changes(self.packet(prev, curr), "new_record")
        self.assertEqual(sorted(c["sources"][0]["source_key"] for c in records),
                         ["sec_edgar:0000000001-26-000101", "sec_edgar:0000000001-26-000102"])
        for change in records:
            self.assertEqual(len(change["sources"]), 1)
            self.assertIn("symbol", change["sources"][0]["subject_match"])

    def test_other_issuers_are_excluded(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sec=[sec_row("0000000002-26-000201", ticker="BETA")])
        packet = self.packet(prev, curr)
        self.assertEqual(packet["changes"], [])
        self.assertIn("No additional matching records were observed in this captured scope",
                      packet["scope_statement"])
        self.assertNotIn("nothing happened", packet["scope_statement"].lower())


class RegulatoryConflictTests(PacketCase):
    """Card: conflicting regulatory report; a scheduled meeting is not a decision."""

    def test_conflict_keeps_both_attributions_and_scheduled_date(self):
        prev = self.brief("prev.json", PREV)
        self.extract("reg.json", "2026-09-22T12:00:00Z", [
            {"source_key": "synthetic:fda:notice", "kind": "notice", "subject_id": "issuer:ALFA",
             "published_at": "2026-09-22T11:00:00Z", "event_date": "2026-10-12",
             "title": "Advisory committee meeting scheduled"},
            {"source_key": "synthetic:news:approval", "kind": "news", "subject_id": "issuer:ALFA",
             "published_at": "2026-09-22T11:30:00Z", "title": "Report: approval granted"}])
        self.relate("synthetic:news:approval", "synthetic:fda:notice", "contradicts",
                    "2026-09-22T12:30:00Z")
        curr = self.brief("curr.json", CURR)
        packet = self.packet(prev, curr)
        conflict = self.changes(packet, "conflict")[0]
        self.assertEqual(sorted(s["source_key"] for s in conflict["sources"]),
                         ["synthetic:fda:notice", "synthetic:news:approval"])
        self.assertNotIn("resolved", json.dumps(conflict))
        scheduled = next(c for c in packet["changes"]
                         if any(l["code"] == "scheduled_not_occurred" for l in c["limitations"]))
        self.assertEqual(scheduled["sources"][0]["source_key"], "synthetic:fda:notice")
        needs = " ".join(n for c in packet["changes"] for n in c["next_evidence"])
        self.assertIn("decision", needs)

    def test_candidate_link_is_labelled_unreviewed(self):
        prev = self.brief("prev.json", PREV)
        self.extract("reg.json", "2026-09-22T12:00:00Z", [
            {"source_key": "synthetic:fda:notice", "kind": "notice", "subject_id": "issuer:ALFA"},
            {"source_key": "synthetic:news:approval", "kind": "news", "subject_id": "issuer:ALFA"}])
        self.relate("synthetic:news:approval", "synthetic:fda:notice", "contradicts",
                    "2026-09-22T12:30:00Z", decision="candidate")
        conflict = self.changes(self.packet(prev, self.brief("curr.json", CURR)), "conflict")[0]
        self.assertEqual(conflict["decision"], "candidate")
        self.assertIn("candidate_link", [l["code"] for l in conflict["limitations"]])


class TimingTests(PacketCase):
    """Card: unknown event time abstains from session placement."""

    def test_day_only_and_unknown_event_times_are_limitations(self):
        prev = self.brief("prev.json", PREV)
        self.extract("t.json", "2026-09-22T12:00:00Z", [
            {"source_key": "synthetic:notice:day", "kind": "notice", "subject_id": "issuer:ALFA",
             "event_date": "2026-09-22"},
            {"source_key": "synthetic:news:undated", "kind": "news", "subject_id": "issuer:ALFA"}])
        packet = self.packet(prev, self.brief("curr.json", CURR))
        codes = {c["sources"][0]["source_key"]: {l["code"] for l in c["limitations"]}
                 for c in self.changes(packet, "new_record")}
        self.assertIn("event_time_day_only", codes["synthetic:notice:day"])
        self.assertIn("timing_unknown", codes["synthetic:news:undated"])
        self.assertIn("no_retained_claims", codes["synthetic:news:undated"])


class PriceContextTests(PacketCase):
    """Card: mixed price bases give no comparison; compatible ones are descriptive only."""

    def test_compatible_rows_give_a_descriptive_change(self):
        prev = self.brief("prev.json", PREV, prices=[price("ALFA", 100.0, "2026-09-21")])
        curr = self.brief("curr.json", CURR, prices=[price("ALFA", 104.0, "2026-09-22")])
        context = self.packet(prev, curr)["price_context"]
        self.assertEqual(context["status"], "descriptive")
        self.assertAlmostEqual(context["change_pct"], 4.0)
        self.assertIn("not attributed", context["label"])

    def test_mixed_sources_or_bases_are_unavailable(self):
        prev = self.brief("prev.json", PREV, prices=[price("ALFA", 100.0, "2026-09-21")])
        curr = self.brief("curr.json", CURR, prices=[price("ALFA", 50.0, "2026-09-22",
                                                           source="other_vendor")])
        context = self.packet(prev, curr)["price_context"]
        self.assertEqual((context["status"], context["reason"]), ("unavailable", "mixed_price_bases"))
        self.assertIsNone(context["change_pct"])
        prev2 = self.brief("prev2.json", PREV, prices=[
            price("ALFA", 100.0, "2026-09-21", adjustment_basis="raw")])
        curr2 = self.brief("curr2.json", CURR, prices=[
            price("ALFA", 50.0, "2026-09-22", adjustment_basis="split_adjusted")])
        self.assertEqual(self.packet(prev2, curr2)["price_context"]["reason"], "mixed_price_bases")

    def test_incomplete_or_missing_rows_are_unavailable(self):
        prev = self.brief("prev.json", PREV, prices=[price("ALFA", 100.0, "2026-09-21")])
        curr = self.brief("curr.json", CURR, prices=[price("ALFA", 104.0, "2026-09-22",
                                                           complete=False)])
        self.assertEqual(self.packet(prev, curr)["price_context"]["reason"], "session_incomplete")
        curr2 = self.brief("curr2.json", CURR)
        self.assertEqual(self.packet(prev, curr2)["price_context"]["reason"], "price_missing")


class MissingProviderTests(PacketCase):
    """Card: a missing provider is a coverage gap, never 'no event'."""

    def test_failed_sec_source_is_a_gap_in_the_scope_statement(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sources={
            "sec_edgar": {"exceptions": 2, "failed_requests": ["x"], "no_cik_tickers": []},
            "news": {"fetch_error": None}})
        packet = self.packet(prev, curr)
        self.assertEqual(packet["changes"], [])
        self.assertEqual(packet["coverage"]["sources"]["sec_edgar"], "degraded")
        self.assertIn("sec_edgar", packet["scope_statement"])
        self.assertIn("not evidence that nothing happened", packet["scope_statement"])

    def test_absent_source_and_subject_without_cik(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sources={
            "sec_edgar": {"exceptions": 0, "failed_requests": [], "no_cik_tickers": ["ALFA"]}})
        coverage = self.packet(prev, curr)["coverage"]["sources"]
        self.assertEqual(coverage["sec_edgar"], "no_cik_for_subject")
        self.assertEqual(coverage["news"], "absent")


class ComparabilityTests(PacketCase):
    def test_unindexed_brief_and_universe_change_are_reported(self):
        prev = self.brief("prev.json", PREV, tickers=("ALFA",))
        curr = self.brief("curr.json", CURR, index=False)
        comparability = self.packet(prev, curr)["comparability"]
        self.assertFalse(comparability["configuration_comparable"])
        self.assertEqual(comparability["indexed"], {"previous": True, "current": False})
        self.assertIn("current brief is not in the evidence history",
                      " ".join(comparability["warnings"]))

    def test_briefs_out_of_order_are_refused(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR)
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.packet(curr, prev)


class NoteTests(PacketCase):
    def packet_with_change(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sec=[sec_row("0000000001-26-000101")])
        return self.packet(prev, curr)

    def test_note_has_the_four_parts(self):
        note = render_note(self.packet_with_change())
        for part in ("What changed", "Source/version", "Limitation/conflict",
                     "Next evidence needed"):
            self.assertIn(part, note)
        self.assertIn("/sections/sec_filings/0", note)
        self.assertIn("not investment advice", note)

    def test_save_needs_an_explicit_new_destination(self):
        packet = self.packet_with_change()
        target = self.root / "notes" / "alfa.md"
        with self.assertRaises(ValueError):
            save_note(packet, None)
        self.assertEqual(save_note(packet, target), target)
        self.assertIn("What changed", target.read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            save_note(packet, target)
        save_note(packet, target, overwrite=True)
        as_json = save_note(packet, self.root / "alfa.json")
        self.assertEqual(json.loads(as_json.read_text(encoding="utf-8"))["contract"],
                         "change-packet-1")


class CliTests(PacketCase):
    def run_cli(self, *args):
        import contextlib
        import io
        from marketbot import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["packet", *map(str, args)])
        return code, out.getvalue()

    def test_packet_prints_json_or_note_and_saves_on_request(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR, sec=[sec_row("0000000001-26-000101")])
        history = self.root / "history.db"
        code, text = self.run_cli(prev, curr, "--subject", "ALFA", "--history", history)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["contract"], "change-packet-1")
        code, text = self.run_cli(prev, curr, "--subject", "ALFA", "--history", history,
                                  "--note")
        self.assertIn("## 1. What changed", text)
        target = self.root / "out" / "alfa.md"
        code, text = self.run_cli(prev, curr, "--subject", "ALFA", "--history", history,
                                  "--save", target)
        self.assertTrue(target.exists())
        self.assertIn(str(target), text)

    def test_missing_history_is_refused_without_creating_one(self):
        prev = self.brief("prev.json", PREV)
        curr = self.brief("curr.json", CURR)
        absent = self.root / "nowhere" / "history.db"
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(prev, curr, "--subject", "ALFA", "--history", absent)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(absent.parent.exists())


if __name__ == "__main__":
    unittest.main()
