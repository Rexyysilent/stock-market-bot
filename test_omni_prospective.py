"""Synthetic end-to-end source-first protocol tests; no real pilot evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omni import prospective as p, timing, holdout, publications, intervals
from omni.coverage import observation_from_rows, record_observation, record_coverage
from omni.evidence import span
from omni.evaluation import evaluate
from omni.identities import digest, load_registry
from omni.lifecycle import reference, record_correspondence
from omni.prospective_cli import main as command
from omni.receipts import put_receipt, get_receipt, list_receipts
from omni.review import event_packet
from omni.runner import analyze
from omni.store import EventStore, encode
from test_omni_events import make_doc, POLICIES, POLICY

FREEZE = "2026-10-20T12:00:00Z"
START = "2026-10-21T09:00:00Z"
OBSERVED = "2026-10-21T10:00:00Z"
END = "2026-10-21T17:00:00Z"
REVIEWED = "2026-10-21T18:00:00Z"
LATER = "2026-10-21T19:00:00Z"
QUERY = {"fixture": "source-first-feed"}
SOURCE = POLICY["policy_id"]


class ProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = EventStore(self.path / "events.db")
        self.registry = load_registry()
        self.policies = deepcopy(POLICIES)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def spec(self, **changes):
        value = {
            "name": "Synthetic source-first verification",
            "partition": "prospective", "window_start": START, "window_end": END,
            "source_scope": [{
                "source_id": SOURCE, "route_id": "synthetic-feed", "family": "clinical_result",
                "enumeration_contract": "omni-native-inventory-1", "query_hash": digest(QUERY),
                "coverage_kind": "feed_interval", "timing_required": True}],
            "sampling": {"method": "census"}, "event_unit_rule": "reviewed_assertion_origin",
            "poll_interval_seconds": 900, "minimum_timing_per_stratum": 1,
            "minimum_unknown_issuer_gold": 1, "minimum_negative_outcome_gold": 1,
            "custodians": ["synthetic-custodian"], "evidence_kind": "synthetic",
            "scoring_rubric_hash": digest({"synthetic": "rubric"}),
            "exclusion_rules_hash": digest({"synthetic": "source-observation-window"}),
            "stop_rule": "Synthetic fixed window; no live run or approval."}
        value.update(changes)
        return value

    def attest(self, purpose, subject, *, at=REVIEWED, reviewer="synthetic-custodian",
               bindings=None, **changes):
        payload = {
            "schema": "omni-operator-attestation-1", "purpose": purpose,
            "subject_hash": digest(subject), "decision": "approved",
            "reviewer": reviewer, "reviewed_at": at, "evidence_kind": "synthetic",
            "external_reference": {"reference": "synthetic-test-only:" + purpose,
                                   "sha256": digest({"synthetic_external_receipt": purpose, "subject": subject})},
            **(bindings or p.current_bindings(self.registry, self.policies))}
        payload.update(changes)
        return put_receipt(self.store, "operator_attestation", payload, observed_at=at)["receipt_id"]

    def freeze(self, **changes):
        spec = self.spec(**changes)
        request = p.protocol_request(self.registry, self.policies, [], spec, now=FREEZE)
        spec["review_receipt_id"] = self.attest("prospective_protocol", request, at=FREEZE)
        return p.freeze_protocol(self.store, self.registry, self.policies, [], spec, now=FREEZE)["receipt_id"]

    def ingest(self, native="synthetic-release", text=None, *, at=OBSERVED, cycle=None):
        doc = make_doc(text or "Moderna reports INTerpath-001 met its primary endpoint.",
                       native_id=native, url="https://issuer.example/releases/" + native,
                       published=START, observed_at=at)
        events, decisions, leads = analyze(doc, self.registry, self.policies, now=at)
        self.store.commit_cycle(cycle_id=cycle or "ingest:" + native, source_id=SOURCE,
                                observed_at=at, documents=[doc], events=events,
                                decisions=decisions, leads=leads, complete=True,
                                preserve_cursor=True)
        return doc, events

    def coverage(self, refs, *, route="synthetic-feed", family="clinical_result",
                 first=START, last=END, captured=OBSERVED, suffix="base",
                 capped=False, query=QUERY, second=True):
        first_cycle, last_cycle = "first:" + suffix, "last:" + suffix
        rows = [{"native_id": r["native_id"], "revision_id": r["revision_id"]} for r in refs]
        first_obs = observation_from_rows(
            rows, source_id=SOURCE, route_id=route, family=family,
            acquired_at=captured, window_start=first, window_end=captured,
            policy_hash=digest(self.policies), raw_response_hash=digest({"synthetic": rows}),
            query=query, capped=capped, end_of_results=not capped,
            reported_total=len(refs), evidence_kind="synthetic")
        self.store.commit_cycle(cycle_id=first_cycle, source_id=SOURCE, observed_at=captured,
                                documents=[], events=[], decisions=[], complete=True,
                                preserve_cursor=True, coverage_observations=[first_obs])
        health = json.loads(self.store.conn.execute("SELECT health FROM source_cycles WHERE cycle_id=?", (first_cycle,)).fetchone()[0])
        observed = health["observation_receipt_ids"]
        cycles = [first_cycle]
        if second:
            last_obs = observation_from_rows(
                rows, source_id=SOURCE, route_id=route, family=family,
                acquired_at=last, window_start=captured, window_end=last,
                policy_hash=digest(self.policies), raw_response_hash=digest({"synthetic_last": rows}),
                query=query, end_of_results=True, reported_total=len(refs),
                evidence_kind="synthetic")
            self.store.commit_cycle(cycle_id=last_cycle, source_id=SOURCE, observed_at=last,
                                    documents=[], events=[], decisions=[], complete=True,
                                    preserve_cursor=True, coverage_observations=[last_obs])
            health = json.loads(self.store.conn.execute("SELECT health FROM source_cycles WHERE cycle_id=?", (last_cycle,)).fetchone()[0])
            observed += health["observation_receipt_ids"]
            cycles.append(last_cycle)
        request = {"kind": "feed_interval", "source_id": SOURCE, "route_id": route, "family": family,
                   "policy_hash": digest(self.policies),
                   "implementation_hash": p.implementation_hash(),
                   "window_start": first, "window_end": last,
                   "cycle_ids": cycles, "observation_refs": observed,
                   "contiguous_head_traversal": True}
        return record_coverage(self.store, request, now=last)["receipt_id"]

    def enumerate_close(self, protocol, refs, **coverage_changes):
        coverage = self.coverage(refs, **coverage_changes)
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        review = self.attest("prospective_inventory_closure", request)
        result = p.close_inventory(self.store, protocol, self.registry, self.policies, review, now=REVIEWED)
        return coverage, result

    def decision(self, unit, event=None, *, unknown=True, event_present=True, **changes):
        value = {
            "unit_id": unit["unit_id"], "event_present": event_present, "detected": bool(event),
            "unknown_issuer": unknown, "issuer_ids": [] if unknown else ["cik:0001682852"],
            "negative_outcome": True, "meaningfully_exercised": True,
            "critical_error": False, "proposed_publication": bool(event), "correct": True,
            "reviewed_at": REVIEWED, "reviewer": "synthetic-custodian", "detections": []}
        if event:
            packet = event_packet(self.store, event["event_id"], event["event_revision"])
            value["detections"] = [{"event_id": event["event_id"], "event_revision": event["event_revision"],
                                    "input_hash": digest({"event": packet["event"], "documents": packet["documents"]})}]
        value.update(changes)
        return value

    def adjudicate(self, protocol, decision, *, at=REVIEWED):
        subject = p.adjudication_request(self.store, protocol, self.registry, self.policies, decision, now=at)
        decision = dict(decision, review_receipt_id=self.attest(
            "prospective_adjudication", subject, at=decision["reviewed_at"], reviewer=decision["reviewer"]))
        return p.adjudicate(self.store, protocol, self.registry, self.policies, decision, now=at), decision

    def simple_closed(self, **spec):
        protocol = self.freeze(**spec)
        doc, events = self.ingest()
        coverage, closed = self.enumerate_close(protocol, [reference(doc)])
        self.assertEqual(closed["payload"]["status"], "closed", closed["payload"].get("reason_codes"))
        return protocol, doc, events[0], coverage, closed

    def test_freeze_requires_future_rules_review_and_not_classifier_ids(self):
        for bad in (self.spec(window_start=FREEZE), self.spec(evidence_kind="template"),
                    self.spec(poll_interval_seconds=100000), self.spec(event_unit_rule="model_discoveries"),
                    self.spec(scoring_rubric_hash="truthy-but-not-an-artifact")):
            with self.assertRaises(ValueError):
                p.protocol_request(self.registry, self.policies, [], bad, now=FREEZE)
        with self.assertRaisesRegex(ValueError, "classifier discoveries"):
            p.freeze_protocol(self.store, self.registry, self.policies, [],
                              self.spec(gold_event_ids=["made-up"]), now=FREEZE)
        with self.assertRaisesRegex(ValueError, "review receipt"):
            p.freeze_protocol(self.store, self.registry, self.policies, [], self.spec(), now=FREEZE)
        self.assertEqual(list_receipts(self.store, kind=p.KINDS["protocol"]), [])

    def test_exact_freeze_replay_is_idempotent_and_policy_drift_rejected(self):
        spec = self.spec()
        body = p.protocol_request(self.registry, self.policies, [], spec, now=FREEZE)
        spec["review_receipt_id"] = self.attest("prospective_protocol", body, at=FREEZE)
        first = p.freeze_protocol(self.store, self.registry, self.policies, [], spec, now=FREEZE)
        second = p.freeze_protocol(self.store, self.registry, self.policies, [], spec, now=FREEZE)
        self.assertEqual(first, second)
        changed = deepcopy(self.policies); changed[SOURCE]["version"] = "changed"
        with self.assertRaisesRegex(ValueError, "policy changed"):
            p.protocol_state(self.store, first["receipt_id"], self.registry, changed)

    def test_request_approval_and_actual_freeze_can_have_distinct_clocks(self):
        requested = p.protocol_request(self.registry, self.policies, [], self.spec(), now=FREEZE)
        approval_at = "2026-10-20T13:00:00Z"
        frozen_at = "2026-10-20T14:00:00Z"
        record = dict(requested, review_receipt_id=self.attest("prospective_protocol", requested, at=approval_at))
        frozen = p.freeze_protocol(self.store, self.registry, self.policies, [], record, now=frozen_at)
        protocol = p.protocol_state(self.store, frozen["receipt_id"], self.registry, self.policies)
        self.assertEqual(protocol["requested_at"], FREEZE)
        self.assertEqual(protocol["frozen_at"], frozen_at)
        self.assertEqual(frozen["observed_at"], frozen_at)
        self.assertEqual(frozen, p.freeze_protocol(self.store, self.registry, self.policies, [], record, now=LATER))
        report = p.evaluation_report(self.store, frozen["receipt_id"], self.registry, self.policies, now=LATER)
        self.assertNotIn("protocol_review_stale_or_revoked", report["pending_reason_codes"])
        fresh = p.protocol_request(self.registry, self.policies, [], self.spec(name="Another request"), now=FREEZE)
        fresh["review_receipt_id"] = self.attest("prospective_protocol", fresh, at=approval_at)
        with self.assertRaisesRegex(ValueError, "precede the future window"):
            p.freeze_protocol(self.store, self.registry, self.policies, [], fresh, now=START)

    def test_source_first_inventory_includes_unknown_miss_without_any_classifier_output(self):
        protocol = self.freeze()
        refs = [{"source_id": SOURCE, "native_id": name, "revision_id": digest(name)}
                for name in ("unknown-source-event", "another-unclassified-event")]
        _, closed = self.enumerate_close(protocol, refs)
        self.assertEqual(closed["payload"]["status"], "closed")
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(len(closed["payload"]["selected"]), 2)
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit))
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["inventory"]["expected"], 2)
        self.assertEqual(report["inventory"]["adjudicated"], 1)
        self.assertEqual(len(report["inventory"]["pending"]), 1)
        self.assertEqual(report["recall"]["overall"]["n"], 0)
        self.assertEqual(report["manual_pilot"]["status"], "pending")
        self.assertFalse(report["automatic_activation"])

    def test_end_to_end_exact_hit_and_miss_remain_synthetic(self):
        protocol = self.freeze()
        doc, events = self.ingest()
        unknown = {"source_id": SOURCE, "native_id": "unseen-by-classifier", "revision_id": digest("miss")}
        _, closed = self.enumerate_close(protocol, [reference(doc), unknown])
        for unit in closed["payload"]["selected"]:
            event = events[0] if reference(doc) in unit["native_refs"] else None
            self.adjudicate(protocol, self.decision(unit, event))
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["inventory"]["pending"], [])
        self.assertEqual(report["fixture_or_nonprospective_summary"]["adjudications"], 2)
        self.assertEqual(report["fixture_or_nonprospective_summary"]["detections"], 1)
        self.assertEqual(report["precision"]["n"], 0)
        self.assertEqual(report["manual_pilot"]["eligible_scheduled_sessions"], 0)
        self.assertIn("partition_or_synthetic_evidence_not_real_prospective", report["pending_reason_codes"])

    def test_capped_observation_and_unclosed_interval_leave_inventory_pending(self):
        protocol = self.freeze()
        ref = {"source_id": SOURCE, "native_id": "cap", "revision_id": digest("cap")}
        for changes in ({"capped": True, "suffix": "cap"}, {"second": False, "suffix": "gap"}):
            coverage = self.coverage([ref], **changes)
            p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        result = p.close_inventory(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertEqual(result["payload"]["status"], "pending")
        self.assertIn("partial_source_coverage", result["payload"]["reason_codes"])
        self.assertIsNone(p._closed(self.store, protocol))

    def test_missing_source_stratum_cannot_close_and_query_changes_are_explicit(self):
        scope = self.spec()["source_scope"]
        protocol = self.freeze(source_scope=scope + [dict(scope[0], route_id="second-route")])
        ref = {"source_id": SOURCE, "native_id": "one", "revision_id": digest("one")}
        coverage = self.coverage([ref], query={"different": "query"})
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertIn("source_stratum_not_closed", request["reason_codes"])
        self.assertIn("enumeration_query_mismatch", request["reason_codes"])
        self.assertEqual(len(request["source_strata"]), 2)

    def test_missing_closure_review_does_not_freeze_denominator(self):
        protocol = self.freeze()
        ref = {"source_id": SOURCE, "native_id": "one", "revision_id": digest("one")}
        coverage = self.coverage([ref])
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        result = p.close_inventory(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertIn("inventory_closure_review_missing", result["payload"]["reason_codes"])
        self.assertIsNone(p._closed(self.store, protocol))

    def test_complete_later_frame_resolves_cap_without_deleting_history(self):
        protocol = self.freeze()
        ref = {"source_id": SOURCE, "native_id": "cap-recovered", "revision_id": digest("cap-recovered")}
        capped = self.coverage([ref], suffix="capped", capped=True)
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [capped], now=END)
        self.assertEqual(p.close_inventory(self.store, protocol, self.registry, self.policies, now=REVIEWED)["payload"]["status"], "pending")
        complete = self.coverage([ref], suffix="recovered")
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [complete], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertEqual(request["reason_codes"], [])
        self.assertIn("partial_source_coverage", request["historical_enumeration_reason_codes"])
        review = self.attest("prospective_inventory_closure", request)
        closed = p.close_inventory(self.store, protocol, self.registry, self.policies, review, now=REVIEWED)
        self.assertEqual(len(closed["payload"]["selected"]), 1)
        self.assertEqual(closed["payload"]["source_strata"][0]["incomplete_historical_receipt_ids"], [capped])
        self.assertEqual(len(p._records(self.store, protocol, "enumeration")), 2)

    def test_recovery_cannot_drop_previously_observed_native_id(self):
        protocol = self.freeze()
        refs = [{"source_id": SOURCE, "native_id": name, "revision_id": digest(name)} for name in ("easy", "hard")]
        capped = self.coverage(refs, suffix="capped", capped=True)
        complete = self.coverage(refs[:1], suffix="incomplete-recovery")
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [capped, complete], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertIn("previous_source_observations_not_in_complete_frame", request["reason_codes"])
        self.assertEqual(len(request["selected"]), 2)
        with self.assertRaisesRegex(ValueError, "closed source inventory"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies, {"unit_id": "fake"}, now=REVIEWED)

    def test_duplicate_enumeration_does_not_multiply_source_events(self):
        protocol = self.freeze()
        ref = {"source_id": SOURCE, "native_id": "one", "revision_id": digest("one")}
        coverage = self.coverage([ref])
        first = p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        second = p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=REVIEWED)
        self.assertEqual(first, second)
        self.assertEqual(len(p._inventory(self.store, p.protocol_state(self.store, protocol), now=LATER)["selected"]), 1)

    def test_generic_enumeration_cannot_drop_unknown_source_records(self):
        protocol = self.freeze()
        refs = [{"source_id": SOURCE, "native_id": name, "revision_id": digest(name)}
                for name in ("synthetic-easy", "synthetic-unknown-miss")]
        coverage = self.coverage(refs)
        manifest = p.protocol_state(self.store, protocol)
        raw, native, reasons = p._coverage_check(self.store, manifest, coverage, now=END)
        observations, exclusions, _, _ = p._enumerated_rows(
            self.store, manifest, raw, native, now=END)
        body = {"contract": p.VERSION, "protocol_id": protocol,
                "coverage_receipt_id": coverage, "observations": observations[:1],
                "excluded_source_records": exclusions,
                "enumeration_basis": "immutable_source_native_inventory",
                "classifier_events_consulted": False, "reason_codes": reasons,
                **p._bindings(manifest)}
        put_receipt(self.store, p.KINDS["enumeration"], body, observed_at=END,
                    refs=[protocol, coverage])
        request = p.closure_request(self.store, protocol, self.registry, self.policies,
                                    now=REVIEWED)
        self.assertEqual(len(request["selected"]), 2)
        self.assertIn("enumeration_source_derivation_mismatch", request["reason_codes"])
        closed = p.close_inventory(self.store, protocol, self.registry, self.policies,
                                    now=REVIEWED)
        self.assertEqual(closed["payload"]["status"], "pending")

    def test_generic_inventory_closure_cannot_omit_unknown_source_records(self):
        protocol = self.freeze()
        refs = [{"source_id": SOURCE, "native_id": name, "revision_id": digest(name)}
                for name in ("synthetic-easy", "synthetic-unknown-miss")]
        coverage = self.coverage(refs)
        p.enumerate_sources(self.store, protocol, self.registry, self.policies,
                             [coverage], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies,
                                    now=REVIEWED)
        request["selected"] = request["selected"][:1]
        review = self.attest("prospective_inventory_closure", request)
        forged = put_receipt(
            self.store, p.KINDS["inventory"],
            dict(request, review_receipt_id=review, status="closed"),
            observed_at=REVIEWED,
            refs=[protocol, coverage, review, *request["enumeration_receipt_ids"]])
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies,
                                     now=LATER)
        self.assertEqual(report["inventory"]["expected"], 2)
        self.assertIn("inventory_closure_derivation_mismatch", report["pending_reason_codes"])
        self.assertEqual(get_receipt(self.store, forged["receipt_id"]), forged)
        self.assertFalse(report["automatic_activation"])

    def test_posthoc_enumeration_and_mapping_are_rejected(self):
        protocol, _, _, coverage, closed = self.simple_closed()
        with self.assertRaisesRegex(ValueError, "post-hoc"):
            p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=LATER)
        with self.assertRaisesRegex(ValueError, "precede inventory closure"):
            p.map_bundled_events(self.store, protocol, self.registry, self.policies, {}, now=LATER)
        self.assertEqual(p.close_inventory(self.store, protocol, self.registry, self.policies, now=LATER), closed)

    def test_adjudication_hash_native_mapping_and_one_to_one_constraints(self):
        protocol, _, event, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        bad = self.decision(unit, event); bad["detections"][0]["input_hash"] = "wrong"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies, bad, now=REVIEWED)
        other, events = self.ingest(native="unrelated", cycle="unrelated")
        bad = self.decision(unit, events[0])
        with self.assertRaisesRegex(ValueError, "correspondence"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies, bad, now=REVIEWED)
        with self.assertRaisesRegex(ValueError, "unknown issuer"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies,
                                  self.decision(unit, event, issuer_ids=["invented"]), now=REVIEWED)

    def test_adjudication_replay_and_correction_keep_one_current_decision(self):
        protocol, _, event, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        first, submitted = self.adjudicate(protocol, self.decision(unit, event))
        self.assertEqual(p.adjudicate(self.store, protocol, self.registry, self.policies,
                                     submitted, now=LATER), first)
        with self.assertRaisesRegex(ValueError, "explicitly supersede"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies,
                                  self.decision(unit, event, correct=False), now=LATER)
        corrected = self.decision(unit, event, correct=False, supersedes=first["receipt_id"], reviewed_at=LATER)
        last, _ = self.adjudicate(protocol, corrected, at=LATER)
        self.assertEqual(len(p._records(self.store, protocol, "adjudication")), 2)
        self.assertEqual(p.latest_adjudications(self.store, protocol), [last])
        p.adjudicate(self.store, protocol, self.registry, self.policies, submitted, now=LATER)
        self.assertEqual(p.latest_adjudications(self.store, protocol), [last])

    def test_generic_adjudication_cannot_claim_detection_without_correspondence(self):
        protocol, _, _, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        subject = p.adjudication_request(
            self.store, protocol, self.registry, self.policies,
            self.decision(unit), now=REVIEWED)
        subject["detected"] = True
        review = self.attest("prospective_adjudication", subject)
        imported = put_receipt(
            self.store, p.KINDS["adjudication"],
            dict(subject, review_receipt_id=review, submitted_hash=digest(subject)),
            observed_at=REVIEWED, refs=[protocol, closed["receipt_id"], review])
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies,
                                     now=LATER)
        self.assertEqual(report["inventory"]["adjudicated"], 0)
        self.assertEqual(report["inventory"]["pending"], [unit["unit_id"]])
        self.assertIn("adjudication_source_correspondence_or_shape_invalidated",
                      report["inventory"]["invalidated_adjudications"][0]["reason_codes"])
        self.assertEqual(report["fixture_or_nonprospective_summary"]["detections"], 0)
        self.assertEqual(get_receipt(self.store, imported["receipt_id"]), imported)
        self.assertFalse(report["automatic_activation"])

    def test_another_family_from_same_native_disclosure_is_not_a_gold_detection(self):
        protocol = self.freeze()
        doc, events = self.ingest(text="Moderna filed a shelf offering to raise up to $100 million.")
        self.assertEqual(events[0]["family"], "financing")
        _, closed = self.enumerate_close(protocol, [reference(doc)])
        unit = closed["payload"]["selected"][0]
        self.assertEqual(unit["family"], "clinical_result")
        with self.assertRaisesRegex(ValueError, "event family differs"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies,
                                   self.decision(unit, events[0]), now=REVIEWED)

    def test_non_event_false_proposal_stays_in_complete_publication_denominator(self):
        protocol, _, event, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit, event, event_present=False,
                                                correct=False, meaningfully_exercised=False))
        census = self.close_publications(protocol)
        self.publication_decision(protocol, census["payload"]["units"][0],
                                   correct=False, meaningfully_exercised=False)
        audit, labels = publications.verified_records(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(audit["expected"], 1)
        self.assertEqual(audit["adjudicated"], 1)
        self.assertFalse(labels[0][0]["payload"]["correct"])
        self.assertFalse(labels[0][0]["payload"]["meaningfully_exercised"])
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["publication_inventory"]["expected"], 1)
        self.assertFalse(report["automatic_activation"])

    def test_protocol_review_revocation_invalidates_report_without_erasing_history(self):
        protocol, _, _, _, _ = self.simple_closed()
        manifest = p.protocol_state(self.store, protocol)
        put_receipt(self.store, "attestation_revocation",
                    {"attestation_ref": manifest["review_receipt_id"], "reason": "synthetic test"},
                    observed_at=LATER)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("protocol_review_stale_or_revoked", report["pending_reason_codes"])
        self.assertEqual(report["manual_pilot"]["status"], "pending")
        self.assertTrue(get_receipt(self.store, protocol))

    def correspondence(self, left, right, relation="same_release", *, at=END):
        proof = span(left, 0, len(left["text"]))
        body = {"relation": relation, "from": reference(left), "to": reference(right),
                "proofs": [proof], "implementation_hash": p.implementation_hash(),
                "policy_hash": digest(self.policies)}
        attestation = self.attest("origin_correspondence", body, at=at)
        return record_correspondence(self.store, dict(body, attestation_ref=attestation),
                                     now=at, allow_synthetic=True)

    def test_reviewed_transitive_copies_count_one_assertion_origin(self):
        protocol = self.freeze()
        a, _ = self.ingest(native="release-a", text="Moderna reports INTerpath-001 met its primary endpoint. This reproduces native_id: release-b.")
        b, _ = self.ingest(native="release-b", text="Moderna reports INTerpath-001 met its primary endpoint. This reproduces native_id: release-c.")
        c, _ = self.ingest(native="release-c")
        self.correspondence(a, b); self.correspondence(b, c)
        _, closed = self.enumerate_close(protocol, [reference(a), reference(b), reference(c)])
        self.assertEqual(len(closed["payload"]["selected"]), 1)
        self.assertEqual(len(closed["payload"]["selected"][0]["native_refs"]), 3)

    def test_independent_reporting_does_not_merge_origins_and_late_copy_invalidates(self):
        protocol = self.freeze()
        a, _ = self.ingest(native="release-a", text="Moderna reports INTerpath-001 met its primary endpoint. Independently reports native_id: release-b. Original release native_id: release-b.")
        b, _ = self.ingest(native="release-b")
        self.correspondence(a, b, "independently_reports")
        _, closed = self.enumerate_close(protocol, [reference(a), reference(b)])
        self.assertEqual(len(closed["payload"]["selected"]), 2)
        self.correspondence(a, b, at=LATER)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("inventory_or_origin_correspondence_changed", report["pending_reason_codes"])

    def test_bundled_event_mapping_is_reviewed_before_sampling_and_cannot_reuse_detection(self):
        protocol = self.freeze()
        doc, events = self.ingest()
        coverage = self.coverage([reference(doc)])
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        unit = p._inventory(self.store, p.protocol_state(self.store, protocol), now=END)["units"][0]
        mapping = {"origin_id": unit["origin_id"], "units": [
            {"unit_key": "scope-a", "family": "clinical_result", "scope": "Synthetic independently reviewed endpoint A"},
            {"unit_key": "scope-b", "family": "clinical_result", "scope": "Synthetic independently reviewed endpoint B"}]}
        subject = {"protocol_id": protocol, "origin_id": unit["origin_id"],
                   "native_refs": unit["native_refs"], "units": mapping["units"]}
        mapping["review_receipt_id"] = self.attest("event_unit_mapping", subject, at=END)
        p.map_bundled_events(self.store, protocol, self.registry, self.policies, mapping, now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        review = self.attest("prospective_inventory_closure", request)
        closed = p.close_inventory(self.store, protocol, self.registry, self.policies, review, now=REVIEWED)
        first, second = closed["payload"]["selected"]
        self.adjudicate(protocol, self.decision(first, events[0]))
        with self.assertRaisesRegex(ValueError, "already matched"):
            p.adjudication_request(self.store, protocol, self.registry, self.policies,
                                  self.decision(second, events[0]), now=REVIEWED)
        put_receipt(self.store, "attestation_revocation",
                    {"attestation_ref": mapping["review_receipt_id"]}, observed_at=LATER)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("event_unit_mapping_review_stale_or_revoked", report["pending_reason_codes"])
        self.assertIn("inventory_or_origin_correspondence_changed", report["pending_reason_codes"])

    def test_reused_origins_across_development_and_untouched_do_not_close(self):
        protocol = self.freeze(partition="development")
        ref = {"source_id": SOURCE, "native_id": "one", "revision_id": digest("one")}
        coverage, closed = self.enumerate_close(protocol, [ref])
        self.assertEqual(closed["payload"]["status"], "closed")
        second = self.freeze(partition="untouched")
        p.enumerate_sources(self.store, second, self.registry, self.policies, [coverage], now=END)
        request = p.closure_request(self.store, second, self.registry, self.policies, now=REVIEWED)
        self.assertIn("event_origins_reused_across_partitions", request["reason_codes"])
        self.assertEqual(request["partition_conflicts"][0]["partition"], "development")

    def test_run_sessions_cycle_reuse_and_exact_replay(self):
        protocol, _, _, _, _ = self.simple_closed()
        run = {"run_id": "synthetic-run", "started_at": START, "completed_at": END,
               "execution_mode": "scheduled", "session": "2026-10-21", "scheduled_for": START,
               "source_cycle_ids": ["first:base", "last:base"], "exit_status": 0,
               "invocation_hash": digest("synthetic-invocation"), "scheduler_log_hash": digest("synthetic-log")}
        bad = dict(run, session="2026-10-22")
        with self.assertRaisesRegex(ValueError, "New York"):
            p.run_request(self.store, protocol, self.registry, self.policies, bad, now=REVIEWED)
        subject = p.run_request(self.store, protocol, self.registry, self.policies, run, now=REVIEWED)
        run["review_receipt_id"] = self.attest("prospective_run", subject)
        first = p.import_run(self.store, protocol, self.registry, self.policies, run, now=REVIEWED)
        self.assertEqual(first, p.import_run(self.store, protocol, self.registry, self.policies, run, now=LATER))
        with self.assertRaisesRegex(ValueError, "already consumed"):
            p.run_request(self.store, protocol, self.registry, self.policies, dict(run, run_id="another"), now=LATER)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["manual_pilot"]["eligible_scheduled_sessions"], 0)

    def clock_proofs(self, protocol, doc, *, availability_basis="dissemination_log",
                     precision="second", timezone_basis="explicit_offset", imported_at=REVIEWED):
        raw = put_receipt(self.store, "synthetic_clock_log",
                          {"synthetic": True, "native_ref": reference(doc), "artifact_hash": digest("synthetic log")},
                          observed_at=END)["receipt_id"]
        availability = {
            "native_ref": reference(doc), "source_proof_receipt_id": raw,
            "availability_basis": availability_basis, "source_field": "synthetic dissemination log available_at",
            "clock": {"at": "2026-10-21T09:59:00Z", "precision": precision,
                      "timezone_basis": timezone_basis, "uncertainty_seconds": 0}}
        acquisition = {
            "native_ref": reference(doc), "source_proof_receipt_id": raw, "source_cycle_id": "first:base",
            "started_clock": {"at": "2026-10-21T09:59:59Z", "precision": "second",
                              "timezone_basis": "recorded_utc_clock", "uncertainty_seconds": 0},
            "completed_clock": {"at": OBSERVED, "precision": "second",
                                "timezone_basis": "recorded_utc_clock", "uncertainty_seconds": 0}}
        packet = self.store.conn.execute(
            "SELECT event_id,revision_id,packet_hash FROM event_packets WHERE event_id IN "
            "(SELECT event_id FROM event_evidence WHERE evidence_id=? AND evidence_revision=?) ORDER BY rowid DESC LIMIT 1",
            (doc["evidence_id"], doc["revision_id"])).fetchone()
        candidate = {"native_ref": reference(doc), "source_proof_receipt_id": raw,
                     "source_cycle_id": "first:base", "event_id": packet["event_id"],
                     "event_revision": packet["revision_id"], "packet_hash": packet["packet_hash"],
                     "clock": {"at": "2026-10-21T10:00:01Z", "precision": "second",
                               "timezone_basis": "recorded_utc_clock", "uncertainty_seconds": 0}}
        result = []
        for kind, value in (("availability", availability), ("acquisition", acquisition), ("candidate", candidate)):
            subject = timing.clock_proof_request(self.store, protocol, self.registry, self.policies,
                                                  kind, value, now=imported_at)
            value["review_receipt_id"] = self.attest("source_" + kind + "_timing", subject)
            result.append(timing.import_clock_proof(self.store, protocol, self.registry, self.policies,
                                                    kind, value, now=imported_at)["receipt_id"])
        return result

    def test_generic_run_receipt_with_attested_but_nonexistent_cycles_is_excluded(self):
        protocol, _, _, _, _ = self.simple_closed()
        run = {"run_id": "generic-synthetic-run", "started_at": START, "completed_at": END,
               "execution_mode": "scheduled", "session": "2026-10-21", "scheduled_for": START,
               "source_cycle_ids": ["first:base", "last:base"], "exit_status": 0,
               "invocation_hash": digest("synthetic-invocation"), "scheduler_log_hash": digest("synthetic-log")}
        subject = p.run_request(self.store, protocol, self.registry, self.policies, run, now=REVIEWED)
        subject["source_cycle_ids"] = ["synthetic-nonexistent-cycle"]
        review = self.attest("prospective_run", subject)
        imported = put_receipt(self.store, p.KINDS["run"],
            dict(subject, review_receipt_id=review, submitted_hash=digest(run)),
            observed_at=REVIEWED, refs=[protocol, review])
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("run_source_cycle_scope_or_attestation_invalidated", report["pending_reason_codes"])
        self.assertEqual(report["manual_pilot"]["descriptive_scheduled_sessions"], 0)
        self.assertEqual(report["manual_pilot"]["eligible_scheduled_sessions"], 0)
        self.assertEqual(get_receipt(self.store, imported["receipt_id"]), imported)

    def test_timing_uses_referenced_clocks_and_synthetic_counts_stay_zero(self):
        protocol, doc, event, coverage, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit, event))
        available, acquired, candidate = self.clock_proofs(protocol, doc)
        record = {"unit_id": unit["unit_id"], "availability_receipt_id": available,
                  "acquisition_receipt_id": acquired, "candidate_receipt_id": candidate, "coverage_receipt_ids": [coverage]}
        result = timing.import_timing(self.store, protocol, self.registry, self.policies, record, now=REVIEWED)
        self.assertEqual(result["payload"]["status"], "verified", result["payload"]["reason_codes"])
        self.assertEqual(result["payload"]["conservative_latency_seconds"], 62)
        report = timing.timing_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["n"], 0); self.assertEqual(report["status"], "pending")
        self.assertIn("not_real_prospective_timing", report["excluded"][0]["reason_codes"])
        self.assertEqual(result, timing.import_timing(self.store, protocol, self.registry, self.policies,
                                                       record, now=LATER))

    def test_timing_correction_is_append_only_and_must_supersede_same_unit(self):
        protocol, doc, event, coverage, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit, event))
        first_input = {"unit_id": unit["unit_id"]}
        first = timing.import_timing(self.store, protocol, self.registry, self.policies, first_input, now=REVIEWED)
        a, b, c = self.clock_proofs(protocol, doc)
        fixed = {"unit_id": unit["unit_id"], "availability_receipt_id": a,
                 "acquisition_receipt_id": b, "candidate_receipt_id": c, "coverage_receipt_ids": [coverage]}
        with self.assertRaisesRegex(ValueError, "supersede current"):
            timing.import_timing(self.store, protocol, self.registry, self.policies, fixed, now=REVIEWED)
        fixed["supersedes"] = first["receipt_id"]
        second = timing.import_timing(self.store, protocol, self.registry, self.policies, fixed, now=REVIEWED)
        self.assertEqual(second["payload"]["status"], "verified")
        self.assertEqual(first, timing.import_timing(self.store, protocol, self.registry, self.policies, first_input, now=LATER))
        self.assertEqual(second, timing.import_timing(self.store, protocol, self.registry, self.policies, fixed, now=LATER))
        self.assertEqual(len(list_receipts(self.store, kind=timing.TIMING_KIND)), 2)

    def test_unknown_acquisition_start_cannot_be_hidden_by_precise_completion(self):
        protocol, doc, event, coverage, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit, event))
        a, b, c = self.clock_proofs(protocol, doc)
        previous = get_receipt(self.store, b)["payload"]
        value = {k: previous[k] for k in ("native_ref", "source_proof_receipt_id", "source_cycle_id", "completed_clock")}
        value["started_clock"] = None
        subject = timing.clock_proof_request(self.store, protocol, self.registry, self.policies, "acquisition", value, now=REVIEWED)
        value["review_receipt_id"] = self.attest("source_acquisition_timing", subject)
        ambiguous = timing.import_clock_proof(self.store, protocol, self.registry, self.policies, "acquisition", value, now=REVIEWED)
        result = timing.import_timing(self.store, protocol, self.registry, self.policies,
                                      {"unit_id": unit["unit_id"], "availability_receipt_id": a,
                                       "acquisition_receipt_id": ambiguous["receipt_id"], "candidate_receipt_id": c,
                                       "coverage_receipt_ids": [coverage]}, now=REVIEWED)
        self.assertIn("acquisition_start_clock_missing", result["payload"]["reason_codes"])
        self.assertIsNone(result["payload"]["conservative_latency_seconds"])

    def test_timing_rejects_scalar_and_continuous_source_flags(self):
        protocol, _, _, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        for field in ("continuous_source", "latency_seconds", "source_available_at", "candidate_ready_at"):
            with self.assertRaisesRegex(ValueError, "caller-supplied"):
                timing.import_timing(self.store, protocol, self.registry, self.policies,
                                     {"unit_id": unit["unit_id"], field: 1}, now=REVIEWED)
        record = timing.import_timing(self.store, protocol, self.registry, self.policies,
                                      {"unit_id": unit["unit_id"]}, now=REVIEWED)
        self.assertEqual(record["payload"]["status"], "descriptive_only")
        self.assertIsNone(record["payload"]["conservative_latency_seconds"])

    def test_date_only_publication_and_unknown_timezone_never_get_latency_credit(self):
        for basis, precision, zone in (
                ("publication_only", "second", "explicit_offset"),
                ("dissemination_log", "date", "explicit_offset"),
                ("dissemination_log", "second", "unknown")):
            with self.subTest(basis=basis, precision=precision, zone=zone):
                with tempfile.TemporaryDirectory() as tmp:
                    original = self.store
                    self.store = EventStore(Path(tmp) / "events.db")
                    try:
                        protocol, doc, event, coverage, closed = self.simple_closed()
                        unit = closed["payload"]["selected"][0]
                        self.adjudicate(protocol, self.decision(unit, event))
                        a, b, c = self.clock_proofs(protocol, doc, availability_basis=basis,
                                                 precision=precision, timezone_basis=zone)
                        result = timing.import_timing(
                            self.store, protocol, self.registry, self.policies,
                            {"unit_id": unit["unit_id"], "availability_receipt_id": a,
                             "acquisition_receipt_id": b, "candidate_receipt_id": c,
                             "coverage_receipt_ids": [coverage]}, now=REVIEWED)
                        self.assertEqual(result["payload"]["status"], "descriptive_only")
                        self.assertIsNone(result["payload"]["conservative_latency_seconds"])
                    finally:
                        self.store.close(); self.store = original

    def test_raw_evaluation_flags_cannot_mature_timing_or_release(self):
        manifest = {"implementation_hash": "fixture", "source_scope_hash": "fixture",
                    "corpus_hash": "fixture", "frozen_at": FREEZE,
                    "source_coverage_receipt": True, "untouched_test_receipt": {"approved": True}}
        case = {"event_case_id": "one", "evidence_kind": "prospective", "adjudicator": "fixture",
                "adjudication_receipt": "placeholder", "correct": True, "meaningfully_exercised": True,
                "observed_at": OBSERVED, "implementation_hash": "fixture",
                "source_scope_hash": "fixture", "corpus_hash": "fixture",
                "continuous_source": True, "latency_seconds": 0}
        report = evaluate(manifest, [case])
        self.assertEqual(report["timing"]["n"], 0)
        self.assertEqual(report["timing"]["status"], "pending")
        self.assertIn("receipt_backed_protocol_required", report["manual_pilot"]["reason_codes"])
        self.assertEqual(report["evidence_verification"]["status"], "descriptive_inputs_only")

    def seal(self, protocol, *, at=REVIEWED):
        self.close_publications(protocol, at=at)
        subject = holdout.seal_request(self.store, protocol, self.registry, self.policies, now=at)
        receipt = self.attest("untouched_custody", subject, at=at)
        return holdout.seal_partition(self.store, protocol, self.registry, self.policies, receipt, now=at)

    def close_publications(self, protocol, *, at=REVIEWED):
        subject = publications.closure_request(self.store, protocol, self.registry, self.policies, now=at)
        review = self.attest("prospective_publication_closure", subject, at=at)
        return publications.close_inventory(self.store, protocol, self.registry, self.policies, review, now=at)

    def publication_decision(self, protocol, unit, *, at=None, **changes):
        decision = {"unit_id": unit["unit_id"], "correct": True, "critical_error": False,
                    "meaningfully_exercised": True, "unknown_issuer": True, "issuer_ids": [],
                    "reviewer": "synthetic-custodian", "reviewed_at": REVIEWED}
        decision.update(changes)
        subject = publications.adjudication_request(self.store, protocol, self.registry, self.policies,
                                                     decision, now=at or decision["reviewed_at"])
        decision["review_receipt_id"] = self.attest("prospective_publication_adjudication", subject,
                                                   at=decision["reviewed_at"], reviewer=decision["reviewer"])
        return publications.adjudicate(self.store, protocol, self.registry, self.policies,
                                       decision, now=at or decision["reviewed_at"]), decision

    def test_untouched_seal_adjudication_controlled_consumption_is_single_use(self):
        protocol, _, _, _, closed = self.simple_closed(partition="untouched")
        seal = self.seal(protocol)
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit))
        publication = publications.closed_inventory(self.store, protocol)["payload"]["units"][0]
        self.publication_decision(protocol, publication)
        subject = holdout.consumption_request(self.store, protocol, self.registry, self.policies, now=LATER)
        review = self.attest("untouched_evaluation", subject, at=LATER)
        consumed = holdout.consume_partition(self.store, protocol, self.registry, self.policies,
                                             {"review_receipt_id": review}, now=LATER)
        self.assertTrue(consumed["payload"]["single_candidate_use"])
        self.assertEqual(holdout.untouched_state(self.store, protocol, self.registry, self.policies,
                                               now=LATER)["status"], "consumed")
        self.assertEqual(consumed, holdout.consume_partition(self.store, protocol, self.registry, self.policies,
                                                            {"review_receipt_id": review}, now=LATER))
        with self.assertRaisesRegex(ValueError, "consumed"):
            holdout.consume_partition(self.store, protocol, self.registry, self.policies,
                                      {"review_receipt_id": "another"}, now=LATER)
        with self.assertRaisesRegex(ValueError, "synthetic"):
            holdout.verify_consumption(self.store, consumed["receipt_id"], self.registry, self.policies, now=LATER)
        self.assertTrue(get_receipt(self.store, seal["receipt_id"]))

    def test_custody_request_review_and_actual_recording_clocks_can_differ(self):
        protocol, _, _, _, closed = self.simple_closed(partition="untouched")
        self.close_publications(protocol)
        subject = holdout.seal_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        seal_review = self.attest("untouched_custody", subject)
        sealed_at = "2026-10-21T18:05:00Z"
        sealed = holdout.seal_partition(self.store, protocol, self.registry, self.policies, seal_review, now=sealed_at)
        self.assertEqual(sealed["payload"]["sealed_at"], sealed_at)
        adjudicated_at = "2026-10-21T18:10:00Z"
        self.adjudicate(protocol, self.decision(closed["payload"]["selected"][0], reviewed_at=adjudicated_at), at=adjudicated_at)
        unit = publications.closed_inventory(self.store, protocol)["payload"]["units"][0]
        self.publication_decision(protocol, unit, reviewed_at=adjudicated_at)
        subject = holdout.consumption_request(self.store, protocol, self.registry, self.policies, now=adjudicated_at)
        review = self.attest("untouched_evaluation", subject, at="2026-10-21T18:30:00Z")
        consumed = holdout.consume_partition(self.store, protocol, self.registry, self.policies,
                                             {"review_receipt_id": review}, now=LATER)
        self.assertEqual(consumed["payload"]["consumed_at"], LATER)
        self.assertEqual(holdout.untouched_state(self.store, protocol, self.registry, self.policies,
                                               now=LATER)["status"], "consumed")

    def test_untouched_uncontrolled_access_and_missing_adjudications_block_consumption(self):
        protocol, _, _, _, _ = self.simple_closed(partition="untouched")
        self.seal(protocol)
        with self.assertRaisesRegex(ValueError, "missing adjudications"):
            holdout.consumption_request(self.store, protocol, self.registry, self.policies, now=LATER)
        receipt = holdout.record_access(
            self.store, protocol, self.registry, self.policies,
            {"actor": "developer", "purpose": "development", "at": LATER}, now=LATER)
        self.assertTrue(receipt["payload"]["contaminates"])
        state = holdout.untouched_state(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(state["status"], "contaminated_or_invalid")
        self.assertFalse(state["external_custody_independently_proven"])
        with self.assertRaisesRegex(ValueError, "not clean"):
            holdout.consumption_request(self.store, protocol, self.registry, self.policies, now=LATER)

    def test_untouched_changed_policy_is_visible_and_cannot_be_consumed(self):
        protocol, _, _, _, _ = self.simple_closed(partition="untouched")
        self.seal(protocol)
        changed = deepcopy(self.policies); changed[SOURCE]["version"] = "new"
        state = holdout.untouched_state(self.store, protocol, self.registry, changed, now=LATER)
        self.assertIn("implementation_registry_or_policy_changed", state["reason_codes"])
        with self.assertRaises(ValueError):
            holdout.consumption_request(self.store, protocol, self.registry, changed, now=LATER)

    def test_untouched_access_requires_exact_subject_and_rechecks_expiry(self):
        protocol, _, _, _, closed = self.simple_closed(partition="untouched")
        self.seal(protocol)
        subject = {"protocol_id": protocol, "inventory_hash": closed["payload"]["inventory_hash"],
                   "actor": "synthetic-custodian", "at": REVIEWED, "purpose": "custody_audit"}
        review = self.attest("untouched_custody_audit", subject, expires_at="2026-10-21T18:30:00Z")
        row = holdout.record_access(self.store, protocol, self.registry, self.policies,
                                   {"actor": "synthetic-custodian", "purpose": "custody_audit", "at": REVIEWED,
                                    "attestation_id": review, "attested_subject": subject}, now=REVIEWED)
        self.assertFalse(row["payload"]["contaminates"])
        self.assertEqual(holdout.untouched_state(self.store, protocol, self.registry, self.policies,
                                               now=REVIEWED)["status"], "sealed")
        state = holdout.untouched_state(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("custody_access_attestation_stale_or_revoked", state["reason_codes"])
        self.assertNotIn("attested_subject", state["access_log"][0])

    def test_untouched_access_cannot_reuse_attestation_for_another_partition(self):
        protocol, _, _, _, closed = self.simple_closed(partition="untouched")
        self.seal(protocol)
        subject = {"protocol_id": "another-partition", "inventory_hash": closed["payload"]["inventory_hash"]}
        review = self.attest("untouched_custody_audit", subject)
        row = holdout.record_access(self.store, protocol, self.registry, self.policies,
                                   {"actor": "synthetic-custodian", "purpose": "custody_audit", "at": REVIEWED,
                                    "attestation_id": review, "attested_subject": subject}, now=REVIEWED)
        self.assertTrue(row["payload"]["contaminates"])

    def test_publication_census_is_independent_of_recall_sample_and_approval(self):
        protocol = self.freeze(sampling={"method": "hash_modulus", "seed": "synthetic-prespecified", "modulus": 2, "buckets": [0]})
        refs = []
        for i in range(8):
            doc, _ = self.ingest(native="proposal-" + str(i))
            refs.append(reference(doc))
        _, source = self.enumerate_close(protocol, refs)
        self.assertGreater(len(source["payload"]["selected"]), 0)
        self.assertLess(len(source["payload"]["selected"]), len(refs))
        census = self.close_publications(protocol)
        self.assertEqual(len(census["payload"]["units"]), 8)
        self.assertFalse(census["payload"]["sampled_recall_filter"])
        self.assertFalse(census["payload"]["approved_only_filter"])
        for unit in census["payload"]["units"]:
            self.publication_decision(protocol, unit, correct=False, meaningfully_exercised=False)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report["publication_inventory"]["pending"], [])
        self.assertEqual(report["publication_inventory"]["expected"], 8)
        self.assertEqual(report["fixture_or_nonprospective_summary"]["publication_adjudications"], 8)
        self.assertEqual(report["precision"]["n"], 0)

    def test_unadjudicated_proposals_keep_precision_gate_pending(self):
        protocol, _, event, _, closed = self.simple_closed()
        # A recall label does not close or adjudicate the precision stream.
        self.adjudicate(protocol, self.decision(closed["payload"]["selected"][0], event,
                                               proposed_publication=False))
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("publication_census_not_closed", report["pending_reason_codes"])
        census = self.close_publications(protocol)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("publication_adjudications_incomplete", report["pending_reason_codes"])
        self.assertEqual(report["publication_inventory"]["pending"], [census["payload"]["units"][0]["unit_id"]])

    def test_publication_replay_correction_and_revocation_preserve_denominator(self):
        protocol, _, _, _, _ = self.simple_closed()
        census = self.close_publications(protocol)
        unit = census["payload"]["units"][0]
        first, submitted = self.publication_decision(protocol, unit)
        self.assertEqual(first, publications.adjudicate(self.store, protocol, self.registry, self.policies, submitted, now=LATER))
        second, _ = self.publication_decision(protocol, unit, correct=False,
                                              reviewed_at=LATER, supersedes=first["receipt_id"])
        self.assertEqual(len(publications.latest_adjudications(self.store, protocol)), 1)
        self.assertEqual(publications.latest_adjudications(self.store, protocol)[0], second)
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": second["payload"]["review_receipt_id"]}, observed_at=LATER)
        audit, valid = publications.verified_records(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(audit["expected"], 1); self.assertEqual(valid, [])
        self.assertIn("publication_adjudications_invalidated", audit["reason_codes"])

    def test_revised_same_native_disclosure_keeps_one_origin_and_all_proposals(self):
        protocol = self.freeze()
        first, _ = self.ingest(native="revision-lineage")
        second, _ = self.ingest(native="revision-lineage", at="2026-10-21T11:00:00Z", cycle="revision-2",
                                text="Moderna reports INTerpath-001 did not meet its primary endpoint.")
        _, closed = self.enumerate_close(protocol, [reference(first), reference(second)])
        self.assertEqual(len(closed["payload"]["selected"]), 1)
        self.assertEqual(len(closed["payload"]["selected"][0]["native_refs"]), 2)
        census = self.close_publications(protocol)
        self.assertEqual(len(census["payload"]["units"]), 1)
        self.assertEqual(len(census["payload"]["units"][0]["proposals"]), 2)
        proposals = census["payload"]["units"][0]["proposals"]
        missing = publications.inspect_packet(self.store, protocol, self.registry, self.policies, proposals[0], now=LATER)
        self.assertIsNone(missing["event"])
        self.assertNotIn("text",missing["documents"][0])
        from omni.rights import record_rights, VERSION as RIGHTS
        from omni.lifecycle import implementation_hash
        for field in ("source.text","claim.value","claim.scope"):
            body={"contract":RIGHTS,"source_ref":reference(first),"field":field,"purpose":"review_display",
                "audience":"local_operator","rightsholder":"synthetic fixture owner","attribution":"synthetic example source",
                "retention_rule":"disposable fixture only","policy_hash":digest(self.policies),"implementation_hash":implementation_hash(),
                "decision":"approved","transformation":"exact_excerpt" if field=="source.text" else "derived_summary",
                "evidence_kind":"synthetic","effective_at":LATER,"expires_at":"2026-10-22T19:00:00Z"}
            if field=="source.text": body["max_characters"]=len(first["text"])
            att=self.attest("field_rights",body,at=LATER,bindings={"policy_hash":digest(self.policies),
                "implementation_hash":implementation_hash(),"source_policy_id":SOURCE})
            record_rights(self.store,{**body,"attestation_ref":att},self.policies,now=LATER,allow_synthetic=True)
        old = publications.inspect_packet(self.store, protocol, self.registry, self.policies, proposals[0], now=LATER)
        self.assertEqual(old["documents"][0]["text"], first["text"])
        self.assertNotIn("support",old["event"]["claims"][0])
        self.assertFalse(old["current_publication_approval"])
        denied = deepcopy(self.policies); denied[SOURCE]["uses"]["review_display"] = False
        hidden = publications.inspect_packet(self.store, protocol, self.registry, denied, proposals[0], now=LATER)
        self.assertIsNone(hidden["event"]); self.assertNotIn("text",hidden["documents"][0])
        self.publication_decision(protocol, census["payload"]["units"][0], correct=False)
        audit, labels = publications.verified_records(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(audit["emitted_packet_revisions"], 2)
        self.assertFalse(labels[0][0]["payload"]["correct"])

    def test_real_feed_gate_requires_actual_traversal_and_retention_proof(self):
        protocol, _, _, coverage, _ = self.simple_closed()
        manifest = dict(p.protocol_state(self.store, protocol), evidence_kind="operator_evidence")
        _, _, reasons = p._coverage_check(self.store, manifest, coverage, now=LATER)
        self.assertIn("actual_feed_traversal_proof_missing", reasons)
        self.assertIn("source_observation_not_bound_to_feed_traversal", reasons)
        self.assertIn("source_interval_retention_attestation_missing_or_stale", reasons)
        self.assertIn("coverage_not_operator_evidence", reasons)

    def retention(self, protocol, *, valid_until=END, minimum=900):
        record = {"source_id": SOURCE, "route_id": "synthetic-feed", "family": "clinical_result",
                  "valid_from": START, "valid_until": valid_until, "minimum_retention_seconds": minimum,
                  "record_visibility": "retained_change_log",
                  "external_reference": {"reference": "synthetic-test-only:retention-contract",
                                         "sha256": digest("synthetic retention")}}
        subject = intervals.retention_request(self.store, protocol, self.registry, self.policies, record, now=REVIEWED)
        record["review_receipt_id"] = self.attest("source_retention_contract", subject)
        return intervals.import_retention(self.store, protocol, self.registry, self.policies, record, now=REVIEWED)

    def test_retention_interval_has_exact_scope_clocks_and_revocable_review(self):
        end = "2026-10-21T09:15:00Z"
        protocol = self.freeze(window_end=end)
        ref = {"source_id": SOURCE, "native_id": "retained-unknown", "revision_id": digest("retained")}
        coverage = self.coverage([ref], last=end, captured="2026-10-21T09:05:00Z")
        retention = self.retention(protocol, valid_until=end)
        value = {"coverage_receipt_id": coverage, "retention_receipt_id": retention["receipt_id"]}
        subject = intervals.interval_request(self.store, protocol, self.registry, self.policies, value, now=REVIEWED)
        self.assertEqual(subject["reason_codes"], [])
        self.assertEqual(subject["maximum_observed_poll_gap_seconds"], 600)
        value["review_receipt_id"] = self.attest("source_interval_coverage", subject)
        proof = intervals.import_interval(self.store, protocol, self.registry, self.policies, value, now=REVIEWED)
        manifest = p.protocol_state(self.store, protocol)
        self.assertEqual(intervals.verified_interval(self.store, manifest, coverage, now=LATER), proof)
        self.assertFalse(proof["payload"]["continuous_source"])
        self.assertEqual(intervals.import_interval(self.store, protocol, self.registry, self.policies, value, now=LATER), proof)
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": retention["payload"]["review_receipt_id"]}, observed_at=LATER)
        with self.assertRaisesRegex(ValueError, "missing, stale or revoked"):
            intervals.verified_interval(self.store, manifest, coverage, now=LATER)

    def test_short_retention_or_missed_poll_cadence_cannot_certify_interval(self):
        protocol, _, _, coverage, _ = self.simple_closed()
        retention = self.retention(protocol, minimum=60)
        record = {"coverage_receipt_id": coverage, "retention_receipt_id": retention["receipt_id"]}
        subject = intervals.interval_request(self.store, protocol, self.registry, self.policies, record, now=REVIEWED)
        self.assertIn("source_retention_shorter_than_poll_gap", subject["reason_codes"])
        self.assertIn("observed_poll_gap_exceeds_frozen_cadence", subject["reason_codes"])
        record["review_receipt_id"] = self.attest("source_interval_coverage", subject)
        with self.assertRaisesRegex(ValueError, "cannot be verified"):
            intervals.import_interval(self.store, protocol, self.registry, self.policies, record, now=REVIEWED)

    def test_known_issuer_uses_frozen_identity_and_unapproved_real_scope_is_rejected(self):
        protocol, _, event, _, closed = self.simple_closed()
        row, _ = self.adjudicate(protocol, self.decision(closed["payload"]["selected"][0], event, unknown=False))
        self.assertEqual(row["payload"]["issuer_ids"], ["cik:0001682852"])
        bad = deepcopy(self.policies); bad[SOURCE]["uses"]["local_analysis"] = False
        with self.assertRaisesRegex(ValueError, "source-purpose"):
            p.protocol_request(self.registry, bad, [], self.spec(evidence_kind="operator_evidence"), now=FREEZE)
        with self.assertRaisesRegex(ValueError, "timing minimum"):
            p.protocol_request(self.registry, self.policies, [], self.spec(evidence_kind="operator_evidence"), now=FREEZE)

    def test_missing_prospective_inventory_stays_pending_without_invented_misses(self):
        protocol = self.freeze()
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertIn("prospective_inventory_not_closed", report["pending_reason_codes"])
        self.assertEqual(report["recall"]["misses"], [])
        self.assertEqual(report["recall"]["overall"]["n"], 0)

    def test_evaluation_receipt_rechecks_exact_inputs_and_rejects_synthetic_gate(self):
        protocol, _, event, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        first_label, _ = self.adjudicate(protocol, self.decision(unit, event))
        census = self.close_publications(protocol)
        self.publication_decision(protocol, census["payload"]["units"][0])
        report = p.record_evaluation(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(report, p.record_evaluation(self.store, protocol, self.registry, self.policies, now=LATER))
        checked = p.verify_evaluation_receipt(self.store, report["receipt_id"], self.registry, self.policies,
                                              now=LATER, require_real=False)
        self.assertEqual(checked["current_report"]["precision"]["n"], 0)
        with self.assertRaisesRegex(ValueError, "synthetic"):
            p.verify_evaluation_receipt(self.store, report["receipt_id"], self.registry, self.policies, now=LATER)
        self.adjudicate(protocol, self.decision(unit, event, reviewed_at=LATER,
                                               supersedes=first_label["receipt_id"], correct=False), at=LATER)
        with self.assertRaisesRegex(ValueError, "inputs? receipts changed"):
            p.verify_evaluation_receipt(self.store, report["receipt_id"], self.registry, self.policies,
                                        now=LATER, require_real=False)

    def test_delayed_imports_cannot_backdate_gold_or_scheduled_run_counts(self):
        protocol, _, event, _, closed = self.simple_closed()
        self.adjudicate(protocol, self.decision(closed["payload"]["selected"][0], event), at=LATER)
        run = {"run_id": "delayed-import", "started_at": START, "completed_at": END,
               "execution_mode": "scheduled", "session": "2026-10-21", "scheduled_for": START,
               "source_cycle_ids": ["first:base", "last:base"], "exit_status": 0,
               "invocation_hash": digest("synthetic-invocation"), "scheduler_log_hash": digest("synthetic-log")}
        subject = p.run_request(self.store, protocol, self.registry, self.policies, run, now=REVIEWED)
        run["review_receipt_id"] = self.attest("prospective_run", subject)
        p.import_run(self.store, protocol, self.registry, self.policies, run, now=LATER)
        before = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        after = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(before["inventory"]["adjudicated"], 0)
        self.assertEqual(before["manual_pilot"]["descriptive_scheduled_sessions"], 0)
        self.assertIn("future_protocol_receipts_present", before["pending_reason_codes"])
        self.assertEqual(after["inventory"]["adjudicated"], 1)
        self.assertEqual(before["manual_pilot"]["eligible_scheduled_sessions"], 0)
        self.assertEqual(after["manual_pilot"]["eligible_scheduled_sessions"], 0)

    def test_future_corrections_do_not_supersede_prior_as_of_heads(self):
        protocol, _, event, _, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        first, _ = self.adjudicate(protocol, self.decision(unit, event))
        second, _ = self.adjudicate(protocol, self.decision(
            unit, event, correct=False, supersedes=first["receipt_id"]), at=LATER)
        self.assertEqual(p.latest_adjudications(self.store, protocol, at=REVIEWED), [first])
        self.assertEqual(p.latest_adjudications(self.store, protocol, at=LATER), [second])
        census = self.close_publications(protocol)
        publication_unit = census["payload"]["units"][0]
        first_publication, _ = self.publication_decision(protocol, publication_unit)
        second_publication, _ = self.publication_decision(
            protocol, publication_unit, correct=False, supersedes=first_publication["receipt_id"], at=LATER)
        _, before = publications.verified_records(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        _, after = publications.verified_records(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(before[0][0], first_publication)
        self.assertEqual(after[0][0], second_publication)
        self.assertTrue(before[0][0]["payload"]["correct"])
        self.assertFalse(after[0][0]["payload"]["correct"])

    def test_future_timing_proofs_and_measurements_cannot_enter_earlier_report(self):
        protocol, doc, event, coverage, closed = self.simple_closed()
        unit = closed["payload"]["selected"][0]
        self.adjudicate(protocol, self.decision(unit, event))
        available, acquired, candidate = self.clock_proofs(protocol, doc, imported_at=LATER)
        record = {"unit_id": unit["unit_id"], "availability_receipt_id": available,
                  "acquisition_receipt_id": acquired, "candidate_receipt_id": candidate,
                  "coverage_receipt_ids": [coverage]}
        result = timing.import_timing(self.store, protocol, self.registry, self.policies, record, now=LATER)
        self.assertEqual(result["payload"]["status"], "verified")
        with self.assertRaisesRegex(ValueError, "after the report clock"):
            timing._proof(self.store, available, timing.AVAILABILITY_KIND,
                          p.protocol_state(self.store, protocol), now=REVIEWED)
        before = timing.timing_report(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        after = timing.timing_report(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(before["excluded"], [])
        self.assertEqual(len(after["excluded"]), 1)
        self.assertEqual(after["n"], 0)

    def test_future_source_closure_and_coverage_are_not_visible_at_earlier_clock(self):
        protocol = self.freeze()
        ref = {"source_id": SOURCE, "native_id": "delayed-close", "revision_id": digest("delayed")}
        coverage = self.coverage([ref])
        p.enumerate_sources(self.store, protocol, self.registry, self.policies, [coverage], now=END)
        request = p.closure_request(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        review = self.attest("prospective_inventory_closure", request)
        p.close_inventory(self.store, protocol, self.registry, self.policies, review, now=LATER)
        report = p.evaluation_report(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        self.assertFalse(report["inventory"]["closed"])
        self.assertEqual(report["inventory"]["expected"], 0)
        original = get_receipt(self.store, coverage)
        delayed = put_receipt(self.store, original["kind"], original["payload"], observed_at=LATER,
                              refs=original["refs"])
        with self.assertRaisesRegex(ValueError, "after the report clock"):
            p._coverage_check(self.store, p.protocol_state(self.store, protocol), delayed["receipt_id"], now=REVIEWED)

    def test_untouched_future_access_is_excluded_and_remains_explicitly_pending(self):
        protocol, _, _, _, _ = self.simple_closed(partition="untouched")
        self.seal(protocol)
        holdout.record_access(self.store, protocol, self.registry, self.policies,
                              {"actor": "synthetic-custodian", "purpose": "development", "at": REVIEWED}, now=LATER)
        before = holdout.untouched_state(self.store, protocol, self.registry, self.policies, now=REVIEWED)
        after = holdout.untouched_state(self.store, protocol, self.registry, self.policies, now=LATER)
        self.assertEqual(before["access_receipt_ids"], [])
        self.assertIn("future_partition_receipts_present", before["reason_codes"])
        self.assertIn("partition_exposed_outside_controlled_evaluation", after["reason_codes"])
        self.assertEqual(len(after["access_receipt_ids"]), 1)

    def test_legacy_migration_is_atomic_idempotent_and_preserves_original_bytes(self):
        with self.store.conn:
            self.store.conn.execute("INSERT INTO pilot_records VALUES(?,?,?,?,?)",
                                    ("legacy-1", "legacy-pilot", "gold", FREEZE,
                                     encode({"evidence_kind": "historical_replay", "event_id": "old"})))
        original = tuple(self.store.conn.execute("SELECT * FROM pilot_records").fetchone())
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            p.migrate_legacy(self.store, now=LATER, fail_at="after_first")
        self.assertEqual(list_receipts(self.store, kind=p.KINDS["legacy_history"]), [])
        a = p.migrate_legacy(self.store, now=LATER)
        b = p.migrate_legacy(self.store, now="2026-10-22T12:00:00Z")
        self.assertEqual(a, b)
        self.assertEqual(tuple(self.store.conn.execute("SELECT * FROM pilot_records").fetchone()), original)
        self.assertEqual(a["real_prospective_count"], 0)
        self.assertEqual(a["original_records_modified"], 0)

    def test_public_command_path_freeze_enumerate_close_adjudicate_report(self):
        sources = self.path / "sources.json"; sources.write_text(json.dumps({"policies": self.policies}))
        coverage_path = self.path / "coverage.json"; coverage_path.write_text("[]")
        def cli(name, *args, at=REVIEWED):
            output = io.StringIO()
            with redirect_stdout(output):
                code = command(["--db", str(self.path / "events.db"), "--sources", str(sources),
                                "--coverage", str(coverage_path), name, *args], clock=lambda: at)
            self.assertEqual(code, 0)
            return json.loads(output.getvalue())
        spec = self.spec(); input_path = self.path / "input.json"
        input_path.write_text(json.dumps(spec))
        requested = cli("prospective-request", str(input_path), at=FREEZE)
        spec["review_receipt_id"] = self.attest("prospective_protocol", requested, at=FREEZE)
        input_path.write_text(json.dumps(spec))
        protocol = cli("prospective-freeze", str(input_path), at=FREEZE)["receipt_id"]
        ref = {"source_id": SOURCE, "native_id": "cli-unknown", "revision_id": digest("cli")}
        source_receipt = self.coverage([ref])
        input_path.write_text(json.dumps({"coverage_receipt_ids": [source_receipt]}))
        cli("prospective-enumerate", protocol, str(input_path), at=END)
        request = cli("prospective-closure-request", protocol)
        review = self.attest("prospective_inventory_closure", request)
        closed = cli("prospective-close", protocol, "--review-receipt", review)
        decision = self.decision(closed["payload"]["selected"][0])
        input_path.write_text(json.dumps(decision))
        requested = cli("prospective-adjudication-request", protocol, str(input_path))
        decision["review_receipt_id"] = self.attest("prospective_adjudication", requested)
        input_path.write_text(json.dumps(decision))
        cli("prospective-adjudicate", protocol, str(input_path))
        report = cli("prospective-report", protocol, at=LATER)
        self.assertEqual(report["inventory"]["expected"], 1)
        self.assertEqual(report["inventory"]["adjudicated"], 1)
        self.assertEqual(report["recall"]["overall"]["n"], 0)
        self.assertFalse(report["automatic_activation"])
        publication_subject = cli("prospective-publication-closure-request", protocol)
        publication_review = self.attest("prospective_publication_closure", publication_subject)
        cli("prospective-publication-close", protocol, publication_review)
        recorded = cli("prospective-record-evaluation", protocol, at=LATER)
        self.assertTrue(recorded["payload"]["report"]["publication_inventory"]["closed"])
        self.assertEqual(recorded["payload"]["report"]["precision"]["n"], 0)
        with self.assertRaisesRegex(ValueError, "synthetic"):
            cli("prospective-verify-evaluation", recorded["receipt_id"], at=LATER)


if __name__ == "__main__":
    unittest.main()
