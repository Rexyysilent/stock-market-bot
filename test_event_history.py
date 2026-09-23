"""T7 rebuildable evidence history: E01-E08 and full M15/M16, network-free."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from agents.news_providers import ProviderResult
import evidence_intake
import event_history as eh
from event_history import store

OPEN_0921 = "2026-09-21T13:30:00Z"


def sec_row(accession, ticker="ALFA", form="8-K", filed_at="2026-09-21T14:00:00.000Z",
            day="2026-09-21"):
    return {"ticker": ticker, "form_type": form, "description": form, "date": day,
            "filed_at": filed_at, "accession_number": accession,
            "primary_doc_url": f"https://www.sec.gov/Archives/{accession}.htm",
            "link": f"https://www.sec.gov/Archives/{accession}.htm",
            "source": "SEC EDGAR", "record_id": accession[-6:], "as_of": filed_at}


def brief(generated_at, sec=(), schema="2.8", intake=None):
    doc = {"schema_version": schema, "pipeline_version": "2.6.5",
           "generated_at": generated_at, "sections": {"sec_filings": list(sec)},
           "data_quality": {"headline_pool": {}}}
    if intake is not None:
        doc["data_quality"]["headline_pool"]["evidence_intake"] = intake
    return doc


def extract(generated_at, extracts, policy=None):
    return {"artifact": "evidence-extract-1", "generated_at": generated_at,
            "capture_policy": policy or {"recorded": True, "basis": "synthetic permitted extract"},
            "extracts": extracts}


def claim(value, unit="USD_million", period="2026-Q2", **extra):
    return dict({"value": value, "unit": unit, "period": period}, **extra)


class HistoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = eh.connect(self.root / "history.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write(self, name, doc):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        return path

    def load(self, name, doc):
        return eh.import_archive(self.conn, self.write(name, doc))

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def version(self, source_key):
        return self.conn.execute("SELECT version_id FROM evidence_versions WHERE source_key=?",
                                 (source_key,)).fetchone()[0]

    def resolver(self, name="manual-1", at="2026-09-01T00:00:00Z"):
        return eh.register_resolver(self.conn, name, introduced_at=at, description=name)


class ArchiveIdentityTests(HistoryCase):
    def test_e01_same_bytes_at_two_paths_are_one_archive(self):
        doc = brief("2026-09-21T15:00:00Z", [sec_row("0000000001-26-000001")])
        first = self.load("a/one.json", doc)
        second = self.load("b/two.json", doc)
        self.assertEqual((first["status"], second["status"]), ("indexed", "duplicate"))
        self.assertEqual(first["archive_sha"], second["archive_sha"])
        self.assertEqual((self.count("archives"), self.count("archive_paths"),
                          self.count("sightings"), self.count("evidence_versions")), (1, 2, 1, 1))

    def test_import_is_idempotent_and_rolls_back_on_failure(self):
        doc = brief("2026-09-21T15:00:00Z", [sec_row("0000000001-26-000001"),
                                             sec_row("0000000001-26-000002")])
        path = self.write("x.json", doc)
        calls = []

        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("simulated crash")
            return real(*args)
        real = store._insert_sighting
        with mock.patch.object(store, "_insert_sighting", fail_second):
            with self.assertRaises(RuntimeError):
                eh.import_archive(self.conn, path)
        self.assertEqual([self.count(t) for t in ("archives", "sightings", "evidence_versions")],
                         [0, 0, 0])
        self.assertEqual(eh.import_archive(self.conn, path)["sightings"], 2)
        self.assertEqual(eh.import_archive(self.conn, path)["status"], "duplicate")
        self.assertEqual((self.count("sightings"), self.count("archive_paths")), (2, 1))

    def test_unknown_schema_and_unreadable_bytes_are_quarantined(self):
        future = self.load("future.json", brief("2026-09-21T15:00:00Z",
                                                [sec_row("0000000001-26-000001")], schema="3.1"))
        broken = self.root / "broken.json"
        broken.write_bytes(b"{not json")
        unreadable = eh.import_archive(self.conn, broken)
        self.assertEqual((future["status"], future["reason"]), ("quarantined", "unsupported_schema:'3.1'"))
        self.assertEqual((unreadable["status"], unreadable["reason"]), ("quarantined", "unreadable_json"))
        self.assertEqual(self.count("sightings"), 0)
        self.assertEqual(eh.exact_archive(self.conn, future["archive_sha"])["status"], "quarantined")

    def test_legacy_zone_less_time_is_not_strict_knowledge(self):
        legacy = brief("2026-06-11T09:29:26.658224", [sec_row("0000000001-26-000001")], schema="2.0")
        report = self.load("legacy.json", legacy)
        row = self.conn.execute("SELECT observed_at, knowledge_quality, adapter FROM archives").fetchone()
        self.assertEqual(tuple(row), (None, "legacy_inferred", "legacy-2.0"))
        self.assertEqual(report["sightings"], 1)
        self.assertEqual(eh.knowledge_view(self.conn, "2026-12-31T00:00:00Z")["evidence"], [])
        loose = eh.knowledge_view(self.conn, "2026-12-31T00:00:00Z", include_legacy=True)
        self.assertFalse(loose["strict"])
        self.assertEqual(loose["evidence"][0]["knowledge"], "legacy_unknown")

    def test_exact_archive_verifies_bytes_and_pointers(self):
        path = self.write("x.json", brief("2026-09-21T15:00:00Z", [sec_row("0000000001-26-000001")]))
        sha = eh.import_archive(self.conn, path)["archive_sha"]
        exact = eh.exact_archive(self.conn, sha, path)
        self.assertTrue(exact["bytes_verified"] and exact["sightings_verified"])
        self.assertEqual(exact["sightings"][0]["pointer"], "/sections/sec_filings/0")
        path.write_text(path.read_text(encoding="utf-8").replace("8-K", "8-K/A"), encoding="utf-8")
        self.assertFalse(eh.exact_archive(self.conn, sha, path)["bytes_verified"])

    def test_history_rows_are_append_only(self):
        self.load("x.json", brief("2026-09-21T15:00:00Z", [sec_row("0000000001-26-000001")]))
        for statement in ("UPDATE sightings SET observed_at=NULL", "DELETE FROM archives",
                          "UPDATE evidence_versions SET kind='news'"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute(statement)


class EventTests(HistoryCase):
    def test_e02_same_issuer_same_day_filings_stay_separate(self):
        self.load("x.json", brief("2026-09-21T20:00:00Z", [
            sec_row("0000000001-26-000001", form="8-K"),
            sec_row("0000000001-26-000002", form="8-K", filed_at="2026-09-21T16:00:00.000Z")]))
        eh.anchor_filings(self.conn, decided_at="2026-09-22T00:00:00Z")
        view = eh.knowledge_view(self.conn, "2026-09-23T00:00:00Z")
        self.assertEqual(len(view["events"]), 2)
        self.assertTrue(all(len(e["members"]) == 1 and not e["links"] for e in view["events"]))
        # Anchoring again is idempotent.
        self.assertEqual(eh.anchor_filings(self.conn, decided_at="2026-09-22T00:00:00Z")["new_memberships"], 0)

    def test_e07_provisional_link_is_appended_and_cycles_refused(self):
        self.resolver()
        self.load("x.json", brief("2026-09-21T20:00:00Z", [sec_row("0000000001-26-000001")]))
        eh.anchor_filings(self.conn, decided_at="2026-09-22T00:00:00Z")
        filing = self.conn.execute("SELECT event_id FROM events").fetchone()[0]
        provisional = eh.create_event(self.conn, "provisional:N1", event_kind="reported_development",
                                      created_at="2026-09-21T15:00:00Z", rule_version="manual-1",
                                      provisional=True)
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM events ORDER BY event_id")]
        eh.record_event_relation(self.conn, provisional, filing, "merge_into", decision="accepted",
                                 decision_at="2026-09-22T01:00:00Z", rule_version="manual-1")
        self.assertEqual(before, [tuple(r) for r in self.conn.execute("SELECT * FROM events ORDER BY event_id")])
        with self.assertRaisesRegex(ValueError, "cycle"):
            eh.record_event_relation(self.conn, filing, provisional, "alias_of", decision="accepted",
                                     decision_at="2026-09-22T02:00:00Z", rule_version="manual-1")
        third = eh.create_event(self.conn, "provisional:N2", event_kind="reported_development",
                                created_at="2026-09-21T15:00:00Z", rule_version="manual-1", provisional=True)
        eh.record_event_relation(self.conn, filing, third, "alias_of", decision="accepted",
                                 decision_at="2026-09-22T02:00:00Z", rule_version="manual-1")
        with self.assertRaisesRegex(ValueError, "cycle"):  # multi-hop: N2 -> N1 -> filing -> N2
            eh.record_event_relation(self.conn, third, provisional, "merge_into", decision="accepted",
                                     decision_at="2026-09-22T03:00:00Z", rule_version="manual-1")
        link = eh.knowledge_view(self.conn, "2026-09-23T00:00:00Z")
        provisional_view = next(e for e in link["events"] if e["event_id"] == provisional)
        self.assertEqual(provisional_view["links"][0]["type"], "merge_into")
        self.assertEqual(provisional_view["links"][0]["rule_version"], "manual-1")


class ClaimTests(HistoryCase):
    def amended_filing(self):
        self.resolver()
        self.load("f1.json", extract("2026-09-21T14:10:00Z", [{
            "source_key": "synthetic:sec:F1", "kind": "filing", "subject_id": "issuer:ALFA",
            "native_evidence_id": "filing:F1", "origin_hint": "issuer-disclosure:ALFA:Q2",
            "published_at": "2026-09-21T14:00:00Z",
            "claims": {"revenue": claim("20"), "eps": claim("1.2", unit="USD_per_share")}}]))
        self.f1 = self.version("synthetic:sec:F1")

    def add_amendment(self):
        self.load("f2.json", extract("2026-09-22T10:00:00Z", [{
            "source_key": "synthetic:sec:F2", "kind": "filing", "subject_id": "issuer:ALFA",
            "native_evidence_id": "filing:F2", "origin_hint": "issuer-disclosure:ALFA:Q2",
            "published_at": "2026-09-22T09:55:00Z", "claims": {"revenue": claim("18")}}]))
        self.f2 = self.version("synthetic:sec:F2")
        eh.record_evidence_relation(self.conn, self.f2, self.f1, "supersedes_claim",
                                    from_claim="/claims/revenue", to_claim="/claims/revenue",
                                    decision="accepted", decision_at="2026-09-22T10:05:00Z",
                                    rule_version="manual-1")

    def claims(self, cutoff):
        return {c["claim"]: c for c in eh.knowledge_view(self.conn, cutoff)["claims"]}

    def test_e03_scoped_amendment_keeps_unchanged_claims_on_original(self):
        self.amended_filing()
        self.add_amendment()
        before = self.claims("2026-09-21T15:00:00Z")
        self.assertEqual(before["revenue"]["values"],
                         [{"value": "20", "attributed_to": f"{self.f1}#/claims/revenue"}])
        after = self.claims("2026-09-22T22:00:00Z")
        self.assertEqual(after["revenue"]["status"], "corrected")
        self.assertEqual(after["revenue"]["values"],
                         [{"value": "18", "attributed_to": f"{self.f2}#/claims/revenue"}])
        self.assertEqual(after["revenue"]["superseded"][0]["superseded_by"], f"{self.f2}#/claims/revenue")
        self.assertEqual(after["eps"]["values"],
                         [{"value": "1.2", "attributed_to": f"{self.f1}#/claims/eps"}])

    def test_e04_contradiction_is_retained_without_vote(self):
        self.load("c.json", extract("2026-09-21T14:10:00Z", [
            {"source_key": "synthetic:notice:A", "kind": "notice", "subject_id": "issuer:ALFA",
             "claims": {"revenue": claim("18")}},
            {"source_key": "synthetic:notice:B", "kind": "notice", "subject_id": "issuer:ALFA",
             "claims": {"revenue": claim("20")}}]))
        revenue = self.claims("2026-09-22T00:00:00Z")["revenue"]
        self.assertEqual(revenue["status"], "conflicting")
        self.assertEqual(sorted(v["value"] for v in revenue["values"]), ["18", "20"])
        self.assertEqual(set(revenue), {"subject_id", "claim", "period", "unit", "values",
                                        "superseded", "status"})

    def test_e05_unknown_event_timezone_abstains(self):
        self.load("t.json", extract("2026-09-21T14:10:00Z", [
            {"source_key": "synthetic:notice:day", "kind": "notice", "event_date": "2026-09-21"},
            {"source_key": "synthetic:notice:exact", "kind": "notice",
             "event_at": "2026-09-21T12:00:00Z"}]))
        evidence = {e["source_key"]: e for e in
                    eh.knowledge_view(self.conn, "2026-09-22T00:00:00Z")["evidence"]}
        day = evidence["synthetic:notice:day"]
        self.assertEqual(day["event"]["precision"], "day")
        self.assertNotIn(day["event"]["lo"], ("2026-09-21T00:00:00.000000Z",))
        self.assertEqual(eh.time_relation(day, OPEN_0921), "undetermined")
        self.assertEqual(eh.time_relation(evidence["synthetic:notice:exact"], OPEN_0921), "before")

    def test_e06_late_correction_leaves_old_view_bytes_unchanged(self):
        self.amended_filing()
        cutoff = "2026-09-21T15:00:00Z"
        old = eh.view_digest(eh.knowledge_view(self.conn, cutoff))
        self.add_amendment()
        self.assertEqual(eh.view_digest(eh.knowledge_view(self.conn, cutoff)), old)
        self.assertEqual(self.claims("2026-09-22T22:00:00Z")["revenue"]["status"], "corrected")

    def test_e08_later_resolver_is_retrospective_only(self):
        self.amended_filing()
        self.add_amendment()
        eh.register_resolver(self.conn, "linker-2", introduced_at="2026-09-25T00:00:00Z",
                             description="later linker")
        eh.record_evidence_relation(self.conn, self.f2, self.f1, "amends", decision="accepted",
                                    decision_at="2026-09-25T01:00:00Z", rule_version="linker-2")
        # An existing resolver's decision made after the cutoff is not earlier knowledge either.
        eh.record_evidence_relation(self.conn, self.f2, self.f1, "resolves", decision="accepted",
                                    decision_at="2026-09-24T00:00:00Z", rule_version="manual-1")
        cutoff = "2026-09-23T00:00:00Z"
        operated = eh.knowledge_view(self.conn, cutoff)
        self.assertEqual({r["type"] for r in operated["relations"]}, {"supersedes_claim"})
        retro = eh.knowledge_view(self.conn, cutoff, mode="retrospective", resolvers=["linker-2"])
        self.assertIn("amends", {r["type"] for r in retro["relations"]})
        self.assertFalse(retro["as_operated"])
        self.assertIn("retrospective", retro["label"])
        with self.assertRaises(ValueError):
            eh.knowledge_view(self.conn, cutoff, resolvers=["linker-2"])
        with self.assertRaisesRegex(ValueError, "predate the resolver"):
            eh.record_evidence_relation(self.conn, self.f2, self.f1, "amends", decision="candidate",
                                        decision_at="2026-09-24T00:00:00Z", rule_version="linker-2")

    def test_supersession_needs_order_compatibility_and_real_pointers(self):
        self.amended_filing()
        self.add_amendment()
        with self.assertRaisesRegex(ValueError, "known before"):
            eh.record_evidence_relation(self.conn, self.f1, self.f2, "supersedes_claim",
                                        from_claim="/claims/revenue", to_claim="/claims/revenue",
                                        decision="accepted", decision_at="2026-09-23T00:00:00Z",
                                        rule_version="manual-1")
        with self.assertRaisesRegex(ValueError, "different quantities"):
            eh.record_evidence_relation(self.conn, self.f2, self.f1, "supersedes_claim",
                                        from_claim="/claims/revenue", to_claim="/claims/eps",
                                        decision="accepted", decision_at="2026-09-23T00:00:00Z",
                                        rule_version="manual-1")
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            eh.record_evidence_relation(self.conn, self.f2, self.f1, "supersedes_claim",
                                        from_claim="/claims/margin", to_claim="/claims/revenue",
                                        decision="accepted", decision_at="2026-09-23T00:00:00Z",
                                        rule_version="manual-1")
        with self.assertRaisesRegex(ValueError, "before it was known"):
            eh.record_evidence_relation(self.conn, self.f2, self.f1, "contradicts",
                                        from_claim="/claims/revenue", to_claim="/claims/revenue",
                                        decision="accepted", decision_at="2026-09-22T09:00:00Z",
                                        rule_version="manual-1")
        first = self.conn.execute("SELECT relation_id FROM evidence_relations").fetchone()[0]
        eh.record_evidence_relation(self.conn, self.f2, self.f1, "supersedes_claim",
                                    from_claim="/claims/revenue", to_claim="/claims/revenue",
                                    decision="retracted", decision_at="2026-09-23T00:00:00Z",
                                    rule_version="manual-1", supersedes=first)
        with self.assertRaisesRegex(ValueError, "linear"):
            eh.record_evidence_relation(self.conn, self.f2, self.f1, "supersedes_claim",
                                        from_claim="/claims/revenue", to_claim="/claims/revenue",
                                        decision="accepted", decision_at="2026-09-24T00:00:00Z",
                                        rule_version="manual-1", supersedes=first)
        self.assertEqual(self.claims("2026-09-23T12:00:00Z")["revenue"]["status"], "conflicting")

    def test_m15_one_primary_origin_two_coverage_reports_no_price_corroboration(self):
        self.amended_filing()
        self.load("n.json", extract("2026-09-21T14:20:00Z", [
            {"source_key": f"synthetic:news:{n}", "kind": "news", "origin_hint": "issuer-disclosure:ALFA:Q2",
             "subject_id": "issuer:ALFA", "claims": {"revenue": claim("20", attribution="F1")}}
            for n in ("N1", "N2")] + [
            {"source_key": "synthetic:prices:ALFA", "kind": "measurement", "subject_id": "issuer:ALFA",
             "claims": {"window_return": claim("0.04", unit="ratio", period="2026-09-21/22")}}]))
        for n in ("N1", "N2"):
            eh.record_evidence_relation(self.conn, self.version(f"synthetic:news:{n}"), self.f1,
                                        "reproduces", from_claim="/claims/revenue",
                                        to_claim="/claims/revenue", decision="accepted",
                                        decision_at="2026-09-21T14:30:00Z", rule_version="manual-1")
        with self.assertRaisesRegex(ValueError, "measurement"):
            eh.record_evidence_relation(self.conn, self.version("synthetic:prices:ALFA"), self.f1,
                                        "reproduces", decision="accepted",
                                        decision_at="2026-09-21T14:30:00Z", rule_version="manual-1")
        view = eh.knowledge_view(self.conn, "2026-09-21T15:00:00Z")
        summary = eh.origin_summary(view, f"{self.f1}#/claims/revenue")
        self.assertEqual((summary["primary_origins"], summary["coverage_reports"]), (1, 2))
        self.assertEqual(summary["independence"], "not_assessed")
        revenue = [c for c in view["claims"] if c["claim"] == "revenue"]
        self.assertEqual([len(c["values"]) for c in revenue], [1])  # coverage is not re-assertion


class OrderAndPolicyTests(HistoryCase):
    def test_m16_either_arrival_order_gives_the_same_history(self):
        shared = sec_row("0000000001-26-000001")
        a = brief("2026-09-21T15:00:00Z", [shared])
        b = brief("2026-09-22T15:00:00Z", [shared, sec_row("0000000001-26-000002")])
        digests = []
        for order in (("a", "b"), ("b", "a")):
            conn = eh.connect(self.root / f"{''.join(order)}.db")
            for name in order:
                eh.import_archive(conn, self.write(f"{name}.json", a if name == "a" else b))
            digests.append([eh.view_digest(eh.knowledge_view(conn, cut)) for cut in
                            ("2026-09-21T16:00:00Z", "2026-09-22T16:00:00Z")])
            first = conn.execute("SELECT min(observed_at), max(observed_at) FROM sightings "
                                 "JOIN evidence_versions USING(version_id) WHERE source_key=?",
                                 ("sec_edgar:0000000001-26-000001",)).fetchone()
            self.assertEqual(tuple(first), ("2026-09-21T15:00:00.000000Z", "2026-09-22T15:00:00.000000Z"))
            conn.close()
        self.assertEqual(digests[0], digests[1])

    def test_identical_content_keeps_each_capture_policy(self):
        item = {"source_key": "synthetic:news:same", "kind": "news", "claims": {}}
        self.load("p1.json", extract("2026-09-21T15:00:00Z", [item], {"recorded": True, "uses": "metadata_only"}))
        self.load("p2.json", extract("2026-09-22T15:00:00Z", [item], {"recorded": True, "uses": "reviewed_extract"}))
        policies = sorted(json.loads(r[0])["uses"] for r in self.conn.execute(
            "SELECT capture_policy_json FROM sightings"))
        self.assertEqual((self.count("evidence_versions"), policies), (1, ["metadata_only", "reviewed_extract"]))

    def test_suspect_archive_time_is_corrected_later_never_earlier(self):
        report = self.load("skewed.json", brief("2026-09-22T19:07:02Z", [sec_row("0000000001-26-000001")]))
        sha = report["archive_sha"]
        with self.assertRaises(ValueError):
            eh.annotate_archive(self.conn, sha, observed_at_not_before="2026-09-22T10:00:00Z",
                                reason="earlier is not allowed")
        eh.annotate_archive(self.conn, sha, observed_at_not_before="2026-09-23T07:37:00Z",
                            reason="host clock behind at capture",
                            decided_at="2026-09-23T16:00:00Z")
        self.assertEqual(eh.knowledge_view(self.conn, "2026-09-23T00:00:00Z")["evidence"], [])
        self.assertEqual(len(eh.knowledge_view(self.conn, "2026-09-23T08:00:00Z")["evidence"]), 1)


class IntakeAdapterTests(HistoryCase):
    def manifest(self):
        now = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
        row = {"title": "Alfa files quarterly report", "link": "https://alfa.example/q2",
               "canonical_url": "https://alfa.example/q2", "published": "2026-09-21T14:00:00Z",
               "source_time_kind": "published", "provider_seen_at": "2026-09-21T14:05:00Z",
               "provider": "alfa", "publisher": "Alfa", "publisher_domain": "alfa.example",
               "source_class": "official", "source_record_id": "frozen:ALFA",
               "tickers": ["ALFA"], "ticker_metadata_kind": "subject", "summary": None}
        captured = evidence_intake.capture([ProviderResult("Official Feeds", "ok", [row])], now, "shadow")
        processed = {name: [] for name in evidence_intake.TERMINALS}
        processed["selected"] = [row]
        manifest = evidence_intake.finish(captured, processed)
        self.assertEqual(evidence_intake.validate_manifest(manifest), [])
        return manifest

    def test_intake_records_keep_native_ids_and_unverified_origin(self):
        manifest = self.manifest()
        self.load("x.json", brief("2026-09-21T15:00:00Z", [sec_row("0000000001-26-000001")], intake=manifest))
        row = self.conn.execute("SELECT * FROM evidence_versions WHERE kind='news'").fetchone()
        record = manifest["records"][0]
        self.assertEqual((row["native_evidence_id"], row["native_revision_id"], row["origin_hint"]),
                         (record["evidence_id"], record["revision_id"], record["origin_cluster_id"]))
        self.assertIs(json.loads(row["payload_json"])["origin_verified_as_event"], False)
        policy = json.loads(self.conn.execute(
            "SELECT capture_policy_json FROM sightings WHERE json_pointer LIKE '/data_quality%'").fetchone()[0])
        self.assertEqual((policy["recorded"], policy["rights_status"]), (True, "unknown"))

    def test_tampered_intake_is_rejected_but_filings_still_import(self):
        manifest = deepcopy(self.manifest())
        manifest["records"][0]["origin_verified_as_event"] = True
        report = self.load("x.json", brief("2026-09-21T15:00:00Z",
                                           [sec_row("0000000001-26-000001")], intake=manifest))
        components = eh.exact_archive(self.conn, report["archive_sha"])["components"]
        self.assertEqual(components["evidence_intake"]["status"], "rejected")
        self.assertEqual((report["sightings"], self.count("evidence_versions")), (1, 1))


if __name__ == "__main__":
    unittest.main()
