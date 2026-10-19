"""Pilot input receipts cannot turn synthetic, repeated or unfrozen work into proof."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from omni.identities import load_registry,digest
from omni.store import EventStore
from omni.runner import analyze
from omni.review import event_packet
from omni.operations import discovery_queue,operations_report,event_history
from omni.pilot import pilot_plan,freeze_pilot,record_case,record_receipt,pilot_report
from test_omni_events import make_doc,POLICIES,NOW

FREEZE='2026-10-18T09:00:00Z'
CORPUS=[{'event_id':'gold-1','source_url':'https://issuer.example/releases/gold',
 'reviewed_by':'fixture-reviewer','reviewed_at':FREEZE,'adjudication_receipt':'synthetic-test-receipt',
 'evidence_kind':'historical_replay','prespecified':True,'in_source_scope':True,'unknown_issuer':True}]


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.temp.name)/'events.db'); self.registry=load_registry()
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def freeze(self): return freeze_pilot(self.store,self.registry,POLICIES,[],CORPUS,now=FREEZE)['pilot_id']
    def ingest(self,kind='synthetic',text='Moderna reports INTerpath-001 met its primary endpoint.'):
        doc=make_doc(text,evidence_kind=kind)
        events,decisions,leads=analyze(doc,self.registry,POLICIES,now=NOW)
        self.store.commit_cycle(cycle_id='fixture',source_id='synthetic',observed_at=NOW,documents=[doc],events=events,decisions=decisions,leads=leads,complete=True)
        return events[0]
    def adjudication(self,event):
        p=event_packet(self.store,event['event_id'],event['event_revision'])
        return dict(event_id=event['event_id'],event_revision=event['event_revision'],reviewer='fixture-reviewer',
            reviewed_at=NOW,adjudication_receipt='synthetic-test-receipt',correct=True,critical_error=False,
            meaningfully_exercised=True,proposed_publication=True,input_hash=digest({'event':p['event'],'documents':p['documents']}))
    def test_empty_plan_is_not_a_frozen_pilot(self):
        self.assertIsNone(pilot_plan(self.registry,POLICIES,[])['frozen_at'])
        for corpus in ([],[dict(CORPUS[0],evidence_kind='synthetic')]):
            with self.assertRaises(ValueError): freeze_pilot(self.store,self.registry,POLICIES,[],corpus,now=FREEZE)
        self.assertEqual(self.store.inventory()['pilot_records'],0)
    def test_synthetic_cases_never_enter_prospective_denominator(self):
        pilot=self.freeze(); event=self.ingest()
        with self.assertRaisesRegex(ValueError,'synthetic or replay'):
            record_case(self.store,pilot,self.registry,POLICIES,self.adjudication(event),now=NOW)
        self.assertEqual(pilot_report(self.store,pilot,self.registry,POLICIES)['precision']['n'],0)
    def test_exact_adjudication_and_unique_event_case(self):
        pilot=self.freeze(); event=self.ingest('prospective'); a=self.adjudication(event)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            record_case(self.store,pilot,self.registry,POLICIES,dict(a,input_hash='wrong'),now=NOW)
        record_case(self.store,pilot,self.registry,POLICIES,a,now=NOW)
        with self.assertRaisesRegex(ValueError,'already adjudicated'):
            record_case(self.store,pilot,self.registry,POLICIES,a,now=NOW)
        r=pilot_report(self.store,pilot,self.registry,POLICIES)
        self.assertEqual(r['precision']['n'],1); self.assertEqual(r['manual_pilot']['status'],'pending')
    def test_changed_policy_preserves_report_but_prevents_more_cases(self):
        pilot=self.freeze(); event=self.ingest('prospective'); changed=deepcopy(POLICIES)
        next(iter(changed.values()))['version']='changed'
        with self.assertRaisesRegex(ValueError,'versions changed'):
            record_case(self.store,pilot,self.registry,changed,self.adjudication(event),now=NOW)
        r=pilot_report(self.store,pilot,self.registry,changed)
        self.assertFalse(r['current_versions_match']); self.assertEqual(r['manual_pilot']['status'],'pending')
    def test_manual_run_is_not_scheduled_and_gold_miss_stays_visible(self):
        pilot=self.freeze(); self.ingest('prospective')
        receipt=dict(reviewed_by='fixture-reviewer',evidence_receipt='synthetic-test-receipt',reviewed_at=NOW,
            source_cycle_ids=['fixture'],run_id='run-1',session='2026-10-18',started_at=FREEZE,completed_at=NOW,execution_mode='manual')
        record_receipt(self.store,pilot,self.registry,POLICIES,'run',receipt,now=NOW)
        with self.assertRaisesRegex(ValueError,'external scheduler receipt'):
            record_receipt(self.store,pilot,self.registry,POLICIES,'run',dict(receipt,run_id='run-2',execution_mode='scheduled'),now=NOW)
        record_receipt(self.store,pilot,self.registry,POLICIES,'gold',dict(reviewed_by='fixture-reviewer',evidence_receipt='synthetic-test-receipt',reviewed_at=NOW,event_id='gold-1',detected=False),now=NOW)
        r=pilot_report(self.store,pilot,self.registry,POLICIES)
        self.assertEqual(r['manual_pilot']['eligible_scheduled_sessions'],0)
        self.assertEqual(r['recall']['misses'],['gold-1'])
    def test_unknown_identity_is_durable_and_ops_does_not_invent_cost(self):
        event=self.ingest(text='Unknown trial met its primary endpoint.')
        queue=discovery_queue(self.store)
        self.assertEqual(queue['total'],1); self.assertEqual(queue['items'][0]['reason'],'entity_resolution_pending')
        self.assertNotIn('support',queue['items'][0])
        report=operations_report(self.store,now=NOW)
        self.assertIsNone(report['costs']['provider_currency_cost'])
        self.assertEqual(event_history(self.store,event['event_id'])['current_revision'],event['event_revision'])


if __name__=='__main__': unittest.main()
