"""Synthetic adversarial rules; these cases are not observed or human gold data."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from omni.classifier import extract, predicates
from omni.clinical_numerics import PREDICATE, parse_sentence, result_projection, semantic
from omni.evidence import check_span, span
from omni.identities import digest, load_registry
from omni.review import build_brief, record_review
from omni.runner import analyze_documents
from omni.store import EventStore
from omni.validator import validate
from test_omni_events import make_doc, NOW, POLICY, POLICIES

ROOT = Path(__file__).resolve().parent
FIXTURES = json.loads((ROOT / "fixtures/omni/clinical_numerics.json").read_text())
BASE = FIXTURES["base_text"]


def fixture_text(row):
    text = row.get("text", BASE)
    for old, new in row.get("replace", []):
        if old not in text:
            raise AssertionError("fixture mutation anchor missing: " + old)
        text = text.replace(old, new)
    return row.get("prefix", "") + text + row.get("suffix", "")


class NumericTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def parse(self, text=BASE, **kwargs):
        doc = make_doc(text, **kwargs)
        return doc, parse_sentence(doc, 0, len(text), self.registry, known_by=NOW)

    def extract(self, text=BASE, **kwargs):
        doc = make_doc(text, **kwargs)
        return doc, extract(doc, self.registry, POLICY, known_by=NOW)

    def decision(self, event, doc, **kwargs):
        return validate(event, [doc], self.registry, POLICIES, now=NOW, **kwargs)

    def test_rule_fixture_corpus(self):
        self.assertTrue(FIXTURES["synthetic"])
        self.assertEqual(FIXTURES["human_review_status"], "pending")
        self.assertEqual(len({r["fixture_id"] for r in FIXTURES["cases"]}), len(FIXTURES["cases"]))
        for row in FIXTURES["cases"]:
            with self.subTest(fixture=row["fixture_id"], rule=row["rule"]):
                _, output = self.parse(fixture_text(row))
                self.assertEqual(len(output["results"]), row["expected_results"])
                if row.get("expected_reason"):
                    actual = set()
                    for lead in output["abstentions"]:
                        actual.add(lead["reason"])
                        actual.update((lead.get("candidate") or {}).get("abstention_reasons", []))
                    self.assertIn(row["expected_reason"], actual)

    def test_every_value_has_revision_bound_exact_support(self):
        doc, out = self.parse()
        result = out["results"][0]
        atoms = []

        def walk(value):
            if isinstance(value, dict):
                if "support" in value:
                    self.assertTrue(check_span(doc, value["support"]))
                    if "value" in value:
                        atoms.append(value)
                for key, child in value.items():
                    if key != "support":
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(result)
        self.assertGreater(len(atoms), 20)
        stats = result["statistics"]
        self.assertEqual(stats["hazard_ratio"]["estimate"]["value"], "0.51")
        self.assertEqual(stats["confidence_interval"]["upper"]["value"], "0.9")
        self.assertEqual(stats["p_value"]["sidedness"]["value"], "two_sided")
        self.assertEqual(stats["p_value"]["adjustment"]["value"], "nominal")
        self.assertEqual([r["denominator"]["value"] for r in stats["absolute_counts"]], ["107", "50"])
        self.assertEqual(result["scope"]["reported_phase"]["value"], "PHASE2B")
        self.assertEqual(result["human_review_status"], "pending")

    def test_schema_checks_all_accepted_synthetic_results(self):
        import jsonschema
        schema = json.loads((ROOT / "schemas/omni-clinical-numerics.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        for row in FIXTURES["cases"]:
            _, output = self.parse(fixture_text(row))
            for result in output["results"]:
                jsonschema.validate(result, schema)

    def test_no_numeric_success_inference_or_automatic_review(self):
        doc, output = self.extract()
        event = output["events"][0]
        decision = self.decision(event, doc)
        self.assertIn("clinical_result_incomplete", decision["reason_codes"])
        self.assertIn("review_required", decision["reason_codes"])
        self.assertFalse(decision["signal_eligible"])
        self.assertEqual(event["clinical_context"]["overall_survival_status"], "not_established")
        doc, output = self.extract("KEYNOTE-942 met its primary endpoint. " + BASE)
        decision = self.decision(output["events"][0], doc)
        self.assertEqual(decision["reason_codes"], ["review_required"])

    def test_historical_phase_two_cannot_supply_phase_three_numbers(self):
        text = "INTerpath-001 reports Phase 3 results. The trial met its primary endpoint.\nPreviously " + BASE
        doc, output = self.extract(text)
        phase3 = next(e for e in output["events"] if e["scope_id"] == "trial:NCT05933577")
        phase2 = next(e for e in output["events"] if e["scope_id"] == "trial:NCT03897881")
        self.assertIsNone(phase3["clinical_context"]["numerical_results"])
        self.assertEqual(phase2["clinical_context"]["numerical_results"][0]["scope"]["trial_id"]["value"], "trial:NCT03897881")
        self.assertIn("historic_result_contamination", self.decision(phase2, doc)["reason_codes"])
        self.assertEqual(self.decision(phase3, doc)["reason_codes"], ["review_required"])

    def test_followup_word_alone_does_not_turn_new_result_into_old_result(self):
        text = "KEYNOTE-942 reports five-year follow-up results and met its primary endpoint."
        doc, output = self.extract(text)
        self.assertNotIn("historic_result_contamination", self.decision(output["events"][0], doc)["reason_codes"])

    def test_failed_prespecified_primary_remains_separate_from_secondary(self):
        sentence = "KEYNOTE-942 did not meet the pre-specified primary endpoint."
        self.assertIn(("primary_endpoint_status", "not_met"), predicates(sentence))
        doc, output = self.extract(sentence + " The trial met a secondary endpoint in a post hoc analysis.")
        values = {(c["predicate"], c["value"]) for c in output["events"][0]["claims"]}
        self.assertIn(("primary_endpoint_status", "not_met"), values)
        self.assertIn(("secondary_endpoint_status", "met"), values)
        self.assertNotIn(("primary_endpoint_status", "met"), values)

    def test_unknown_trial_alongside_known_trial_and_intervening_text(self):
        for text in ("INTerpath-001 and NCT00000000 met the primary endpoint.",
                     "INTerpath-001 began. Unrelated company discussion. The trial met its primary endpoint."):
            doc, output = self.extract(text)
            claim_event = next(e for e in output["events"] if any(c["predicate"] == "primary_endpoint_status" for c in e["claims"]))
            self.assertIsNone(claim_event["scope_id"])
            self.assertIn("trial_unresolved", self.decision(claim_event, doc)["reason_codes"])

    def test_endpoints_timepoints_and_analyses_are_distinct_scopes(self):
        variants = [BASE, BASE.replace("recurrence-free survival", "overall survival"),
                    BASE.replace("18 months", "36 months"), BASE.replace("prespecified", "descriptive")]
        ids = [self.parse(text)[1]["results"][0]["analysis_scope_id"] for text in variants]
        self.assertEqual(len(set(ids)), len(ids))
        doc, output = self.extract("KEYNOTE-942 met its primary endpoint. " + " ".join(variants))
        event = output["events"][0]
        self.assertNotIn("contradiction_unresolved", self.decision(event, doc)["reason_codes"])
        self.assertEqual(len(event["clinical_context"]["numerical_results"]), 4)

    def test_same_analysis_conflicting_estimate_stays_contested(self):
        doc, output = self.extract("KEYNOTE-942 met its primary endpoint. " + BASE + " " + BASE.replace("ratio 0.51", "ratio 0.6"))
        self.assertIn("contradiction_unresolved", self.decision(output["events"][0], doc)["reason_codes"])

    def test_presentation_variations_cannot_hide_conflict_between_exhibits(self):
        filing_policy = {**deepcopy(POLICY), "source_class": "filing",
                         "routes": [{"host": "www.sec.gov", "path_prefix": "/Archives/edgar/data/"}]}
        policies = {filing_policy["policy_id"]: filing_policy}
        native = "urn:tag:sec.gov,2008:accession-number=0000000123-26-000001"
        base_url = "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/"
        first = make_doc("KEYNOTE-942 met its primary endpoint. " + BASE,
                         policy=filing_policy, source_class="filing", url=base_url + "first.htm",
                         native_id=native + "/first", origin_reference=["filing-accession", native])
        for before, after in (("intention-to-treat", "Intention-to-treat"),
                              ("intismeran plus pembrolizumab", "Intismeran plus pembrolizumab"),
                              ("18 months", "18 month"), ("KEYNOTE-942", "NCT03897881")):
            with self.subTest(change=after):
                changed = make_doc(BASE.replace("ratio 0.51", "ratio 0.6").replace(before, after),
                                   policy=filing_policy, source_class="filing", url=base_url + "second.htm",
                                   native_id=native + "/second", origin_reference=["filing-accession", native])
                _, decisions, _ = analyze_documents([first, changed], self.registry, policies, now=NOW)
                self.assertIn("contradiction_unresolved", decisions[0]["reason_codes"])

    def test_tampered_values_scope_flags_and_display_projection_are_rejected(self):
        doc, output = self.extract("KEYNOTE-942 met its primary endpoint. " + BASE)
        original = output["events"][0]
        for change in ("estimate", "denominator", "scope", "review", "support", "version"):
            with self.subTest(change=change):
                event = deepcopy(original)
                result = next(c for c in event["claims"] if c["predicate"] == PREDICATE)["numeric_result"]
                if change == "estimate": result["statistics"]["hazard_ratio"]["estimate"]["value"] = "0.01"
                if change == "denominator": result["statistics"]["absolute_counts"][0]["denominator"]["value"] = "157"
                if change == "scope": result["scope"]["endpoint_rank"]["value"] = "secondary"
                if change == "review": result["human_review_status"] = "approved"
                if change == "support": result["statistics"]["hazard_ratio"]["estimate"]["support"]["text"] = "0.01"
                if change == "version": result["contract"] = "unknown"
                self.assertIn("unsupported_clinical_numeric_result", self.decision(event, doc)["reason_codes"])
        event = deepcopy(original)
        event["clinical_context"]["numerical_results"] = []
        self.assertIn("numeric_context_not_supported", self.decision(event, doc)["reason_codes"])
        event = deepcopy(original)
        event["claims"][0]["historical"] = True
        self.assertIn("claim_qualifier_mismatch", self.decision(event, doc)["reason_codes"])

    def test_cropped_span_cannot_hide_competing_result(self):
        good_doc, output = self.extract(BASE)
        event = deepcopy(output["events"][0])
        bad_doc = make_doc(BASE[:-1] + "; hazard ratio 0.8.")
        claim = next(c for c in event["claims"] if c["predicate"] == PREDICATE)
        # Even a newly computed exact span of the harmless prefix is insufficient.
        claim["support"] = span(bad_doc, 0, len(BASE) - 1)
        self.assertIn("unsupported_clinical_numeric_result", self.decision(event, bad_doc)["reason_codes"])

    def test_source_revision_changes_evidence_not_unchanged_numeric_meaning(self):
        d1, o1 = self.extract(BASE)
        d2, o2 = self.extract(BASE, published="2026-10-18T11:00:00Z")
        self.assertNotEqual(d1["revision_id"], d2["revision_id"])
        self.assertEqual(o1["events"][0]["event_revision"], o2["events"][0]["event_revision"])
        _, changed = self.extract(BASE.replace("ratio 0.51", "ratio 0.6"))
        self.assertNotEqual(o1["events"][0]["event_revision"], changed["events"][0]["event_revision"])

    def test_postposed_p_qualifiers_cannot_be_silently_discarded(self):
        for phrase in ("p=0.02 (nominal two-sided)", "p=0.02, nominal two-sided"):
            with self.subTest(phrase=phrase):
                _, output = self.parse(BASE.replace("two-sided nominal p=0.02", phrase))
                self.assertEqual(output["results"], [])
                self.assertEqual(output["abstentions"][0]["reason"], "numeric_p_qualifier_unsupported")

    def test_conflicting_p_qualifiers_abstain_through_validator(self):
        for phrase in ("one-sided p=0.02 (two-sided)", "nominal p=0.02 (adjusted)"):
            with self.subTest(phrase=phrase):
                text = BASE.replace("two-sided nominal p=0.02", phrase)
                _, output = self.parse(text)
                self.assertEqual(output["results"], [])
                self.assertEqual(output["abstentions"][0]["reason"], "numeric_p_qualifier_ambiguous")
                doc, extracted = self.extract("KEYNOTE-942 met its primary endpoint. " + text)
                self.assertFalse(any(c["predicate"] == PREDICATE for e in extracted["events"] for c in e["claims"]))

    def test_ci_qualifier_does_not_become_p_qualifier(self):
        text = BASE.replace("(95% CI 0.29-0.90); two-sided nominal p=0.02",
                            "(two-sided 95% CI 0.29-0.90, p=0.02)")
        _, output = self.parse(text)
        self.assertEqual(output["results"], [])
        self.assertEqual(output["abstentions"][0]["reason"], "numeric_p_qualifier_unsupported")

    def test_negated_or_compound_analysis_label_abstains(self):
        for phrase in ("not a prespecified analysis", "not an exploratory analysis",
                       "prespecified analysis was not performed",
                       "analysis: prespecified and exploratory"):
            with self.subTest(phrase=phrase):
                _, output = self.parse(BASE.replace("analysis: prespecified", phrase))
                self.assertEqual(output["results"], [])
                reasons = output["abstentions"][0]["candidate"]["abstention_reasons"]
                self.assertTrue({"numeric_analysis_not_established", "numeric_analysis_unsupported"} & set(reasons))

    def test_resumed_exhibits_combine_numeric_results_without_double_counting(self):
        filing_policy = {**deepcopy(POLICY), "source_class": "filing",
                         "routes": [{"host": "www.sec.gov", "path_prefix": "/Archives/edgar/data/"}]}
        policies = {filing_policy["policy_id"]: filing_policy}
        native = "urn:tag:sec.gov,2008:accession-number=0000000123-26-000001"
        base_url = "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/"
        def exhibit(name, text):
            return make_doc(text, policy=filing_policy, source_class="filing", url=base_url + name + ".htm",
                            native_id=native + "/" + name, origin_reference=["filing-accession", native])
        d1 = exhibit("exhibit-one", "KEYNOTE-942 met its primary endpoint. " + BASE)
        d2 = exhibit("exhibit-two", BASE.replace("recurrence-free survival", "overall survival"))
        mirror = exhibit("exhibit-copy", BASE)
        events, decisions, _ = analyze_documents([d1, d2, mirror], self.registry, policies, now=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]["clinical_context"]["numerical_results"]), 2)
        self.assertEqual(decisions[0]["reason_codes"], ["review_required"])
        self.assertEqual(decisions[0]["assertion_origin_units"], 1)
        self.assertIsNone(decisions[0]["independent_origins"])

    def test_numeric_review_receipt_cannot_approve_changed_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "test.db")
            try:
                doc = make_doc("KEYNOTE-942 met its primary endpoint. " + BASE)
                events, decisions, leads = analyze_documents([doc], self.registry, POLICIES, now=NOW)
                store.commit_cycle(cycle_id="synthetic-numeric-1", source_id=POLICY["policy_id"], observed_at=NOW,
                    documents=[doc], events=events, decisions=decisions, leads=leads)
                event = events[0]
                record_review(store, event["event_id"], event["event_revision"], self.registry, POLICIES,
                              now=NOW, reviewer="synthetic-test-reviewer", action="approve")
                brief = build_brief(store, self.registry, POLICIES, now=NOW, mode="reviewed")
                self.assertEqual(len(brief["cards"]), 1)
                changed = make_doc(doc["text"].replace("ratio 0.51", "ratio 0.6"))
                events, decisions, leads = analyze_documents([changed], self.registry, POLICIES, now=NOW)
                store.commit_cycle(cycle_id="synthetic-numeric-2", source_id=POLICY["policy_id"], observed_at=NOW,
                    documents=[changed], events=events, decisions=decisions, leads=leads)
                self.assertEqual(build_brief(store, self.registry, POLICIES, now=NOW, mode="reviewed")["cards"], [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
