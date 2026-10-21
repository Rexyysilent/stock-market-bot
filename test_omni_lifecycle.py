"""Executable B-O01..06 plus durable correction, coverage and migration controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from omni.attestations import VERSION as ATTESTATION
from omni.coverage import observation_from_rows, record_coverage, repair_plan
from omni.evidence import span
from omni.identities import digest, load_registry
from omni.index_snapshots import parse_snapshot, record_snapshot, compare_snapshots
from omni.lifecycle import implementation_hash, reference, record_correspondence, resolve_origin
from omni.migrations import backup, restore
from omni.receipts import put_receipt, import_receipt, list_receipts
from omni.review import build_brief, event_packet, record_review
from omni.runner import analyze
from omni.store import EventStore, SCHEMA
from test_omni_events import NOW, POLICY, POLICIES, make_doc
from test_omni_reconciliation import RAW

LATER = "2026-10-18T12:01:00Z"
AFTER = "2026-10-18T12:02:00Z"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"events.db"
        self.store=EventStore(self.path); self.registry=load_registry()
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def ingest(self, text="INTerpath-001 met its primary endpoint.", *, native="one", now=NOW, url=None, **extra):
        doc=make_doc(text,native_id=native,observed_at=now,url=url or "https://issuer.example/releases/"+native)
        events,decisions,leads=analyze(doc,self.registry,POLICIES,now=now)
        self.store.commit_cycle(cycle_id="cycle:"+digest([doc,extra]),source_id=POLICY["policy_id"],observed_at=now,
            documents=[doc],events=events,decisions=decisions,leads=leads,**extra)
        return doc,events[0] if events else None
    def approve(self,event,now=NOW):
        return record_review(self.store,event["event_id"],event["event_revision"],self.registry,POLICIES,
            now=now,reviewer="synthetic-reviewer",action="approve")
    def brief(self,now=NOW):
        return build_brief(self.store,self.registry,POLICIES,now=now,mode="reviewed")
    def link(self,left,right,relation="republishes",now=LATER):
        text=left["text"]; start=text.index("This ")
        body={"relation":relation,"from":reference(left),"to":reference(right),
            "proofs":[span(left,start,len(text))],"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES)}
        att=put_receipt(self.store,"operator_attestation",{"schema":ATTESTATION,"purpose":"origin_correspondence",
            "subject_hash":digest(body),"decision":"approved","reviewer":"synthetic-reviewer","evidence_kind":"synthetic",
            "reviewed_at":now,"implementation_hash":implementation_hash(),"policy_hash":digest(POLICIES),
            "external_reference":{"reference":"synthetic correspondence exercise","sha256":digest(body)}},observed_at=now)
        return record_correspondence(self.store,{**body,"attestation_ref":att["receipt_id"]},now=now,allow_synthetic=True)
    def test_b_o01_transitive_reviewed_copies_are_one_assertion(self):
        a,ea=self.ingest(native="issuer")
        b,eb=self.ingest("INTerpath-001 met its primary endpoint. This republishes the original release "+a["url"]+".",native="wire")
        c,ec=self.ingest("INTerpath-001 met its primary endpoint. This republishes the original release "+b["url"]+".",native="filing")
        self.link(b,a); self.link(c,b)
        for event in (ea,eb,ec): self.approve(event,LATER)
        origins=[resolve_origin(self.store,reference(d),at=LATER,allow_synthetic=True)["origin_id"] for d in (a,b,c)]
        self.assertEqual(len(set(origins)),1)
        brief=self.brief(LATER)
        self.assertEqual(len(brief["cards"]),1)
        self.assertEqual(len(brief["cards"][0]["same_assertion_event_ids"]),3)
        self.assertFalse(brief["cards"][0]["independence_assessed"])
    def test_b_o02_similar_titles_and_caller_origin_strings_prove_nothing(self):
        a,_=self.ingest(native="a"); b,_=self.ingest(native="b")
        self.assertNotEqual(resolve_origin(self.store,reference(a))["origin_id"],resolve_origin(self.store,reference(b))["origin_id"])
        self.assertNotEqual(make_doc(a["text"],native_id="a",origin_reference="same")["origin_cluster_id"],
                            make_doc(a["text"],native_id="b",origin_reference="same")["origin_cluster_id"])
    def test_b_o03_independent_report_preserves_reporting_roles_without_copy_union(self):
        a,_=self.ingest("Moderna reports FDA approved the application.",native="regulator")
        b,_=self.ingest("Moderna reports FDA approved the application. This independently reports the action at "+a["url"]+".",native="issuer")
        self.link(b,a,"independently_reports")
        self.assertNotEqual(resolve_origin(self.store,reference(a),allow_synthetic=True)["origin_id"],
                            resolve_origin(self.store,reference(b),allow_synthetic=True)["origin_id"])
        self.assertEqual(a["reporting_party"],"synthetic-company")
        self.assertFalse(resolve_origin(self.store,reference(b))["independence_assessed"])
    def test_b_o04_same_url_changed_number_requires_exact_current_review_and_retains_history(self):
        a,event=self.ingest("INTerpath-001 met its primary endpoint. Numerical appendix: 5.")
        old=self.approve(event); self.assertEqual(len(self.brief()["cards"]),1)
        b,event2=self.ingest("INTerpath-001 met its primary endpoint. Numerical appendix: 6.",now=LATER)
        self.assertNotEqual(a["revision_id"],b["revision_id"])
        self.assertEqual(self.brief(LATER)["cards"],[])
        self.store.close(); self.store=EventStore(self.path)
        self.assertEqual(self.brief(LATER)["cards"],[])
        new=self.approve(event2,AFTER)
        self.assertNotEqual(old["review"]["current_state_hash"],new["review"]["current_state_hash"])
        self.assertEqual(len(self.brief(AFTER)["cards"]),1)
        self.assertEqual(self.store.inventory()["evidence"],2)
        self.assertEqual(list_receipts(self.store,kind="source_change")[0]["payload"]["kind"],"substantive_correction")
        self.assertEqual(resolve_origin(self.store,reference(a))["origin_id"],resolve_origin(self.store,reference(b))["origin_id"])
    def test_b_o05_absent_full_index_is_discrepancy_never_retraction(self):
        first=record_snapshot(self.store,parse_snapshot(RAW,route_id="sec-full-index",observed_at=NOW),
            source_id="sec-edgar",policy_hash=digest({}),now=NOW)
        empty=b"CIK|Company Name|Form Type|Date Filed|Filename\n--------------------\n"
        second=record_snapshot(self.store,parse_snapshot(empty,route_id="sec-full-index",observed_at=LATER),
            source_id="sec-edgar",policy_hash=digest({}),now=LATER)
        before=self.store.inventory()
        with self.assertRaisesRegex(RuntimeError,"crash"):
            compare_snapshots(self.store,first["receipt_id"],second["receipt_id"],now=AFTER,fail_at="after_changes")
        self.assertEqual(self.store.inventory(),before)
        compared=compare_snapshots(self.store,first["receipt_id"],second["receipt_id"],now=AFTER)
        before_replay=self.store.inventory()
        replay=compare_snapshots(self.store,first["receipt_id"],second["receipt_id"],now="2026-10-18T12:03:00Z")
        self.assertEqual(replay["receipt_id"],compared["receipt_id"])
        self.assertEqual(self.store.inventory(),before_replay)
        self.assertEqual(len(compared["payload"]["missing_native_ids"]),1)
        self.assertEqual(compared["payload"]["gaps"],["empty_index_requires_review"])
        self.assertTrue(all(r["payload"]["kind"]=="discrepancy" for r in list_receipts(self.store,kind="source_change")))
        self.assertIsNone(self.store.cursor("sec-edgar")["watermark"])
    def test_b_o06_cached_parsed_jobs_and_metadata_status_cannot_clear_quarantine(self):
        candidate={"native_id":"one","title":"original","url":"https://issuer.example/releases/one","hydration_status":"parsed"}
        a,event=self.ingest(candidates=[candidate]); self.approve(event)
        changed={**candidate,"title":"changed","hydration_status":"parsed"}
        self.store.commit_cycle(cycle_id="metadata-change",source_id=POLICY["policy_id"],observed_at=LATER,
            documents=[],events=[],decisions=[],candidates=[changed])
        self.assertEqual(self.brief(LATER)["cards"],[])
        with self.assertRaisesRegex(ValueError,"source_metadata_reverification_required"):
            self.approve(event,LATER)
        self.store.commit_cycle(cycle_id="cached-hydration",source_id=POLICY["policy_id"],observed_at=AFTER,
            documents=[a],events=[],decisions=[],candidates=[changed])
        self.assertEqual(self.brief(AFTER)["cards"],[])
        self.assertTrue(self.store.pending())
    def test_metadata_enrichment_does_not_fabricate_retraction_or_invalidate_approval(self):
        row={"native_id":"one","url":"https://issuer.example/releases/one"}
        _,event=self.ingest(candidates=[row]); self.approve(event)
        self.store.commit_cycle(cycle_id="enrich",source_id=POLICY["policy_id"],observed_at=LATER,documents=[],events=[],decisions=[],
            candidates=[{**row,"filed_date":"2026-10-18"}])
        changes=list_receipts(self.store,kind="source_change")
        self.assertEqual(changes[-1]["payload"]["kind"],"metadata_enrichment")
        self.assertFalse(changes[-1]["payload"]["invalidates_review"])
        # Metadata alters the review state fingerprint conservatively, but is
        # never a withdrawal; a current explicit review can restore display.
        self.approve(event,LATER)
        self.assertEqual(len(self.brief(LATER)["cards"]),1)
    def test_correction_cycle_crash_rolls_back_all_changes_and_replay_is_idempotent(self):
        _,event=self.ingest(); self.approve(event); inventory=self.store.inventory()
        with self.assertRaises(RuntimeError):
            self.ingest("INTerpath-001 did not meet its primary endpoint.",now=LATER,fail_at="after_events")
        self.assertEqual(self.store.inventory(),inventory); self.assertEqual(len(self.brief(LATER)["cards"]),1)
        self.ingest("INTerpath-001 did not meet its primary endpoint.",now=LATER)
        inventory=self.store.inventory()
        self.ingest("INTerpath-001 did not meet its primary endpoint.",now=LATER)
        self.assertEqual(self.store.inventory(),inventory)
    def test_own_identifier_without_target_relationship_is_not_correspondence_proof(self):
        a,_=self.ingest(native="a")
        b,_=self.ingest("INTerpath-001 met its primary endpoint. This republishes https://issuer.example/releases/b.",native="b")
        with self.assertRaisesRegex(ValueError,"exact native correspondence"): self.link(b,a)
    def test_correspondence_expiry_and_revocation_stop_reuse(self):
        a,_=self.ingest(native="a")
        b,_=self.ingest("INTerpath-001 met its primary endpoint. This republishes "+a["url"]+".",native="b")
        link=self.link(b,a)
        put_receipt(self.store,"attestation_revocation",{"attestation_ref":link["payload"]["attestation_ref"],"reason":"synthetic revoke"},
            observed_at=AFTER,refs=[link["payload"]["attestation_ref"]])
        self.assertNotEqual(resolve_origin(self.store,reference(a),at=AFTER,allow_synthetic=True)["origin_id"],
                            resolve_origin(self.store,reference(b),at=AFTER,allow_synthetic=True)["origin_id"])
    def test_inventory_must_link_to_cycle_and_does_not_prove_continuous_coverage(self):
        rows=[{"native_id":"one"}]
        observation=observation_from_rows(rows,source_id="synthetic",route_id="rss",family="issuer",acquired_at=NOW,
            window_start=NOW,window_end=NOW,policy_hash=digest({}),raw_response_hash=hashlib.sha256(b"fixture").hexdigest(),
            query={},end_of_results=True,evidence_kind="synthetic")
        self.store.commit_cycle(cycle_id="source-cycle",source_id="synthetic",observed_at=NOW,documents=[],events=[],decisions=[],
            coverage_observations=[observation],complete=True)
        ref=list_receipts(self.store,kind="native_inventory_observation")[0]["receipt_id"]
        body={"kind":"acquired_index_set","source_id":"synthetic","route_id":"rss","family":"issuer",
            "policy_hash":digest({}),"implementation_hash":implementation_hash(),"window_start":NOW,"window_end":NOW,
            "cycle_ids":["source-cycle"],"observation_refs":[ref]}
        result=record_coverage(self.store,body,now=NOW)["payload"]
        self.assertEqual(result["status"],"complete"); self.assertFalse(result["continuous_source"])
        self.store.commit_cycle(cycle_id="unrelated",source_id="synthetic",observed_at=NOW,documents=[],events=[],decisions=[])
        result=record_coverage(self.store,{**body,"cycle_ids":["unrelated"]},now=NOW)["payload"]
        self.assertIn("observation_not_bound_to_source_cycle",result["gaps"])
    def test_truthy_complete_cannot_advance_cursor_and_repair_is_bounded(self):
        self.store.commit_cycle(cycle_id="unsupported",source_id="synthetic",observed_at=NOW,documents=[],events=[],decisions=[],
            watermark="2026-10-18T10:00:00Z",complete=True)
        self.assertIsNone(self.store.cursor("synthetic")["watermark"])
        plan=repair_plan(start_day="2020-01-01",end_day="2026-10-20")
        self.assertEqual(len(plan["batches"]),52); self.assertTrue(plan["continuation_day"])
        self.assertFalse(plan["historical_availability_reconstructed"])
    def test_extra_envelope_fields_cannot_leave_partial_import_and_receipts_are_immutable(self):
        record=put_receipt(self.store,"fixture",{"value":1},observed_at=NOW)
        bad={**record,"receipt_id":"bad","extra":True}
        bad["record_hash"]=digest({k:v for k,v in bad.items() if k!="record_hash"})
        before=self.store.inventory()
        with self.assertRaises(ValueError): import_receipt(self.store,bad)
        self.assertEqual(self.store.inventory(),before)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute("DELETE FROM omni_receipts WHERE receipt_id=?",(record["receipt_id"],))
        self.store.conn.rollback()
    def test_existing_six_design_fixture_ids_are_all_exercised(self):
        fixture=json.loads((Path(__file__).parent/"fixtures/omni/coverage_rules.json").read_text())
        self.assertEqual({r["fixture_id"] for r in fixture["origin_proposal_cases"]},{f"B-O{i:02}" for i in range(1,7)})


class MigrationTests(unittest.TestCase):
    def test_legacy_upgrade_is_idempotent_and_backup_restore_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); original=root/"legacy.db"
            conn=sqlite3.connect(original); conn.executescript(SCHEMA)
            conn.execute("INSERT INTO metadata VALUES('contract','omni-store-1')")
            conn.execute("INSERT INTO reviews VALUES('legacy-review','legacy-event','legacy-revision','{}')"); conn.commit()
            class Holder: pass
            holder=Holder(); holder.conn=conn
            saved=backup(holder,root/"legacy-backup.db"); conn.close()
            before=hashlib.sha256((root/"legacy-backup.db").read_bytes()).hexdigest()
            store=EventStore(original)
            self.assertEqual(store.migration["status"],"upgraded")
            self.assertEqual(store.conn.execute("SELECT payload FROM reviews").fetchone()[0],"{}")
            new=backup(store,root/"new-backup.db"); store.close()
            store=EventStore(original); self.assertEqual(store.migration["status"],"already_current"); store.close()
            restore(saved["path"],root/"legacy-restored.db",expected_sha256=saved["sha256"])
            restored=sqlite3.connect(root/"legacy-restored.db")
            self.assertIsNone(restored.execute("SELECT value FROM metadata WHERE key='extensions_contract'").fetchone()); restored.close()
            restore(new["path"],root/"new-restored.db",expected_sha256=new["sha256"])
            store=EventStore(root/"new-restored.db"); self.assertEqual(store.migration["status"],"already_current"); store.close()
            self.assertEqual(before,hashlib.sha256((root/"legacy-backup.db").read_bytes()).hexdigest())
            with self.assertRaises(ValueError): restore(saved["path"],original,expected_sha256=saved["sha256"])
            with self.assertRaises(ValueError): restore(saved["path"],root/"bad.db",expected_sha256="bad")


if __name__=="__main__": unittest.main()
