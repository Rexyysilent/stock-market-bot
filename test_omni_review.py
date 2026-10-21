"""OMNI-07 manual gates, sixth candidate, corrections and off-switch equivalence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from omni.classifier import extract
from omni.identities import load_registry
from omni.review import build_brief, record_review
from omni.store import EventStore
from omni.validator import validate
from test_omni_events import POLICY, POLICIES, make_doc, NOW


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.temp.name)/"events.db"); self.registry=load_registry()
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def ingest(self,text,native_id="one",cycle="one",now=NOW):
        doc=make_doc(text,native_id=native_id,observed_at=now)
        events=extract(doc,self.registry,POLICY,known_by=NOW)["events"]
        self.store.commit_cycle(cycle_id=cycle,source_id="synthetic",observed_at=now,documents=[doc],events=events,
            decisions=[validate(e,[doc],self.registry,POLICIES,now=NOW) for e in events])
        return events[0]
    def approve(self,e):
        return record_review(self.store,e["event_id"],e["event_revision"],self.registry,POLICIES,now=NOW,reviewer="synthetic-reviewer",action="approve")
    def test_t04_t27_manual_review_and_signal_nonpromotion(self):
        e=self.ingest("INTerpath-001 met its primary endpoint.")
        before=build_brief(self.store,self.registry,POLICIES,now=NOW,mode="reviewed")
        self.assertEqual(before["cards"],[])
        self.approve(e)
        after=build_brief(self.store,self.registry,POLICIES,now=NOW,mode="reviewed")
        self.assertEqual(after["focus"]["kind"],"reviewed_event")
        self.assertFalse(after["cards"][0]["signal_eligible"])
        self.assertIsNone(after["cards"][0]["market_reaction"])
    def test_t31_disabled_and_shadow_preserve_headline_focus(self):
        e=self.ingest("INTerpath-001 met its primary endpoint."); self.approve(e)
        legacy={"status":"selected","ticker":"TSLA","selection_mode":"dynamic"}; original=deepcopy(legacy)
        for mode in ("off","shadow"):
            brief=build_brief(self.store,self.registry,POLICIES,now=NOW,mode=mode,legacy_focus=legacy)
            self.assertEqual(brief["cards"],[]); self.assertEqual(brief["focus"]["headline_focus"],original)
        self.assertEqual(legacy,original)
    def test_t38_t39_sixth_candidate_available_before_display_cap(self):
        for i in range(6): self.approve(self.ingest("INTerpath-001 met its primary endpoint.",native_id=str(i),cycle=str(i)))
        brief=build_brief(self.store,self.registry,POLICIES,now=NOW,mode="reviewed")
        self.assertEqual(brief["eligible_before_display_cap"],6); self.assertEqual(len(brief["cards"]),5)
        self.assertEqual(brief["budget_deferred"],1)
    def test_correction_requires_new_review_and_stale_approval_expires(self):
        e=self.ingest("INTerpath-001 met its primary endpoint."); self.approve(e)
        self.ingest("INTerpath-001 did not meet its primary endpoint.",cycle="correction")
        self.assertEqual(build_brief(self.store,self.registry,POLICIES,now=NOW,mode="reviewed")["cards"],[])
        e=self.ingest("KEYNOTE-942 met its primary endpoint.",native_id="two",cycle="two"); self.approve(e)
        self.assertEqual(build_brief(self.store,self.registry,POLICIES,now="2026-10-25T12:00:00Z",mode="reviewed")["cards"],[])
    def test_review_cannot_override_primary_source_gates(self):
        e=self.ingest("Unknown trial met its primary endpoint.")
        with self.assertRaisesRegex(ValueError,"mandatory gates"):
            self.approve(e)

    def test_reverted_correction_needs_fresh_review_and_reopens_queue(self):
        e=self.ingest("INTerpath-001 met its primary endpoint."); self.approve(e)
        self.assertEqual(self.store.pending(),[])
        self.ingest("INTerpath-001 did not meet its primary endpoint.",cycle="correction",now="2026-10-18T12:01:00Z")
        self.ingest("INTerpath-001 met its primary endpoint.",cycle="reversion",now="2026-10-18T12:02:00Z")
        brief=build_brief(self.store,self.registry,POLICIES,now="2026-10-18T12:03:00Z",mode="reviewed")
        self.assertEqual(brief["cards"],[])
        self.assertEqual(brief["withheld"][0]["revision_id"],e["event_revision"])
        self.assertTrue(any(r["revision_id"]==e["event_revision"] for r in self.store.pending()))

    def test_conflicting_current_sources_stay_contested_after_individual_reviews(self):
        self.approve(self.ingest("INTerpath-001 met its primary endpoint.",native_id="source-a",cycle="a"))
        with self.assertRaisesRegex(ValueError,"cross_source_outcome_conflict"):
            self.approve(self.ingest("INTerpath-001 did not meet its primary endpoint.",native_id="source-b",cycle="b"))
        brief=build_brief(self.store,self.registry,POLICIES,now=NOW,mode="reviewed")
        self.assertEqual(brief["cards"],[])
        self.assertTrue(all("cross_source_outcome_conflict" in r["reason_codes"] for r in brief["withheld"]))


if __name__=="__main__": unittest.main()
