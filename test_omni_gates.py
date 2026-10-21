"""Synthetic gate-boundary tests; no actual pilot, human approval or activation.

Most checks use real temporary-store/protocol/receipt interfaces. Downstream
comparison tests mock only an already-verified threshold/economics result because real
release evidence must not be invented to exercise those comparisons. The mock
results stay in memory and every resulting overall gate remains pending.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from omni import gates, prospective
from omni.identities import digest
from omni.operating import record_economics_report
from omni.receipts import get_receipt, put_receipt
import test_omni_prospective as prospective_fixture


class GateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = prospective_fixture.ProspectiveTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = self.fixture.store
        self.registry = self.fixture.registry
        self.policies = self.fixture.policies
        self.now = prospective_fixture.REVIEWED
        self.protocol_id = self.fixture.freeze()
        self.candidates = ["SYNTH-A"]

    def context(self, *, protocol_id=None, candidates=None, **kwargs):
        return gates.context_request(
            self.store, protocol_id or self.protocol_id, self.registry, self.policies,
            [], self.candidates if candidates is None else candidates, now=self.now, **kwargs)

    def evaluate(self, values=None, *, candidates=None, now=None, evidence_changes=None):
        choices = self.candidates if candidates is None else candidates
        context = self.context(candidates=choices)
        evidence = {"protocol_id": self.protocol_id, "scope_hash": context["scope_hash"], **(values or {})}
        evidence.update(evidence_changes or {})
        return gates.dependent_gates(
            evidence, [], candidates=choices, store=self.store, registry=self.registry,
            policies=self.policies, now=now or self.now)

    def assert_pending(self, result):
        for name in gates.REQUIRED:
            self.assertEqual(result[name]["status"], "pending")
        self.assertFalse(result["automatic_activation"])
        self.assertFalse(result["automatic_membership_change"])
        self.assertEqual(result["human_review_status"], "pending")

    def test_bare_receipt_strings_without_store_context_cannot_mature_any_gate(self):
        evidence = {name: "approved" for names in gates.REQUIRED.values() for name in names}
        result = gates.dependent_gates(evidence, ["SYNTH-A"], candidates=["SYNTH-A", "SYNTH-B"])
        self.assert_pending(result)
        self.assertEqual(result["wave_b_candidates"], ["SYNTH-B"])
        self.assertIn("typed store", result["OMNI-10"]["invalid"]["stable_architecture_receipt"])

    def test_context_binds_frozen_protocol_coverage_scope_and_candidate_list(self):
        context = self.context()
        same = self.context(candidates=["SYNTH-A", "SYNTH-A"])
        self.assertEqual(context, same)
        changed_candidates = self.context(candidates=["SYNTH-B"])
        self.assertNotEqual(context["candidate_hash"], changed_candidates["candidate_hash"])
        second = self.fixture.freeze(name="Another synthetic future protocol")
        self.assertNotEqual(context["scope_hash"], self.context(protocol_id=second)["scope_hash"])
        with self.assertRaisesRegex(ValueError, "coverage baseline"):
            gates.context_request(self.store, self.protocol_id, self.registry, self.policies,
                                  ["SYNTH-A"], self.candidates, now=self.now)

    def test_arbitrary_scope_or_other_protocol_scope_remains_pending(self):
        for wrong in (digest("arbitrary synthetic scope"), None):
            with self.subTest(scope=wrong):
                result = self.evaluate(evidence_changes={"scope_hash": wrong})
                self.assert_pending(result)
                self.assertIn("scope_hash", result["OMNI-10"]["invalid"])
        second = self.fixture.freeze(name="Different synthetic protocol")
        result = self.evaluate(evidence_changes={"protocol_id": second})
        self.assert_pending(result)
        self.assertIn("exact prospective protocol", result["OMNI-10"]["invalid"]["scope_hash"])

    def test_operating_window_is_explicit_canonical_and_contains_the_source_window(self):
        context = self.context()
        self.assertEqual(context["operating_window"], {
            "start": prospective_fixture.START, "end": prospective_fixture.END})
        wider = {"start": prospective_fixture.START, "end": prospective_fixture.LATER}
        expanded = self.context(operating_window=wider)
        self.assertNotEqual(context["scope_hash"], expanded["scope_hash"])
        self.assertEqual(expanded["operating_window"], wider)
        for invalid in (
                {"start": "2026-10-20T11:59:59Z", "end": prospective_fixture.LATER},
                {"start": prospective_fixture.OBSERVED, "end": prospective_fixture.END},
                {"start": prospective_fixture.START, "end": prospective_fixture.OBSERVED},
                {**wider, "unreviewed_extension": True}):
            with self.subTest(window=invalid), self.assertRaises(ValueError):
                self.context(operating_window=invalid)
        result = self.evaluate(evidence_changes={"operating_window": wider})
        self.assert_pending(result)
        self.assertIn("scope_hash", result["OMNI-10"]["invalid"])

    def test_future_freeze_and_current_policy_change_invalidate_gate_context(self):
        with self.assertRaisesRegex(ValueError, "future prospective freeze"):
            gates.context_request(self.store, self.protocol_id, self.registry, self.policies,
                                  [], self.candidates, now="2026-10-20T11:59:59Z")
        changed = deepcopy(self.policies)
        changed[prospective_fixture.SOURCE]["version"] = "synthetic-changed-policy"
        result = gates.dependent_gates(
            {"protocol_id": self.protocol_id, "scope_hash": self.context()["scope_hash"]},
            [], candidates=self.candidates, store=self.store, registry=self.registry,
            policies=changed, now=self.now)
        self.assert_pending(result)
        self.assertIn("scope_hash", result["OMNI-10"]["invalid"])

    def test_missing_wrong_kind_and_synthetic_architecture_reviews_are_invalid(self):
        foreign = put_receipt(self.store, "synthetic_fixture", {"synthetic": True}, observed_at=self.now)
        context = self.context()
        synthetic_review = self.fixture.attest(
            "omni_architecture_review", context,
            bindings={k: context[k] for k in ("scope_hash", "implementation_hash", "policy_hash")})
        for reference in ("approved", foreign["receipt_id"], synthetic_review):
            with self.subTest(reference=reference):
                result = self.evaluate({"stable_architecture_receipt": reference})
                self.assert_pending(result)
                self.assertIn("stable_architecture_receipt", result["OMNI-10"]["invalid"])
                self.assertEqual(result["OMNI-10"]["validated_refs"], [])

    def test_malformed_receipt_identifiers_are_typed_failures_not_database_errors(self):
        values = {
            "prospective_evaluation_receipt": {"status": "approved"},
            "stable_architecture_receipt": ["not-a-scalar-id"],
            "reviewed_user_value_receipt": {"approved": True},
            "vertical_source_policies": [{"approved": True}],
            "expansion_report_receipt": {"ready": True},
            "reviewed_useful_brief_receipts": [{"useful": True}],
            "field_level_rights_receipt": [{"approved": True}],
            "counsel_review_receipt": {"approved": True},
            "customer_feedback_receipt": [{"useful": True}],
            "operating_cost_receipt": {"cost": 0}}
        result = self.evaluate(values)
        self.assert_pending(result)
        for gate, fields in gates.REQUIRED.items():
            for field in fields:
                self.assertIn(field, result[gate]["invalid"])

    def test_real_interface_rejects_synthetic_prospective_evaluation_receipt(self):
        receipt = prospective.record_evaluation(
            self.store, self.protocol_id, self.registry, self.policies, now=self.now)
        result = self.evaluate({"prospective_evaluation_receipt": receipt["receipt_id"]})
        self.assert_pending(result)
        self.assertIn("synthetic", result["OMNI-10"]["invalid"]["prospective_evaluation_receipt"])

    @staticmethod
    def isolated_threshold_result(protocol_id):
        """Memory-only verifier stub; deliberately not recorded as pilot evidence."""
        return {"payload": {"protocol_id": protocol_id}, "current_report": {
            "manual_pilot": {"status": "eligible_for_review"},
            "recall": {"status": "eligible_for_review"},
            "timing": {"status": "eligible_for_review"},
            "family_source_gates": [{"status": "eligible_for_activation_review"}],
            "pending_reason_codes": [], "evidence_verification": {"real_prospective": True},
            "automatic_activation": False}}

    def test_threshold_result_cannot_be_reused_for_another_protocol(self):
        second = self.fixture.freeze(name="Foreign synthetic threshold boundary")
        with patch("omni.prospective.verify_evaluation_receipt",
                   return_value=self.isolated_threshold_result(second)) as verifier:
            result = self.evaluate({"prospective_evaluation_receipt": "synthetic-verifier-boundary"})
        self.assert_pending(result)
        self.assertIn("protocol differs", result["OMNI-10"]["invalid"]["prospective_evaluation_receipt"])
        self.assertTrue(verifier.call_args.kwargs["require_real"])

    def test_threshold_result_with_absent_source_stratum_or_pending_reason_is_rejected(self):
        for key, value in (("family_source_gates", []), ("pending_reason_codes", ["source_stratum_not_closed"])):
            stub = self.isolated_threshold_result(self.protocol_id)
            stub["current_report"][key] = value
            with self.subTest(field=key), patch("omni.prospective.verify_evaluation_receipt", return_value=stub):
                result = self.evaluate({"prospective_evaluation_receipt": "synthetic-verifier-boundary"})
            self.assert_pending(result)
            self.assertIn("thresholds", result["OMNI-10"]["invalid"]["prospective_evaluation_receipt"])

    def expansion_boundary(self, *, protocol_id=None, candidates=None):
        proposal = put_receipt(self.store, "expansion_proposal",
            {"protocol_id": protocol_id or self.protocol_id, "synthetic_boundary_fixture": True}, observed_at=self.now)
        # The real expansion verifier is replaced only for the downstream scope
        # comparison. No forged actual expansion report is written to the store.
        return {"payload": {"proposal_id": proposal["receipt_id"],
                "candidate_symbols": self.candidates if candidates is None else candidates,
                "evidence_kind": "operator_evidence", "pending_reasons": [],
                "automatic_membership_change": False}}

    def test_expansion_report_for_a_different_candidate_or_protocol_is_rejected(self):
        second = self.fixture.freeze(name="Foreign synthetic expansion boundary")
        for stub in (self.expansion_boundary(candidates=["SYNTH-B"]),
                     self.expansion_boundary(protocol_id=second)):
            with self.subTest(stub=stub), patch("omni.expansion.validate_report", return_value=stub):
                result = self.evaluate({"expansion_report_receipt": "synthetic-verifier-boundary"})
            self.assert_pending(result)
            self.assertIn("protocol/candidate scope", result["OMNI-11"]["invalid"]["expansion_report_receipt"])

    def test_matching_expansion_evidence_cannot_skip_prior_gate(self):
        with patch("omni.expansion.validate_report", return_value=self.expansion_boundary()):
            result = self.evaluate({"expansion_report_receipt": "synthetic-verifier-boundary"})
        self.assert_pending(result)
        self.assertNotIn("expansion_report_receipt", result["OMNI-11"]["missing"])
        self.assertIn("OMNI-10", result["OMNI-11"]["missing"])
        self.assertIn("OMNI-11", result["OMNI-12"]["missing"])

    def test_future_economics_envelope_is_rejected_before_derivation(self):
        value = {"scope_hash": self.context()["scope_hash"], "window_start": prospective_fixture.START,
                 "window_end": prospective_fixture.END, "synthetic_boundary_fixture": True}
        future = put_receipt(self.store, "operating_economics", value, observed_at=prospective_fixture.LATER)
        result = self.evaluate({"operating_cost_receipt": future["receipt_id"]})
        self.assert_pending(result)
        self.assertIn("future operating", result["OMNI-12"]["invalid"]["operating_cost_receipt"])

    def test_empty_actual_economics_cannot_be_upgraded_by_report_flags(self):
        envelope = record_economics_report(
            self.store, scope_hash=self.context()["scope_hash"], window_start=prospective_fixture.START,
            window_end=prospective_fixture.END, policy_hash=digest(self.policies), now=self.now,
            registry=self.registry, policies=self.policies)
        result = self.evaluate({"operating_cost_receipt": envelope["receipt_id"]})
        self.assert_pending(result)
        self.assertIn("operating_cost_receipt", result["OMNI-12"]["invalid"])
        altered = dict(envelope["payload"], direct_cost="0", useful_event_units=["synthetic-invented-unit"],
                       cost_per_useful_event="0", pending_reasons=[], measured_actuals=True)
        forged = put_receipt(self.store, "operating_economics", altered, observed_at=self.now)
        result = self.evaluate({"operating_cost_receipt": forged["receipt_id"]})
        self.assert_pending(result)
        self.assertIn("derivation mismatch", result["OMNI-12"]["invalid"]["operating_cost_receipt"])

    def test_same_scope_tag_cannot_substitute_a_different_operating_window(self):
        report = put_receipt(self.store, "operating_economics", {
            "scope_hash": self.context()["scope_hash"], "window_start": prospective_fixture.OBSERVED,
            "window_end": prospective_fixture.END, "evidence_kind": "synthetic"}, observed_at=self.now)
        result = self.evaluate({"operating_cost_receipt": report["receipt_id"]})
        self.assert_pending(result)
        self.assertIn("window", result["OMNI-12"]["invalid"]["operating_cost_receipt"])

    def isolated_economic_result(self, records):
        """Synthetic stored boundary data; actual-success status is memory-only."""
        refs = [put_receipt(self.store, "operating_observation", {
            **record, "evidence_kind": "synthetic", "scope_hash": self.context()["scope_hash"],
            "window_start": prospective_fixture.START, "window_end": prospective_fixture.END,
            "policy_hash": digest(self.policies)}, observed_at=self.now)["receipt_id"] for record in records]
        context = self.context()
        payload = {"scope_hash": context["scope_hash"], "window_start": prospective_fixture.START,
                   "window_end": prospective_fixture.END, "record_refs": refs, "direct_cost": "1",
                   "useful_event_units": ["synthetic-unit"], "cost_per_useful_event": "1",
                   "feedback_results": {"useful": 1, "not_useful": 1, "mixed": 0, "unknown": 0},
                   "implementation_hash": context["implementation_hash"], "policy_hash": context["policy_hash"],
                   "pending_reasons": [], "measured_actuals": False, "evidence_kind": "synthetic"}
        envelope = put_receipt(self.store, "operating_economics", payload, observed_at=self.now, refs=refs)
        verified_stub = dict(payload, measured_actuals=True, evidence_kind="operator_evidence")
        return envelope["receipt_id"], refs, verified_stub

    def test_feedback_census_cannot_omit_negative_record_from_verified_economics(self):
        report, refs, stub = self.isolated_economic_result([
            {"kind": "customer_feedback", "result": "useful"},
            {"kind": "customer_feedback", "result": "not_useful"}])
        with patch("omni.operating.economics_report", return_value=stub):
            result = self.evaluate({"operating_cost_receipt": report, "customer_feedback_receipt": [refs[0]]})
        self.assert_pending(result)
        self.assertIn("including negative observations",
                      result["OMNI-12"]["invalid"]["customer_feedback_receipt"])

    def test_reviewed_brief_requires_positive_assessment_in_same_economics(self):
        for useful in (False, True):
            report, _, stub = self.isolated_economic_result([
                {"kind": "useful_brief", "brief_ref": "synthetic-other-brief", "useful": useful}])
            with self.subTest(underlying_useful=useful), patch("omni.operating.economics_report", return_value=stub):
                result = self.evaluate({"operating_cost_receipt": report,
                                        "reviewed_useful_brief_receipts": ["synthetic-requested-brief"]})
            self.assert_pending(result)
            self.assertIn("positive", result["OMNI-12"]["invalid"]["reviewed_useful_brief_receipts"])

    def test_gate_report_records_pending_evidence_and_never_activation(self):
        context = self.context()
        evidence = {"protocol_id": self.protocol_id, "scope_hash": context["scope_hash"]}
        receipt = gates.record_gate_report(self.store, evidence, [], candidates=self.candidates,
                    registry=self.registry, policies=self.policies, now=self.now)
        retained = get_receipt(self.store, receipt["receipt_id"], kind="dependent_gate_report")
        self.assert_pending(retained["payload"]["report"])
        self.assertEqual(retained["payload"]["evidence"], evidence)
        self.assertEqual(retained["refs"], [])


if __name__ == "__main__":
    unittest.main()
