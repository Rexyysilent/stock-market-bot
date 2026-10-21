"""Synthetic receipt-backed expansion comparisons; no actual pilot observations."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from config import EDITORIAL_COVERAGE_TICKERS
from instrument_registry import load_registry as load_instruments
from omni import expansion as x, expansion_cli as cli, operating, prospective as p
from omni.identities import digest
from omni.receipts import get_receipt, list_receipts, put_receipt
import test_omni_prospective as support

FIXTURES = Path(__file__).parent / "fixtures/omni/expansion_scenarios.json"
ISSUER = "cik:0001682852"


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.h = support.ProspectiveTests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.store, self.registry, self.policies = self.h.store, self.h.registry, self.h.policies
        self.instruments = load_instruments()
        self.current = []
        self.protocol = self.proposal = None
        self.records, self.adjudications = {}, {}
        self.before_membership = list(EDITORIAL_COVERAGE_TICKERS)

    def freeze(self, *, candidates=None, baseline=None, minimum_independent_cases=1,
               spec_changes=None, review_changes=None):
        self.current = [] if baseline is None else list(baseline)
        spec = self.h.spec(**(spec_changes or {}))
        subject = p.protocol_request(self.registry, self.policies, self.current, spec, now=support.FREEZE)
        spec["review_receipt_id"] = self.h.attest("prospective_protocol", subject, at=support.FREEZE,
                                                **(review_changes or {}))
        self.protocol = p.freeze_protocol(self.store, self.registry, self.policies, self.current, spec,
                                          now=support.FREEZE)["receipt_id"]
        self.proposal = x.create_proposal(self.store, self.protocol, self.registry, self.policies, self.instruments,
                                           self.current, ["MRNA"] if candidates is None else candidates,
                                           now=support.FREEZE, minimum_independent_cases=minimum_independent_cases)
        return self.proposal

    def observation(self, record, *, now=support.REVIEWED):
        subject, _ = x.observation_request(self.store, self.proposal["receipt_id"], record,
                                           self.registry, self.policies, now=now)
        attestation = self.h.attest("expansion_observation", subject, at=now,
                                    bindings={"proposal_id": self.proposal["receipt_id"], **p.current_bindings(self.registry, self.policies)})
        record = dict(record, attestation_ref=attestation)
        result = x.record_observation(self.store, self.proposal["receipt_id"], record,
                                      self.registry, self.policies, now=now)
        self.records[record["observation_id"]] = (result, record)
        return result

    def common(self, kind, name):
        return {"kind": kind, "observation_id": name, "evidence_kind": "synthetic", "observed_at": support.REVIEWED,
                "external_reference": {"reference": "synthetic-test-only:" + name, "sha256": digest({"fixture": name})}}

    def source_inventory(self, *, assessments=True, useful=True, miss_state="abstained", mapping_error=False):
        doc, events = self.h.ingest()
        refs = [support.reference(doc)]
        refs += [{"source_id": support.SOURCE, "native_id": name, "revision_id": digest(name)}
                 for name in ("candidate-miss", "unknown-miss")]
        _, closed = self.h.enumerate_close(self.protocol, refs)
        self.assertEqual(closed["payload"]["status"], "closed")
        for unit in closed["payload"]["selected"]:
            native = unit["native_refs"][0]["native_id"]
            event = events[0] if native == doc["native_id"] else None
            unknown = native == "unknown-miss"
            adjudication, _ = self.h.adjudicate(self.protocol, self.h.decision(
                unit, event, unknown=unknown, issuer_ids=[] if unknown else [ISSUER]))
            self.adjudications[native] = adjudication
            if assessments:
                state = "detected" if event else "not_detected" if unknown else miss_state
                body = dict(self.common("event_assessment", "assessment:" + native),
                            adjudication_ref=adjudication["receipt_id"], useful=bool(event and useful and not mapping_error),
                            mapping_error=bool(event and mapping_error), processing_state=state)
                self.observation(body)
        return doc, events[0]

    def request_records(self, arm, *, duplicate=False, scope=True, missing_counter=False):
        attempts = 1 if arm == "baseline" else 2
        cycle_id = "request-cycle:" + arm
        metrics = {"request_counts": {} if missing_counter else {"attempt": attempts}}
        if scope:
            metrics["expansion_scope_hash"] = self.proposal["payload"][arm + "_scope_hash"]
        self.store.commit_cycle(cycle_id=cycle_id, source_id=support.SOURCE,
                                observed_at="2026-10-21T11:00:05Z", documents=[], events=[], decisions=[],
                                complete=True, preserve_cursor=True, metrics=metrics)
        refs = []
        for number in range(1, attempts + 1):
            body = dict(self.common("request_attempt", arm + ":attempt:" + str(number)), arm=arm,
                        request_id=arm + ":request", attempt_id=arm + ":attempt:" + str(number),
                        source_cycle_id=cycle_id, route_id="synthetic-feed", attempt_number=number, method="GET",
                        started_at=f"2026-10-21T11:00:0{2 * (number - 1)}Z",
                        completed_at=f"2026-10-21T11:00:0{2 * number - 1}Z", http_status=200 if number == attempts else 503)
            if duplicate:
                body["external_reference"]["sha256"] = digest("duplicate-attempt-fixture")
            refs.append(self.observation(body)["receipt_id"])
        self.observation(dict(self.common("request_census", arm + ":census"),
                              arm=arm, attempt_refs=refs, cycle_ids=[cycle_id]))
        return refs

    def operating_record(self, arm, kind, name, **fields):
        body = {"contract": operating.VERSION, **self.common(kind, name),
                "scope_hash": self.proposal["payload"][arm + "_scope_hash"],
                "implementation_hash": self.proposal["payload"]["implementation_hash"],
                "policy_hash": self.proposal["payload"]["policy_hash"],
                "window_start": support.START, "window_end": support.END,
                "source_refs": [self.proposal["receipt_id"]], **fields}
        body["attestation_ref"] = self.h.attest("operating_observation", body,
            bindings={k: body[k] for k in ("scope_hash", "implementation_hash", "policy_hash")})
        return operating.record_observation(self.store, body, now=support.REVIEWED, allow_synthetic=True)

    def operating_records(self, arm, *, total="10", mismatched_window=False, omit_category=None, duplicate=False):
        refs = []
        fields = {"window_start": "2026-10-21T08:00:00Z"} if mismatched_window else {}
        for category in sorted(x.CATEGORIES):
            if category == omit_category:
                continue
            item = self.operating_record(arm, "cost", arm + ":cost:" + category,
                category=category, amount=total if category == "reviewer_labor" else "0", currency="USD",
                allocation_basis="Synthetic matched scope and exact window; no actual invoice.", **fields)
            refs.append(item["receipt_id"])
        if duplicate:
            old = get_receipt(self.store, refs[0])["payload"]
            extra = self.operating_record(arm, "cost", arm + ":cost:duplicate", category=old["category"],
                amount=old["amount"], currency="USD", allocation_basis="Duplicate synthetic invoice fixture.",
                external_reference=old["external_reference"], **fields)
            refs.append(extra["receipt_id"])
        self.operating_record(arm, "cost_completeness", arm + ":cost-census",
                              covered_categories=sorted(x.CATEGORIES), cost_refs=sorted(refs), **fields)
        elapsed = 60.0 if arm == "baseline" else 120.0
        self.operating_record(arm, "review_effort", arm + ":effort", started_at="2026-10-21T12:00:00Z",
                              ended_at="2026-10-21T12:01:00Z" if arm == "baseline" else "2026-10-21T12:02:00Z",
                              elapsed_seconds=elapsed, role="reviewer", run_id="synthetic:" + arm, **fields)
        return refs

    def run_cost_records(self, arm, *, shared_run=False):
        row = self.operating_record(arm, "run_cost", arm + ":run-cost",
            run_id="shared-run" if shared_run else arm + ":run", currency="USD",
            incurred_at="2026-10-21T12:00:00Z", allocation_basis="Synthetic observed allocation, not actuals.",
            review_minutes="60" if arm == "baseline" else "120", review_hourly_rate="10",
            operator_minutes="30", operator_hourly_rate="20", vendor_amount="5" if arm == "baseline" else "6",
            compute_storage_amount="0", other_amount="-1" if arm == "baseline" else "-2")
        self.operating_record(arm, "cost_completeness", arm + ":cost-census",
                              covered_categories=sorted(x.CATEGORIES), cost_refs=[row["receipt_id"]])
        return row

    def build(self, **options):
        self.freeze(minimum_independent_cases=options.get("minimum_independent_cases", 1))
        self.source_inventory(assessments=options.get("assessments", True), useful=options.get("useful", True),
                              miss_state=options.get("miss_state", "abstained"), mapping_error=options.get("mapping_error", False))
        if options.get("duplicate_assessment"):
            _, old = self.records["assessment:synthetic-release"]
            self.observation(dict(old, observation_id="duplicate-assessment",
                                   external_reference={"reference": "synthetic-test-only:duplicate", "sha256": digest("duplicate")}))
        if options.get("requests", True):
            self.request_records("baseline")
            self.request_records("expanded", duplicate=options.get("duplicate_request", False))
        if options.get("operating", True):
            self.operating_records("baseline")
            self.operating_records("expanded", total=options.get("expanded_cost", "16"),
                mismatched_window=options.get("mismatched_window", False),
                omit_category=options.get("omit_category"), duplicate=options.get("duplicate_cost", False))
        return self.report()

    def report(self, **options):
        return x.expansion_report(self.store, self.proposal["receipt_id"], self.registry, self.policies,
                                  self.instruments, self.current, now=support.LATER, **options)

    def reviews(self):
        report = self.report()
        bindings = {"proposal_id": self.proposal["receipt_id"], **p.current_bindings(self.registry, self.policies)}
        identities = {symbol: self.h.attest("expansion_candidate_identity", identity, at=support.LATER, bindings=bindings)
                      for symbol, identity in report["identities"].items() if symbol in report["candidate_symbols"]}
        return {"identity_review_refs": identities,
                "wave_a_review_ref": self.h.attest("expansion_wave_a_review", report["review_subjects"]["wave_a"], at=support.LATER, bindings=bindings),
                "candidate_review_ref": self.h.attest("expansion_candidate_review", report["review_subjects"]["candidate"], at=support.LATER, bindings=bindings)}

    def measurement(self, *, arm="expanded", proposal_id=None, now=support.OBSERVED, **changes):
        context = dict(family=None, instruments=self.instruments, allow_synthetic=True)
        context.update(changes)
        return x.measurement_context(self.store, proposal_id or self.proposal["receipt_id"], arm,
            self.registry, self.policies, self.current, support.SOURCE, "synthetic-feed", now, **context)

    def test_measurement_context_is_read_only_and_keeps_frozen_query_and_source_frame(self):
        self.freeze()
        before = self.store.conn.total_changes
        receipts = list_receipts(self.store)
        baseline = self.measurement(arm="baseline", now=support.START)
        expanded = self.measurement()
        self.assertEqual(expanded["expansion_scope_hash"], self.proposal["payload"]["expanded_scope_hash"])
        self.assertNotEqual(expanded["expansion_scope_hash"], baseline["expansion_scope_hash"])
        self.assertEqual(expanded["query_hash"], digest(support.QUERY))
        self.assertEqual(expanded["query_hash"], baseline["query_hash"])
        self.assertEqual(expanded["sampling_family"], "clinical_result")
        self.assertEqual(expanded["expansion_measurement_context"]["source_scope"],
                         baseline["expansion_measurement_context"]["source_scope"])
        self.assertNotIn("candidate_symbols", expanded)
        self.assertNotIn("request_counts", expanded)
        self.assertNotIn("uses", expanded)
        self.assertFalse(expanded["expansion_measurement_context"]["automatic_membership_change"])
        self.assertFalse(expanded["expansion_measurement_context"]["signal_eligible"])
        self.assertEqual(self.store.conn.total_changes, before)
        self.assertEqual(list_receipts(self.store), receipts)
        self.assertEqual(list(EDITORIAL_COVERAGE_TICKERS), self.before_membership)

    def test_measurement_context_defaults_to_actual_and_rejects_nonprospective(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError, "actual prospective"):
            self.measurement(allow_synthetic=False)
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            self.measurement(allow_synthetic="true")
        with patch("instrument_registry.load_registry", return_value=self.instruments) as load:
            self.measurement(instruments=None)
        load.assert_called_once_with()
        self.freeze(spec_changes={"partition": "development"})
        with self.assertRaisesRegex(ValueError, "actual prospective"):
            self.measurement()

    def test_measurement_context_requires_bounded_arm_window_and_exact_source_route(self):
        self.freeze()
        for arm in (None, "candidate", "both"):
            with self.subTest(arm=arm), self.assertRaisesRegex(ValueError, "explicit baseline"):
                self.measurement(arm=arm)
        for at in (support.FREEZE, support.END, support.LATER):
            with self.subTest(at=at), self.assertRaisesRegex(ValueError, "inside the frozen"):
                self.measurement(now=at)
        for source, route in (("foreign-source", "synthetic-feed"), (support.SOURCE, "foreign-route")):
            with self.subTest(source=source, route=route), self.assertRaisesRegex(ValueError, "exact frozen"):
                x.measurement_context(self.store, self.proposal["receipt_id"], "baseline", self.registry,
                    self.policies, self.current, source, route, support.OBSERVED,
                    instruments=self.instruments, allow_synthetic=True)

    def test_measurement_context_infers_family_only_for_a_unique_frozen_stratum(self):
        first = self.h.spec()["source_scope"][0]
        second = dict(first, family="clinical_plan", query_hash=digest({"synthetic": "second-query"}))
        self.freeze(spec_changes={"source_scope": [first, second]})
        with self.assertRaisesRegex(ValueError, "one exact frozen"):
            self.measurement()
        clinical = self.measurement(family="clinical_result")
        planned = self.measurement(family="clinical_plan")
        self.assertEqual(clinical["query_hash"], first["query_hash"])
        self.assertEqual(planned["query_hash"], second["query_hash"])
        self.assertEqual(planned["sampling_family"], "clinical_plan")
        with self.assertRaisesRegex(ValueError, "one exact frozen"):
            self.measurement(family="issuer_release")

    def test_measurement_context_rechecks_current_coverage_identities_and_all_versions(self):
        self.freeze()
        self.current = ["MRNA"]
        with self.assertRaisesRegex(ValueError, "current coverage"):
            self.measurement()
        self.current = []
        with patch.object(self.instruments, "fingerprint", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "identity version"):
                self.measurement()
        current = p.current_bindings(self.registry, self.policies)
        for key in current:
            with self.subTest(binding=key), patch.object(p, "current_bindings", return_value=dict(current, **{key: "changed"})):
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.measurement()

    def test_measurement_context_rechecks_protocol_approval_expiry_and_revocation(self):
        self.freeze(review_changes={"expires_at": support.OBSERVED})
        self.measurement(now=support.START)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.measurement()
        protocol = p.protocol_state(self.store, self.protocol)
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": protocol["review_receipt_id"]},
                     observed_at="2026-10-21T09:30:00Z")
        with self.assertRaisesRegex(ValueError, "revoked"):
            self.measurement(now="2026-10-21T09:45:00Z")

    def test_measurement_context_reproduces_proposal_instead_of_trusting_imported_scope_hashes(self):
        self.freeze()
        forged = dict(self.proposal["payload"], expanded_scope_hash=digest("synthetic-forged-scope"))
        imported = put_receipt(self.store, x.PROPOSAL_KIND, forged, observed_at=support.FREEZE, refs=[self.protocol])
        with self.assertRaisesRegex(ValueError, "cannot be reproduced"):
            self.measurement(proposal_id=imported["receipt_id"])
        later = put_receipt(self.store, x.PROPOSAL_KIND, self.proposal["payload"], observed_at=support.REVIEWED,
                            refs=[self.protocol])
        with self.assertRaisesRegex(ValueError, "frozen before collection"):
            self.measurement(proposal_id=later["receipt_id"])

    def test_measurement_context_does_not_measure_unresolved_or_empty_candidate_membership(self):
        for candidates in (["NO-SUCH-SYMBOL"], []):
            with self.subTest(candidates=candidates):
                self.freeze(candidates=candidates)
                with self.assertRaisesRegex(ValueError, "resolved nonempty"):
                    self.measurement()

    def test_all_declared_scenarios_execute_through_receipts(self):
        fixture = json.loads(FIXTURES.read_text())
        self.assertEqual(fixture["evidence_kind"], "synthetic")
        self.assertTrue(fixture["not_pilot_evidence"])
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                other = ExpansionTests()
                other.setUp()
                try:
                    report = other.build(**case["changes"])
                    metrics = report["metrics"]
                    self.assertEqual(metrics["deduplicated_useful_candidate_events"], case["useful"])
                    self.assertEqual(metrics["incremental_cost"], case["incremental_cost"])
                    self.assertEqual(metrics["incremental_cost_per_useful_event"], case["unit_cost"])
                    if "reason" in case:
                        self.assertIn(case["reason"], report["reason_codes"])
                    if "backlog" in case:
                        self.assertEqual(metrics["candidate_processing"]["backlog"], case["backlog"])
                    self.assertEqual(report["status"], "pending")
                    self.assertEqual(report["real_gate_observations"], 0)
                    self.assertFalse(report["automatic_membership_change"])
                    self.assertFalse(report["automatic_activation"])
                    self.assertFalse(report["signal_eligible"])
                finally:
                    other.doCleanups()

    def test_complete_fixture_reports_recall_retries_effort_and_keeps_misses(self):
        result = self.build()
        m = result["metrics"]
        self.assertEqual(m["useful_yield"]["estimate"], .5)
        self.assertEqual(m["candidate_abstention_rate"]["estimate"], .5)
        self.assertEqual(m["recall"]["overall"]["estimate"], 1 / 3)
        self.assertEqual(m["recall"]["unknown_issuers"]["estimate"], 0)
        self.assertEqual(len(m["recall"]["negative_outcomes"]["misses"]), 2)
        self.assertEqual(m["requests"]["baseline"]["requests"], 1)
        self.assertEqual(m["requests"]["expanded"]["attempts"], 2)
        self.assertEqual(m["requests"]["expanded"]["retries"], 1)
        self.assertEqual(m["operating"]["baseline"]["review_seconds"], "60.0")
        self.assertEqual(m["operating"]["expanded"]["review_seconds"], "120.0")
        self.assertEqual(result["sample"]["source_units"], 3)
        self.assertIn("wave_a_manual_pilot_not_eligible", result["reason_codes"])
        self.assertIn("source_recall_not_eligible", result["reason_codes"])

    def test_candidate_set_difference_identity_and_no_membership_mutation(self):
        before = self.instruments.fingerprint()
        proposal = self.freeze(candidates=["MRNA", "MRNA", "NO-SUCH-SYMBOL"], baseline=["MRNA"])["payload"]
        self.assertEqual(proposal["candidate_symbols"], ["NO-SUCH-SYMBOL"])
        self.assertEqual(proposal["already_covered"], ["MRNA"])
        self.assertEqual(proposal["duplicate_candidates_removed"], 1)
        self.assertEqual(proposal["identity_unresolved"], ["NO-SUCH-SYMBOL"])
        self.assertEqual(proposal["identities"]["MRNA"]["issuer_id"], ISSUER)
        self.assertTrue(proposal["identities"]["MRNA"]["listing"]["evidence"])
        self.assertNotEqual(proposal["baseline_scope_hash"], proposal["expanded_scope_hash"])
        self.report()
        self.assertEqual(self.instruments.fingerprint(), before)
        self.assertEqual(list(EDITORIAL_COVERAGE_TICKERS), self.before_membership)

    def test_post_hoc_proposals_and_wrong_current_coverage_are_rejected(self):
        self.freeze()
        for coverage, at in (([], support.START), (["MRNA"], support.FREEZE)):
            with self.assertRaises(ValueError):
                x.create_proposal(self.store, self.protocol, self.registry, self.policies, self.instruments,
                                  coverage, ["MRNA"], now=at)
        with self.assertRaises(ValueError):
            x.proposal_request(self.store, self.protocol, self.registry, self.policies, self.instruments,
                                [], "MRNA", now=support.FREEZE)

    def test_unknown_missing_assessments_do_not_become_zero_abstention(self):
        result = self.build(assessments=False)
        self.assertEqual(result["metrics"]["candidate_processing"]["unassessed"], 2)
        self.assertIsNone(result["metrics"]["candidate_abstention_rate"]["estimate"])
        self.assertIsNone(result["metrics"]["useful_yield"]["estimate"])
        self.assertIsNone(result["metrics"]["incremental_cost_per_useful_event"])

    def test_incomplete_source_inventory_is_not_a_complete_recall_denominator(self):
        self.freeze()
        self.source_inventory()
        latest = self.adjudications["unknown-miss"]["payload"]
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": latest["review_receipt_id"]},
                     observed_at=support.LATER)
        result = self.report()
        self.assertIsNone(result["metrics"]["recall"]["overall"]["estimate"])
        self.assertEqual(result["metrics"]["recall"]["overall"]["observed_estimate"], .5)
        self.assertIn("source_inventory_or_adjudication_incomplete", result["reason_codes"])

    def test_truthy_and_wrong_scope_reviews_never_satisfy_dependent_review(self):
        self.build()
        report = self.report(wave_a_review_ref="approved", candidate_review_ref="yes", identity_review_refs={"MRNA": "true"})
        self.assertIn("wave_a_review_missing_or_invalid", report["reason_codes"])
        self.assertIn("candidate_review_missing_or_invalid", report["reason_codes"])
        refs = self.reviews()
        valid = self.report(**refs)
        self.assertNotIn("wave_a_review_missing_or_invalid", valid["reason_codes"])
        self.assertNotIn("candidate_review_missing_or_invalid", valid["reason_codes"])
        self.assertNotIn("candidate_identity_review_missing_or_invalid:MRNA", valid["reason_codes"])
        wrong = dict(refs, candidate_review_ref=refs["wave_a_review_ref"])
        self.assertIn("candidate_review_missing_or_invalid", self.report(**wrong)["reason_codes"])

    def test_review_expiry_revocation_and_changed_evidence_are_rechecked(self):
        self.build()
        refs = self.reviews()
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": refs["wave_a_review_ref"]}, observed_at=support.LATER)
        self.assertIn("wave_a_review_missing_or_invalid", self.report(**refs)["reason_codes"])
        old = get_receipt(self.store, refs["candidate_review_ref"])["payload"]
        expired = put_receipt(self.store, "operator_attestation", dict(old, expires_at=support.LATER), observed_at=support.LATER)
        refs["candidate_review_ref"] = expired["receipt_id"]
        self.assertIn("candidate_review_missing_or_invalid", self.report(**refs)["reason_codes"])
        prior, record = self.records["assessment:synthetic-release"]
        self.observation(dict(record, observation_id="revised-usefulness", useful=False, supersedes=prior["receipt_id"],
                              external_reference={"reference": "synthetic-test-only:revision", "sha256": digest("revision")}), now=support.LATER)
        result = self.report()
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 0)
        self.assertEqual(len(list_receipts(self.store, kind=x.OBSERVATION_KIND)), 9)

    def test_revoked_supersession_does_not_restore_prior_usefulness(self):
        self.freeze()
        self.source_inventory()
        prior, record = self.records["assessment:synthetic-release"]
        current = self.observation(dict(record, observation_id="usefulness-correction", useful=False,
                                         supersedes=prior["receipt_id"]), now=support.LATER)
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": current["payload"]["attestation_ref"]},
                     observed_at=support.LATER)
        result = self.report()
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 0)
        self.assertIn("measurement_records_invalidated", result["reason_codes"])
        self.assertIn(prior["payload"]["unit_id"], result["sample"]["unassessed_unit_ids"])

    def test_reviewed_copies_cannot_multiply_candidate_yield(self):
        self.freeze()
        original, _ = self.h.ingest(native="release-original")
        copied, events = self.h.ingest(native="release-copy", text=(
            "Moderna reports INTerpath-001 met its primary endpoint. This is the same release as "
            "https://issuer.example/releases/release-original ."))
        self.h.correspondence(copied, original)
        _, closed = self.h.enumerate_close(self.protocol, [support.reference(original), support.reference(copied)])
        self.assertEqual(len(closed["payload"]["selected"]), 1)
        unit = closed["payload"]["selected"][0]
        decision, _ = self.h.adjudicate(self.protocol, self.h.decision(unit, events[0], unknown=False, issuer_ids=[ISSUER]))
        self.observation(dict(self.common("event_assessment", "copied-useful"), adjudication_ref=decision["receipt_id"],
                              useful=True, mapping_error=False, processing_state="detected"))
        result = self.report()
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 1)
        self.assertEqual(result["sample"]["candidate_unique_origins"], 1)
        self.assertEqual(len(unit["native_refs"]), 2)

    def bundled(self, *, baseline=None):
        self.freeze(baseline=baseline)
        doc, events = self.h.ingest()
        coverage = self.h.coverage([support.reference(doc)])
        p.enumerate_sources(self.store, self.protocol, self.registry, self.policies, [coverage], now=support.END)
        unit = p._inventory(self.store, p.protocol_state(self.store, self.protocol), now=support.END)["units"][0]
        mapping = {"origin_id": unit["origin_id"], "units": [
            {"unit_key": "result", "family": "clinical_result", "scope": "Synthetic independently reviewed result"},
            {"unit_key": "safety", "family": "clinical_result", "scope": "Synthetic independently reviewed safety event"}]}
        subject = {"protocol_id": self.protocol, "origin_id": unit["origin_id"], "native_refs": unit["native_refs"], "units": mapping["units"]}
        mapping["review_receipt_id"] = self.h.attest("event_unit_mapping", subject, at=support.END)
        p.map_bundled_events(self.store, self.protocol, self.registry, self.policies, mapping, now=support.END)
        subject = p.closure_request(self.store, self.protocol, self.registry, self.policies, now=support.REVIEWED)
        review = self.h.attest("prospective_inventory_closure", subject)
        closed = p.close_inventory(self.store, self.protocol, self.registry, self.policies, review, now=support.REVIEWED)
        for index, selected in enumerate(closed["payload"]["selected"]):
            event = events[0] if index == 0 else None
            issuer = "cik:0001745999" if baseline and index == 1 else ISSUER
            adjudication, _ = self.h.adjudicate(self.protocol, self.h.decision(selected, event, unknown=False, issuer_ids=[issuer]))
            self.observation(dict(self.common("event_assessment", "bundle:" + str(index)),
                adjudication_ref=adjudication["receipt_id"], useful=bool(event), mapping_error=False,
                processing_state="detected" if event else "abstained"))
        return self.report()

    def test_bundled_events_expose_correlated_sample_and_count_one_useful_origin(self):
        result = self.bundled()
        self.assertEqual(result["sample"]["candidate_units"], 2)
        self.assertEqual(result["sample"]["candidate_unique_origins"], 1)
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 1)
        self.assertIn("correlated_event_units_share_assertion_origins", result["reason_codes"])

    def test_candidate_origin_already_in_baseline_is_not_net_new_yield(self):
        result = self.bundled(baseline=["BEAM"])
        self.assertEqual(result["sample"]["candidate_units_sharing_baseline_origin"], 1)
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 0)
        self.assertEqual(result["metrics"]["useful_baseline_events"], 1)
        self.assertEqual(result["sample"]["candidate_unique_origins"], 0)
        self.assertIn("zero_useful_candidate_events", result["reason_codes"])

    def test_run_cost_uses_explicit_workbook_inputs_and_signed_credits(self):
        self.freeze()
        self.source_inventory()
        self.run_cost_records("baseline")
        self.run_cost_records("expanded")
        result = self.report()
        baseline = result["metrics"]["operating"]["baseline"]
        self.assertEqual(baseline["cost"], "24")
        self.assertEqual(baseline["category_costs"]["other"], "-1")
        self.assertEqual(result["metrics"]["operating"]["expanded"]["cost"], "34")
        self.assertEqual(result["metrics"]["incremental_cost"], "10")
        self.assertEqual(result["metrics"]["incremental_cost_per_useful_event"], "10")
        self.assertIsNone(baseline["review_seconds"])
        self.assertIn("baseline:review_effort_missing", result["reason_codes"])
        with self.assertRaisesRegex(ValueError, "decimal string"):
            self.operating_record("baseline", "run_cost", "missing-rate", run_id="missing-rate", currency="USD",
                incurred_at="2026-10-21T12:00:00Z", allocation_basis="Synthetic missing input.",
                review_minutes="10", operator_minutes="0", operator_hourly_rate="0", vendor_amount="0",
                compute_storage_amount="0", other_amount="0")

    def test_cross_arm_cost_run_reuse_cannot_produce_incremental_cost(self):
        self.freeze()
        self.source_inventory()
        self.run_cost_records("baseline", shared_run=True)
        self.run_cost_records("expanded", shared_run=True)
        result = self.report()
        self.assertIn("comparison_arms_reuse_cost_run", result["reason_codes"])
        self.assertIsNone(result["metrics"]["incremental_cost"])

    def test_future_imports_do_not_become_historical_measurements(self):
        self.freeze()
        self.source_inventory(assessments=False)
        body = dict(self.common("event_assessment", "later-import"), useful=True, mapping_error=False,
                    processing_state="detected", adjudication_ref=self.adjudications["synthetic-release"]["receipt_id"])
        self.observation(body, now=support.LATER)
        result = x.expansion_report(self.store, self.proposal["receipt_id"], self.registry, self.policies,
                                    self.instruments, self.current, now=support.REVIEWED)
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 0)
        self.assertIn("measurement_records_invalidated", result["reason_codes"])
        self.assertIn("imported after report time", result["sample"]["excluded_observations"][0]["reason"])

    def test_cross_arm_request_evidence_reuse_is_confounded(self):
        self.freeze()
        self.source_inventory()
        baseline = self.request_records("baseline")
        source = get_receipt(self.store, baseline[0])["payload"]
        replay = dict(source, observation_id="expanded:reused", arm="expanded")
        ref = self.observation(replay)["receipt_id"]
        self.observation(dict(self.common("request_census", "expanded:census"), arm="expanded",
                              attempt_refs=[ref], cycle_ids=[source["source_cycle_id"]]))
        self.operating_records("baseline")
        self.operating_records("expanded", total="16")
        result = self.report()
        self.assertIn("comparison_arms_reuse_request_evidence", result["reason_codes"])
        self.assertIsNone(result["metrics"]["incremental_cost"])

    def test_imported_typed_receipt_still_requires_normalized_observation_contract(self):
        self.freeze()
        self.source_inventory()
        before = self.report()["metrics"]["deduplicated_useful_candidate_events"]
        subject = {**self.common("made_up_total", "forged"), "contract": x.VERSION,
                   "proposal_id": self.proposal["receipt_id"], "useful_event_total": 999,
                   **p.current_bindings(self.registry, self.policies)}
        review = self.h.attest("expansion_observation", subject,
                              bindings={"proposal_id": self.proposal["receipt_id"], **p.current_bindings(self.registry, self.policies)})
        put_receipt(self.store, x.OBSERVATION_KIND, dict(subject, attestation_ref=review, submitted_hash=digest(subject)),
                     observed_at=support.REVIEWED, refs=[self.proposal["receipt_id"], review])
        result = self.report()
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], before)
        self.assertIn("measurement_records_invalidated", result["reason_codes"])

    def test_observation_idempotence_is_not_permission_to_rewrite_evidence(self):
        self.freeze()
        self.source_inventory()
        prior, record = self.records["assessment:synthetic-release"]
        replay = x.record_observation(self.store, self.proposal["receipt_id"], record, self.registry, self.policies, now=support.LATER)
        self.assertEqual(replay, prior)
        with self.assertRaisesRegex(ValueError, "reused"):
            x.record_observation(self.store, self.proposal["receipt_id"], dict(record, useful=False), self.registry, self.policies, now=support.LATER)
        with self.assertRaises(ValueError):
            x.observation_request(self.store, self.proposal["receipt_id"], [], self.registry, self.policies, now=support.LATER)

    def test_useful_and_state_claims_must_agree_with_source_adjudication(self):
        self.freeze()
        self.source_inventory(assessments=False)
        base = dict(self.common("event_assessment", "false-useful"), adjudication_ref=self.adjudications["candidate-miss"]["receipt_id"],
                    useful=True, mapping_error=False, processing_state="abstained")
        with self.assertRaisesRegex(ValueError, "contradicts"):
            self.observation(base)
        with self.assertRaisesRegex(ValueError, "processing state"):
            self.observation(dict(base, useful=False, processing_state="detected"))
        with self.assertRaisesRegex(ValueError, "precede"):
            self.observation(dict(base, useful=False, observed_at=support.OBSERVED))

    def test_mapping_errors_remain_visible_and_block_eligibility(self):
        result = self.build(mapping_error=True)
        self.assertEqual(result["metrics"]["mapping_errors"], 1)
        self.assertEqual(result["metrics"]["deduplicated_useful_candidate_events"], 0)
        self.assertIn("mapping_errors_observed", result["reason_codes"])

    def test_existing_budget_counts_are_not_observed_attempts_or_arm_scope(self):
        self.freeze()
        self.source_inventory()
        self.request_records("baseline", scope=False, missing_counter=True)
        result = self.report()["metrics"]["requests"]["baseline"]
        self.assertIsNone(result["requests"])
        self.assertIsNone(result["retries"])
        self.assertIn("source_attempt_counter_missing", result["reason_codes"])
        self.assertIn("source_cycle_comparison_scope_missing_or_mismatched", result["reason_codes"])

    def test_current_coverage_policy_and_instrument_changes_cannot_reuse_comparison(self):
        self.build()
        self.current = ["MRNA"]
        changed = self.report()
        self.assertIn("current_coverage_changed", changed["reason_codes"])
        self.assertIsNone(changed["metrics"]["incremental_cost"])
        self.current = []
        self.policies[support.SOURCE]["version"] = "changed"
        changed = self.report()
        self.assertIn("confounded_implementation_registry_or_policy_change", changed["reason_codes"])
        self.assertIsNone(changed["metrics"]["incremental_cost"])
        with patch.object(self.instruments, "fingerprint", return_value="changed"):
            self.assertIn("instrument_identity_version_changed", self.report()["reason_codes"])

    def test_persisted_report_is_immutable_idempotent_and_synthetic_gate_rejected(self):
        self.build()
        refs = self.reviews()
        first = self.report(persist=True, **refs)
        self.assertEqual(first, self.report(persist=True, **refs))
        for ref in first["refs"]:
            get_receipt(self.store, ref)
        with self.assertRaisesRegex(ValueError, "actual bounded"):
            x.validate_report(self.store, first["receipt_id"], self.registry, self.policies,
                               current_coverage=[], now=support.LATER, instruments=self.instruments)
        forged = dict(first["payload"], evidence_kind="operator_evidence", status="eligible_for_expansion_review", pending_reasons=[])
        forged = put_receipt(self.store, x.REPORT_KIND, forged, observed_at=support.LATER, refs=first["refs"])
        with self.assertRaisesRegex(ValueError, "remains pending"):
            x.validate_report(self.store, forged["receipt_id"], self.registry, self.policies,
                               current_coverage=[], now=support.LATER, instruments=self.instruments)
        self.assertEqual(list(EDITORIAL_COVERAGE_TICKERS), self.before_membership)

    def test_cli_plan_prepare_observe_report_are_local_and_reviewable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["expansion-plan"]), 0)
        plan = json.loads(output.getvalue())
        self.assertIsNone(plan["candidate_list"])
        self.assertEqual(plan["network_requests"], 0)
        self.freeze()
        self.source_inventory(assessments=False)
        parser = argparse.ArgumentParser()
        cli.register_commands(parser.add_subparsers(dest="command", required=True))
        context = dict(store=self.store, registry=self.registry, policies=self.policies, instruments=self.instruments,
                       current_coverage=self.current, now=support.LATER)
        candidate_path = self.h.path / "candidates.json"
        candidate_path.write_text(json.dumps(["MRNA"]))
        proposed = cli.dispatch(parser.parse_args(["expansion-propose", self.protocol, str(candidate_path),
                                                   "--minimum-independent-cases", "1"]), **dict(context, now=support.FREEZE))
        self.assertEqual(proposed["receipt_id"], self.proposal["receipt_id"])
        body = dict(self.common("event_assessment", "cli-assessment"), useful=False, mapping_error=False,
                    processing_state="abstained", adjudication_ref=self.adjudications["candidate-miss"]["receipt_id"])
        path = self.h.path / "observation.json"
        path.write_text(json.dumps(body))
        prepared = cli.dispatch(parser.parse_args(["expansion-observe", self.proposal["receipt_id"], str(path), "--prepare"]), **context)
        self.assertEqual(prepared["subject_hash"], digest(prepared["subject"]))
        self.assertEqual(list_receipts(self.store, kind=x.OBSERVATION_KIND), [])
        body["attestation_ref"] = self.h.attest("expansion_observation", prepared["subject"], at=support.LATER,
            bindings={"proposal_id": self.proposal["receipt_id"], **p.current_bindings(self.registry, self.policies)})
        path.write_text(json.dumps(body))
        result = cli.dispatch(parser.parse_args(["expansion-observe", self.proposal["receipt_id"], str(path)]), **context)
        self.assertEqual(result["kind"], x.OBSERVATION_KIND)
        report = cli.dispatch(parser.parse_args(["expansion-report", self.proposal["receipt_id"], "--persist"]), **context)
        self.assertEqual(report["kind"], x.REPORT_KIND)
        self.assertEqual(report["payload"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
