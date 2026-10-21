"""Synthetic operating workflow: rights, brief, costs, feedback and typed gates."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from omni.attestations import VERSION as ATTESTATION
from omni.briefs import REQUIRED, export_brief, preview_brief, safe_packet, search_history, validate_brief
from omni.gates import dependent_gates
from omni.identities import digest, load_registry
from omni.lifecycle import implementation_hash, reference
from omni.operating import CATEGORIES, VERSION as OPERATING, cost_components, economics_report, record_observation, record_economics_report
from omni.receipts import put_receipt
from omni.review import record_review
from omni.rights import FIELDS, VERSION as RIGHTS, field_decision, record_rights, revoke_rights
from omni.runner import analyze
from omni.store import EventStore
from test_omni_events import NOW, POLICIES, POLICY, make_doc

END="2026-10-18T12:01:00Z"
AFTER="2026-10-18T12:02:00Z"
EXPIRY="2026-10-25T12:00:00Z"
SCOPE=digest("synthetic-operating-scope")


class OperatingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.store=EventStore(self.root/"events.db"); self.registry=load_registry()
        self.doc=make_doc("INTerpath-001 met its primary endpoint.")
        self.events,self.decisions,_=analyze(self.doc,self.registry,POLICIES,now=NOW)
        self.event=self.events[0]
        self.store.commit_cycle(cycle_id="fixture",source_id=POLICY["policy_id"],observed_at=NOW,
            documents=[self.doc],events=self.events,decisions=self.decisions)
        record_review(self.store,self.event["event_id"],self.event["event_revision"],self.registry,POLICIES,
            now=NOW,reviewer="synthetic-reviewer",action="approve")
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def attest(self,purpose,subject,bindings,*,now=NOW,evidence_kind="synthetic",**extra):
        return put_receipt(self.store,"operator_attestation",{"schema":ATTESTATION,"purpose":purpose,"subject_hash":digest(subject),
            "decision":"approved","reviewer":"synthetic-reviewer","reviewed_at":now,"evidence_kind":evidence_kind,
            "external_reference":{"reference":"synthetic fixture only","sha256":digest(subject)},**bindings,**extra},observed_at=now)
    def grant(self,field,purpose="review_display",**extra):
        body={"contract":RIGHTS,"source_ref":reference(self.doc),"field":field,"purpose":purpose,"audience":"local_operator",
            "rightsholder":"synthetic fixture owner","attribution":"exact synthetic source URL","retention_rule":"disposable fixture only",
            "policy_hash":digest(POLICIES),"implementation_hash":implementation_hash(),"decision":"approved",
            "transformation":("derived_summary" if field.startswith("claim.") else "sanitized_history" if field in {"coverage","correction.history","review.decision"} else "canonical_metadata"),"evidence_kind":"synthetic","effective_at":NOW,"expires_at":EXPIRY,**extra}
        if field=="source.text": body.update(max_characters=20,transformation="exact_excerpt")
        att=self.attest("field_rights",body,{"policy_hash":digest(POLICIES),"implementation_hash":implementation_hash(),"source_policy_id":POLICY["policy_id"]})
        return record_rights(self.store,{**body,"attestation_ref":att["receipt_id"]},POLICIES,now=NOW,allow_synthetic=True)
    def brief(self,*,purpose="review_display",persist=True):
        return preview_brief(self.store,self.registry,POLICIES,now=NOW,scope_hash=SCOPE,purpose=purpose,allow_synthetic=True,persist=persist)
    def approved_brief(self,purpose="review_display"):
        for field in REQUIRED: self.grant(field,purpose)
        return self.brief(purpose=purpose)
    def observe(self,kind,identifier,*,now=AFTER,**values):
        if kind=="useful_brief": values.setdefault("assessed_at","2026-10-18T12:00:30Z")
        body={"contract":OPERATING,"observation_id":identifier,"kind":kind,"evidence_kind":"synthetic",
            "scope_hash":SCOPE,"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES),
            "window_start":NOW,"window_end":END,"observed_at":now,"source_refs":[],
            "external_reference":{"reference":"synthetic row "+identifier,"sha256":digest(identifier)},**values}
        att=self.attest("operating_observation",body,{"scope_hash":SCOPE,"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES)},now=now)
        return record_observation(self.store,{**body,"attestation_ref":att["receipt_id"]},now=now,allow_synthetic=True,registry=self.registry,policies=POLICIES)
    def complete_costs(self):
        rows=[]
        for i,category in enumerate(sorted(CATEGORIES)):
            rows.append(self.observe("cost","cost-"+category,category=category,amount=str(i),currency="USD",allocation_basis="single fixture window"))
        self.observe("cost_completeness","cost-completeness",covered_categories=sorted(CATEGORIES),cost_refs=[e["receipt_id"] for e in rows])
        return rows
    def report(self,**extra):
        return economics_report(self.store,scope_hash=SCOPE,window_start=NOW,window_end=END,policy_hash=digest(POLICIES),now=AFTER,
            allow_synthetic=True,registry=self.registry,policies=POLICIES,**extra)
    def test_missing_field_rights_withhold_card_body_and_reviewer_identity(self):
        result=self.brief(persist=False)
        self.assertEqual(result["cards"],[]); self.assertIn("field_rights_pending",result["withheld"][0]["reason_codes"])
        packet=safe_packet(self.store,self.event["event_id"],self.event["event_revision"],POLICIES,now=NOW,allow_synthetic=True)
        self.assertNotIn(self.doc["text"],json.dumps(packet)); self.assertNotIn('"url"',json.dumps(packet))
        self.assertEqual(search_history(self.store,POLICIES,query=self.event["event_id"],now=NOW,allow_synthetic=True)["items"],[])
    def test_local_synthetic_preview_has_attribution_pending_labels_and_no_review_identity_without_grant(self):
        brief=self.approved_brief()["payload"]
        self.assertEqual(len(brief["cards"]),1)
        self.assertEqual(brief["evidence_kind"],"synthetic")
        self.assertEqual(brief["cards"][0]["source_attribution"][0]["url"],self.doc["url"])
        self.assertNotIn("reviewer",brief["cards"][0]); self.assertNotIn("clinical_context",brief["cards"][0])
        self.assertEqual(brief["ai_domain_labels"],"pending_human_review")
        self.assertFalse(brief["signal_eligible"])
    def test_rights_expiry_revocation_scope_and_synthetic_boundary(self):
        grant=self.grant("source.url")
        self.assertFalse(field_decision(self.store,reference(self.doc),"source.url","review_display","local_operator",POLICIES,now=NOW)["allowed"])
        self.assertFalse(field_decision(self.store,reference(self.doc),"source.url","local_export","local_operator",POLICIES,now=NOW,allow_synthetic=True)["allowed"])
        self.assertFalse(field_decision(self.store,reference(self.doc),"source.url","review_display","other",POLICIES,now=NOW,allow_synthetic=True)["allowed"])
        self.assertFalse(field_decision(self.store,reference(self.doc),"source.url","review_display","local_operator",POLICIES,now=EXPIRY,allow_synthetic=True)["allowed"])
        revoke_rights(self.store,grant["receipt_id"],now=END,reason="synthetic revocation")
        self.assertFalse(field_decision(self.store,reference(self.doc),"source.url","review_display","local_operator",POLICIES,now=END,allow_synthetic=True)["allowed"])
    def test_export_requires_own_purpose_and_rechecks_current_rights(self):
        review=self.approved_brief()
        with self.assertRaisesRegex(ValueError,"purpose"): export_brief(self.store,review["receipt_id"],self.root/"wrong.json",self.registry,POLICIES,now=NOW,allow_synthetic=True)
        brief=self.approved_brief("local_export")
        for suffix in (".json",".md",".html"):
            result=export_brief(self.store,brief["receipt_id"],self.root/("brief"+suffix),self.registry,POLICIES,now=NOW,allow_synthetic=True)
            self.assertEqual(result["payload"]["external_distribution"],"not_authorized")
            self.assertTrue((self.root/("brief"+suffix)).is_file())
        revoke_rights(self.store,brief["payload"]["rights_refs"][0],now=END,reason="synthetic revoke")
        with self.assertRaises(ValueError): export_brief(self.store,brief["receipt_id"],self.root/"revoked.json",self.registry,POLICIES,now=END,allow_synthetic=True)
    def test_forged_brief_fields_and_missing_binding_cannot_validate(self):
        brief=self.approved_brief()
        for mutate in (lambda p:p["cards"][0]["claims"][0].update(value="invented"),lambda p:p.update(packet_bindings=[])):
            payload=deepcopy(brief["payload"]); mutate(payload)
            fake=put_receipt(self.store,"reviewed_brief",payload,observed_at=NOW,refs=payload["rights_refs"])
            with self.assertRaises(ValueError): validate_brief(self.store,fake["receipt_id"],self.registry,POLICIES,now=NOW,allow_synthetic=True)
    def test_correction_invalidates_export_and_history_is_searchable_only_with_rights(self):
        brief=self.approved_brief("local_export")
        for field in ("correction.history","review.decision","source.times"): self.grant(field)
        history=search_history(self.store,POLICIES,query=self.event["event_id"],now=NOW,allow_synthetic=True)
        self.assertEqual(len(history["items"]),1)
        changed=make_doc("INTerpath-001 did not meet its primary endpoint.",observed_at=END)
        events,decisions,_=analyze(changed,self.registry,POLICIES,now=END)
        self.store.commit_cycle(cycle_id="correction",source_id=POLICY["policy_id"],observed_at=END,documents=[changed],events=events,decisions=decisions)
        with self.assertRaisesRegex(ValueError,"state changed"):
            export_brief(self.store,brief["receipt_id"],self.root/"stale.json",self.registry,POLICIES,now=END,allow_synthetic=True)
    def test_empty_inputs_and_templates_create_no_actual_cost_useful_events_or_customers(self):
        self.assertIsNone(self.report()["direct_cost"]); self.assertIsNone(self.report()["useful_event_count"])
        template={"contract":OPERATING,"observation_id":"blank-template","kind":"cost","evidence_kind":"template",
            "scope_hash":SCOPE,"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES),
            "window_start":NOW,"window_end":END,"observed_at":AFTER,"amount":None,"source_refs":[]}
        record_observation(self.store,template,now=AFTER)
        report=self.report(); self.assertIsNone(report["direct_cost"]); self.assertEqual(report["feedback_records"],0)
        self.assertFalse(report["measured_actuals"])
    def test_complete_aligned_costs_and_useful_event_census_are_derived_without_pilot_credit(self):
        brief=self.approved_brief(); self.complete_costs()
        useful=self.observe("useful_brief","useful",brief_ref=brief["receipt_id"],event_ids=[self.event["event_id"]],useful=True)
        self.observe("useful_event_census","census",assessment_refs=[useful["receipt_id"]])
        report=self.report()
        self.assertEqual(report["direct_cost"],"10"); self.assertEqual(report["useful_event_count"],1)
        self.assertEqual(report["cost_per_useful_event"],"10"); self.assertEqual(report["pending_reasons"],[])
        self.assertFalse(report["measured_actuals"]); self.assertEqual(report["evidence_kind"],"synthetic")
    def test_zero_useful_events_preserves_negative_assessment_and_has_no_finite_unit_cost(self):
        brief=self.approved_brief(); self.complete_costs()
        negative=self.observe("useful_brief","negative",brief_ref=brief["receipt_id"],event_ids=[self.event["event_id"]],useful=False)
        self.observe("useful_event_census","census",assessment_refs=[negative["receipt_id"]])
        report=self.report()
        self.assertEqual(report["useful_event_count"],0); self.assertEqual(report["negative_event_count"],1)
        self.assertIsNone(report["cost_per_useful_event"])
        self.assertIn("zero_useful_events_no_finite_unit_cost",report["pending_reasons"])
    def test_duplicate_ids_external_cost_evidence_mixed_currency_and_missing_completeness_stay_pending(self):
        first=self.observe("cost","same",category="vendor",amount="1",currency="USD",allocation_basis="fixture")
        with self.assertRaisesRegex(ValueError,"reused"):
            self.observe("cost","same",category="vendor",amount="2",currency="USD",allocation_basis="fixture")
        self.observe("cost","copy",category="other",amount="1",currency="EUR",allocation_basis="fixture",
            external_reference=first["payload"]["external_reference"])
        report=self.report()
        self.assertIsNone(report["direct_cost"])
        self.assertIn("duplicate_underlying_cost_evidence",report["pending_reasons"])
        self.assertIn("cost_currency_missing_or_mixed",report["pending_reasons"])
    def test_review_effort_uses_clocks_not_caller_scalar_and_does_not_invent_labor_price(self):
        with self.assertRaisesRegex(ValueError,"disagrees"):
            self.observe("review_effort","bad-effort",started_at=NOW,ended_at=END,elapsed_seconds=1,role="reviewer",event_id=self.event["event_id"])
        self.observe("review_effort","effort",started_at=NOW,ended_at=END,elapsed_seconds=60.0,role="reviewer",event_id=self.event["event_id"])
        report=self.report(); self.assertEqual(report["review_effort_seconds"],60); self.assertIsNone(report["direct_cost"])
    def test_workbook_run_cost_formula_credits_and_blank_inputs(self):
        values={"run_id":"synthetic-allocation","currency":"USD","allocation_basis":"explicit fixture allocation",
            "incurred_at":NOW,"review_minutes":"60","review_hourly_rate":"60","operator_minutes":"30",
            "operator_hourly_rate":"40","vendor_amount":"1","compute_storage_amount":"2","other_amount":"3"}
        row=self.observe("run_cost","workbook-row",**values)
        self.observe("cost_completeness","run-census",covered_categories=sorted(CATEGORIES),cost_refs=[row["receipt_id"]])
        report=self.report()
        self.assertEqual(Decimal(report["direct_cost"]),Decimal("86"))
        self.assertEqual(Decimal(report["allocated_labor_hours"]),Decimal("1.5"))
        self.assertIsNone(report["review_effort_seconds"])
        self.assertFalse(report["measured_actuals"])
        zero=cost_components({"kind":"run_cost",**values,"vendor_amount":"0"})
        self.assertEqual(sum(zero.values()),Decimal("85"))
        credit=cost_components({"kind":"run_cost",**values,"vendor_amount":"-5"})
        self.assertEqual(sum(credit.values()),Decimal("80"))
        for key in ("review_minutes","review_hourly_rate","operator_minutes","operator_hourly_rate","vendor_amount","compute_storage_amount","other_amount"):
            with self.subTest(key=key),self.assertRaises(ValueError): cost_components({"kind":"run_cost",**values,key:None})
        with self.assertRaises(ValueError): cost_components({"kind":"run_cost",**values,"review_minutes":"-1"})
        self.observe("run_cost","duplicate-allocation",**values)
        self.assertIn("duplicate_run_allocation_id",self.report()["pending_reasons"])
    def test_date_only_cost_requires_full_explicit_calendar_day(self):
        values={"run_id":"dated","currency":"USD","allocation_basis":"UTC fixture day","incurred_date":"2026-10-18","cost_timezone":"UTC",
            "review_minutes":"0","review_hourly_rate":"0","operator_minutes":"0","operator_hourly_rate":"0",
            "vendor_amount":"0","compute_storage_amount":"0","other_amount":"0"}
        with self.assertRaisesRegex(ValueError,"entire calendar day"): self.observe("run_cost","partial-day",**values)
        row=self.observe("run_cost","full-day",now="2026-10-19T00:00:00Z",window_start="2026-10-18T00:00:00Z",window_end="2026-10-19T00:00:00Z",**values)
        self.assertEqual(row["payload"]["incurred_date"],"2026-10-18")
    def test_useful_assessment_window_and_brief_availability_are_bound(self):
        brief=self.approved_brief()
        with self.assertRaisesRegex(ValueError,"inside reporting window"):
            self.observe("useful_brief","outside",brief_ref=brief["receipt_id"],event_ids=[self.event["event_id"]],useful=True,
                assessed_at="2026-10-18T11:59:59Z")
        with self.assertRaisesRegex(ValueError,"did not exist"):
            self.observe("useful_brief","later-brief",window_start="2026-10-18T10:00:00Z",window_end="2026-10-18T11:00:00Z",
                assessed_at="2026-10-18T10:30:00Z",brief_ref=brief["receipt_id"],event_ids=[self.event["event_id"]],useful=True)
        accepted=self.observe("useful_brief","evidenced-use",brief_ref=brief["receipt_id"],event_ids=[self.event["event_id"]],useful=True)
        from omni.operating import validate_record
        later=validate_record(self.store,accepted["receipt_id"],now="2026-10-30T12:00:00Z",allow_synthetic=True,registry=self.registry,policies=POLICIES)
        self.assertEqual(later["receipt_id"],accepted["receipt_id"])
    def test_history_requires_decision_and_time_rights_as_well_as_correction_rights(self):
        self.grant("correction.history")
        self.assertEqual(search_history(self.store,POLICIES,query=self.event["event_id"],now=NOW,allow_synthetic=True)["items"],[])
        self.grant("review.decision"); time_grant=self.grant("source.times")
        self.assertEqual(len(search_history(self.store,POLICIES,query=self.event["event_id"],now=NOW,allow_synthetic=True)["items"]),1)
        revoke_rights(self.store,time_grant["receipt_id"],now=END,reason="fixture revocation")
        self.assertEqual(search_history(self.store,POLICIES,query=self.event["event_id"],now=END,allow_synthetic=True)["items"],[])
    def test_feedback_requires_prior_consent_and_keeps_negative_observation(self):
        brief=self.approved_brief(); subject={"participant_ref":"synthetic-person","task_version":"fixture-task-1",
            "brief_ref":brief["receipt_id"],"purpose":"structured_feedback_retention"}
        consent=self.attest("customer_feedback_consent",subject,{"scope_hash":SCOPE,"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES)})
        self.observe("customer_feedback","feedback",brief_ref=brief["receipt_id"],participant_ref="synthetic-person",task_version="fixture-task-1",
            consent_ref=consent["receipt_id"],result="not_useful",session_started_at="2026-10-18T12:00:10Z",session_ended_at="2026-10-18T12:00:30Z")
        report=self.report(); self.assertEqual(report["feedback_results"]["not_useful"],1); self.assertFalse(report["measured_actuals"])
        with self.assertRaises(ValueError): self.observe("customer_feedback","unsupported",brief_ref=brief["receipt_id"],participant_ref="synthetic-person",task_version="fixture-task-1",consent_ref=consent["receipt_id"],result="useful",session_started_at=NOW,session_ended_at=END,quotation="not authorized")
    def test_placeholders_and_synthetic_economics_cannot_mature_dependent_gates(self):
        fake={name:"approved" for name in ("stable_architecture_receipt","reviewed_user_value_receipt","vertical_source_policies",
            "expansion_report_receipt","reviewed_useful_brief_receipts","field_level_rights_receipt","counsel_review_receipt","customer_feedback_receipt","operating_cost_receipt")}
        for context in ({},{"store":self.store,"registry":self.registry,"policies":POLICIES,"now":NOW}):
            gates=dependent_gates({**fake,"scope_hash":SCOPE},["MRNA"],candidates=["MRNA","BEAM"],**context)
            self.assertTrue(all(gates[g]["status"]=="pending" for g in ("OMNI-10","OMNI-11","OMNI-12")))
            self.assertEqual(gates["wave_b_candidates"],["BEAM"]); self.assertFalse(gates["automatic_membership_change"])


if __name__=="__main__": unittest.main()
