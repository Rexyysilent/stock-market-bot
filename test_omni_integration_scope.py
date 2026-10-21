"""Synthetic cross-workstream regressions; no source rights or human pilot credit."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from omni.amounts import SCOPED_PREDICATE, amount_projection
from omni.briefs import REQUIRED, export_brief, preview_brief, validate_brief
from omni.evidence import span
from omni.identities import EntityRegistry, digest, load_registry
from omni.lifecycle import implementation_hash, reference, record_correspondence, resolve_origin
from omni.receipts import VERSION as RECEIPT_VERSION, import_receipt
from omni.review import build_brief, event_packet, record_review
from omni.runner import analyze, analyze_documents
from omni.store import EventStore
from omni.validator import validate
from test_omni_clinical_scope import approved_synthetic_catalog
from test_omni_clinical_numerics import BASE
from test_omni_events import NOW, POLICY, POLICIES, make_doc
import test_omni_operating as operating_fixtures

LATER = "2026-10-18T12:01:00Z"
AFTER = "2026-10-18T12:02:00Z"


def imported(store, kind, payload, refs=()):
    """Exercise the public generic importer, not direct database mutation."""
    core = {"contract": RECEIPT_VERSION, "kind": kind, "observed_at": NOW,
            "payload": payload, "refs": sorted(refs),
            "receipt_id": "synthetic-integration:" + digest([kind, payload])}
    return import_receipt(store, {**core, "record_hash": digest(core)})


class ScopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "events.db")
        self.registry = load_registry()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scoped_result(self):
        catalog = approved_synthetic_catalog()
        self.registry = EntityRegistry(load_registry().data, clinical_catalog=catalog)
        definition = catalog["trial_versions"][0]["documents"][0]
        result = make_doc("KEYNOTE-942 met its primary endpoint. " + BASE,
                          native_id="synthetic-scoped-result", url="https://issuer.example/releases/result")
        events, decisions, _ = analyze(result, self.registry, POLICIES, now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(decisions[0]["reason_codes"], ["review_required"])
        self.store.commit_cycle(cycle_id="scoped-result", source_id=POLICY["policy_id"],
            observed_at=NOW, documents=[result], events=events, decisions=decisions, registry=self.registry)
        event = events[0]
        reviewed = record_review(self.store, event["event_id"], event["event_revision"], self.registry,
            POLICIES, now=NOW, reviewer="synthetic-integration-reviewer", action="approve")
        return definition, result, event, reviewed

    def grant(self, doc, fields):
        # Qualified module access avoids collecting OperatingTests a second time.
        helper = operating_fixtures.OperatingTests()
        helper.store, helper.doc = self.store, doc
        return [helper.grant(field) for field in fields]

    def preview(self):
        return preview_brief(self.store, self.registry, POLICIES, now=NOW,
            scope_hash=digest("synthetic-clinical-definition-integration"), allow_synthetic=True)

    def test_historical_definition_is_auto_retained_and_needs_distinct_field_rights(self):
        definition, result, event, reviewed = self.scoped_result()
        packet = event_packet(self.store, event["event_id"], event["event_revision"])
        self.assertEqual([d["evidence_id"] for d in packet["documents"]], [result["evidence_id"]])
        self.assertEqual(packet["scope_documents"], [definition])
        linked = {(r[0], r[1]) for r in self.store.conn.execute(
            "SELECT evidence_id,evidence_revision FROM event_evidence WHERE event_id=? AND revision_id=?",
            (event["event_id"], event["event_revision"]))}
        self.assertIn((definition["evidence_id"], definition["revision_id"]), linked)
        self.assertTrue(definition["publication"]["start"].startswith("2026-01-01"))
        self.assertEqual(reviewed["validation"]["editorial_status"], "eligible")
        self.assertNotIn("outside_recency", reviewed["validation"]["reason_codes"])
        self.assertEqual(len(build_brief(self.store, self.registry, POLICIES, now=NOW, mode="reviewed")["cards"]), 1)

        self.grant(result, REQUIRED + ("claim.numeric",))
        withheld = self.preview()
        self.assertEqual(withheld["cards"], [])
        self.assertIn("field_rights_pending", withheld["withheld"][0]["reason_codes"])
        self.grant(definition, REQUIRED)
        partial = self.preview()
        self.assertEqual(len(partial["cards"]), 1)
        self.assertNotIn("clinical_context", partial["cards"][0])
        self.assertNotIn("clinical_numeric_result", {c["predicate"] for c in partial["cards"][0]["claims"]})
        self.grant(definition, ("claim.numeric",))
        complete = self.preview()
        self.assertIn("clinical_context", complete["cards"][0])
        self.assertEqual(len(complete["cards"][0]["source_attribution"]), 2)
        serialized = json.dumps(complete)
        self.assertNotIn('"support":', serialized)
        self.assertNotIn(result["text"], serialized)
        self.assertNotIn(definition["text"], serialized)
        self.assertEqual(complete["ai_domain_labels"], "pending_human_review")
        self.assertFalse(complete["signal_eligible"])

    def test_changed_definition_quarantines_review_and_cached_catalog_cannot_clear_it(self):
        definition, result, event, _ = self.scoped_result()
        self.assertEqual(self.store.pending(), [])
        corrected = make_doc(definition["text"].replace("recurrence or death", "recurrence only"),
            native_id=definition["native_id"], url=definition["url"],
            published=definition["publication"]["raw"], observed_at=LATER)
        self.store.commit_cycle(cycle_id="definition-correction", source_id=POLICY["policy_id"],
            observed_at=LATER, documents=[corrected], events=[], decisions=[])
        packet = event_packet(self.store, event["event_id"], event["event_revision"])
        self.assertEqual(packet["scope_documents"], [definition])
        self.assertIn("source_revision_superseded", packet["lifecycle"]["hard_reason_codes"])
        self.assertTrue(packet["lifecycle"]["change_refs"])
        self.assertIn(event["event_id"], {r["event_id"] for r in self.store.pending()})
        self.assertEqual(build_brief(self.store, self.registry, POLICIES, now=LATER, mode="reviewed")["cards"], [])
        with self.assertRaisesRegex(ValueError, "source_revision_superseded"):
            record_review(self.store, event["event_id"], event["event_revision"], self.registry,
                POLICIES, now=LATER, reviewer="synthetic-integration-reviewer", action="approve")

        # A resumed cycle may re-supply the old catalog; that is not a re-fetch
        # or approval of the corrected definition and must not restore its head.
        events, decisions, _ = analyze(result, self.registry, POLICIES, now=AFTER)
        self.store.commit_cycle(cycle_id="cached-scope-resume", source_id=POLICY["policy_id"],
            observed_at=AFTER, documents=[result], events=events, decisions=decisions, registry=self.registry)
        head = self.store.conn.execute("SELECT revision_id FROM evidence_heads WHERE evidence_id=?",
            (definition["evidence_id"],)).fetchone()[0]
        self.assertEqual(head, corrected["revision_id"])
        self.assertEqual(build_brief(self.store, self.registry, POLICIES, now=AFTER, mode="reviewed")["cards"], [])
        self.assertIn(event["event_id"], {r["event_id"] for r in self.store.pending()})

    def test_resumed_filing_amounts_rebuild_projection_and_reparse_both_exhibits(self):
        policy = {**deepcopy(POLICY), "policy_id": "synthetic-sec", "source_class": "filing",
                  "routes": [{"host": "www.sec.gov", "path_prefix": "/Archives/edgar/data/"}]}
        policies = {policy["policy_id"]: policy}
        native = "urn:tag:sec.gov,2008:accession-number=0000000123-26-000001"
        def exhibit(name, text, observed_at):
            return make_doc(text, policy=policy, source_class="filing", native_id=native + "/" + name,
                url="https://www.sec.gov/Archives/edgar/data/123/000000012326000001/" + name + ".htm",
                origin_reference=["filing-accession", native], observed_at=observed_at)
        gross = exhibit("gross", "Moderna reports gross proceeds of USD 5 million.", NOW)
        net = exhibit("net", "Moderna reports net proceeds of USD 4 million.", LATER)
        mirror = exhibit("gross-copy", gross["text"], LATER)
        events, decisions, _ = analyze_documents([gross], self.registry, policies, now=NOW)
        self.store.commit_cycle(cycle_id="amount-first", source_id=policy["policy_id"], observed_at=NOW,
            documents=[gross], events=events, decisions=decisions)
        first = events[0]
        record_review(self.store, first["event_id"], first["event_revision"], self.registry, policies,
            now=NOW, reviewer="synthetic-integration-reviewer", action="approve")
        docs = [gross, net, mirror]
        events, decisions, _ = analyze_documents(docs, self.registry, policies, now=LATER)
        self.assertEqual(len(events), 1)
        merged = events[0]
        self.assertNotEqual(first["event_revision"], merged["event_revision"])
        self.assertEqual(decisions[0]["reason_codes"], ["review_required"])
        self.assertEqual(merged["scoped_amounts"], amount_projection(merged["claims"]))
        self.assertEqual(len(merged["scoped_amounts"]), 2)
        self.assertEqual({a["role"]["value"] for a in merged["scoped_amounts"]}, {"gross_proceeds", "net_proceeds"})
        self.store.commit_cycle(cycle_id="amount-resume", source_id=policy["policy_id"], observed_at=LATER,
            documents=docs, events=events, decisions=decisions)
        self.assertEqual(build_brief(self.store, self.registry, policies, now=LATER, mode="reviewed")["cards"], [])
        packet = event_packet(self.store, merged["event_id"], merged["event_revision"])
        self.assertEqual(packet["event"]["scoped_amounts"], merged["scoped_amounts"])
        for mutation in ("projection", "claim"):
            with self.subTest(mutation=mutation):
                damaged = deepcopy(merged)
                if mutation == "projection":
                    damaged["scoped_amounts"] = damaged["scoped_amounts"][:1]
                    expected = "amount_context_not_supported"
                else:
                    atom = next(c["amount_atom"] for c in damaged["claims"] if c["predicate"] == SCOPED_PREDICATE)
                    atom["value"]["value"] = "1"
                    expected = "unsupported_scoped_amount"
                self.assertIn(expected, validate(damaged, docs, self.registry, policies, now=LATER)["reason_codes"])
        record_review(self.store, merged["event_id"], merged["event_revision"], self.registry, policies,
            now=LATER, reviewer="synthetic-integration-reviewer", action="approve")
        cards = build_brief(self.store, self.registry, policies, now=LATER, mode="reviewed")["cards"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0]["scoped_amounts"]), 2)


class ImportedReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = operating_fixtures.OperatingTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_generic_correspondence_import_cannot_bypass_native_source_proofs(self):
        f = self.fixture
        a = make_doc("Unrelated synthetic source A.", native_id="origin-a", url="https://issuer.example/releases/a")
        b = make_doc("Unrelated synthetic source B.", native_id="origin-b", url="https://issuer.example/releases/b")
        f.store.commit_cycle(cycle_id="unrelated-docs", source_id=POLICY["policy_id"], observed_at=NOW,
            documents=[a, b], events=[], decisions=[])
        for proofs in ([], [span(a, 0, len(a["text"]))]):
            with self.subTest(proofs=bool(proofs)):
                body = {"relation": "republishes", "from": reference(a), "to": reference(b), "proofs": proofs,
                        "implementation_hash": implementation_hash(), "policy_hash": digest(POLICIES)}
                att = f.attest("origin_correspondence", body,
                    {"implementation_hash": implementation_hash(), "policy_hash": digest(POLICIES)})
                payload = {**body, "attestation_ref": att["receipt_id"]}
                with self.assertRaises(ValueError):
                    record_correspondence(f.store, payload, now=NOW, allow_synthetic=True)
                imported(f.store, "origin_correspondence", payload, [att["receipt_id"]])
                left = resolve_origin(f.store, reference(a), at=NOW, allow_synthetic=True)
                right = resolve_origin(f.store, reference(b), at=NOW, allow_synthetic=True)
                self.assertNotEqual(left["origin_id"], right["origin_id"])
                self.assertEqual(left["basis"], "exact_native_reference")
                self.assertEqual(left["correspondence_receipt_ids"], [])

    def test_imported_brief_cannot_export_injected_top_level_or_withheld_source_text(self):
        f = self.fixture
        original = preview_brief(f.store, f.registry, POLICIES, now=NOW,
            scope_hash=operating_fixtures.SCOPE, purpose="local_export", persist=True)
        self.assertEqual(original["payload"]["cards"], [])
        self.assertEqual(original["payload"]["rights_refs"], [])
        validate_brief(f.store, original["receipt_id"], f.registry, POLICIES, now=NOW)
        for location in ("withheld", "top_level"):
            with self.subTest(location=location):
                payload = deepcopy(original["payload"])
                if location == "withheld":
                    payload["withheld"].append({"unapproved_source_text": f.doc["text"]})
                else:
                    payload["unapproved_source_text"] = f.doc["text"]
                forged = imported(f.store, "reviewed_brief", payload)
                with self.assertRaises(ValueError):
                    validate_brief(f.store, forged["receipt_id"], f.registry, POLICIES, now=NOW)
                destination = f.root / ("unapproved-" + location + ".json")
                with self.assertRaises(ValueError):
                    export_brief(f.store, forged["receipt_id"], destination, f.registry, POLICIES, now=NOW)
                self.assertFalse(destination.exists())

    def test_imported_brief_requires_exact_rights_inventory(self):
        f = self.fixture
        original = f.approved_brief("local_export")
        self.assertGreater(len(original["payload"]["rights_refs"]), 1)
        validate_brief(f.store, original["receipt_id"], f.registry, POLICIES, now=NOW, allow_synthetic=True)
        payload = deepcopy(original["payload"])
        payload["rights_refs"] = payload["rights_refs"][:1]
        forged = imported(f.store, "reviewed_brief", payload, payload["rights_refs"])
        with self.assertRaises(ValueError):
            validate_brief(f.store, forged["receipt_id"], f.registry, POLICIES, now=NOW, allow_synthetic=True)
        destination = f.root / "omitted-rights.json"
        with self.assertRaises(ValueError):
            export_brief(f.store, forged["receipt_id"], destination, f.registry, POLICIES, now=NOW, allow_synthetic=True)
        self.assertFalse(destination.exists())

    def test_useful_assessment_cannot_precede_retained_brief_or_escape_reporting_window(self):
        f = self.fixture
        brief = f.approved_brief()
        for assessed_at in ("2026-10-18T10:30:00Z", "2026-10-18T12:00:30Z"):
            with self.subTest(assessed_at=assessed_at), self.assertRaises(ValueError):
                f.observe("useful_brief", "outside-window-" + assessed_at, brief_ref=brief["receipt_id"],
                    event_ids=[f.event["event_id"]], useful=True, window_start="2026-10-18T10:00:00Z",
                    window_end="2026-10-18T11:00:00Z", assessed_at=assessed_at)


if __name__ == "__main__":
    unittest.main()
