"""Daily index backfill preserves date precision, gaps and existing feed clocks."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock,patch
from omni.acquisition import Deferred
from omni.reconciliation import parse_daily_index,reconcile_days,daily_index_url,merge_index_candidate
from omni.runner import watch_edgar
from omni.identities import load_registry
from omni.store import EventStore
from test_omni_events import NOW

RAW=b'Description: Synthetic daily index\nCIK|Company Name|Form Type|Date Filed|Filename\n--------------------\n123|Synthetic Issuer|8-K|2026-10-16|edgar/data/123/0000000123-26-000001.txt\n'


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.temp.name)/'events.db')
        self.client=Mock(); self.client.policy={'policy_id':'sec-edgar'}
        self.client.counts={'metadata':1,'attempt':1,'document':0}; self.client.get.return_value=RAW
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def test_parse_preserves_date_not_midnight_and_rejects_partial_or_unsafe_index(self):
        r=parse_daily_index(RAW,day='2026-10-16'); row=r['rows'][0]
        self.assertIsNone(row['published']); self.assertEqual(row['filed_date'],'2026-10-16')
        self.assertTrue(row['url'].endswith('0000000123-26-000001-index.html'))
        variant=RAW.rstrip().replace(b'|Filename',b'|File Name').replace(b'|2026-10-16|',b'|20261016|')
        self.assertEqual(parse_daily_index(variant,day='2026-10-16')['rows'][0]['filed_date'],'2026-10-16')
        for raw in (RAW[:-5],RAW.replace(b'edgar/data/123/',b'edgar/data/../'),RAW.replace(b'|2026-10-16|',b'|2026-10-19|')):
            with self.assertRaises(ValueError): parse_daily_index(raw,day='2026-10-16')
    def test_index_receipt_and_candidates_persist_without_corrupting_feed_progress(self):
        self.store.commit_cycle(cycle_id='seed',source_id='sec-edgar',observed_at=NOW,documents=[],events=[],decisions=[],
            watermark='2026-10-15T10:00:00Z',continuation='300',gaps=['result_cap_before_watermark'])
        old=self.store.cursor('sec-edgar')
        r=reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-18',now='2026-10-19T12:00:00Z')
        self.assertEqual(r['status'],'index_range_acquired'); self.assertEqual(r['persisted_candidates'],1)
        self.assertEqual(self.client.get.call_count,1)
        self.assertEqual(self.store.cursor('sec-edgar'),old)
        self.assertEqual(self.store.inventory()['source_receipts'],1)
        self.assertEqual(self.store.inventory()['acquisition_queue'],1)
        reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-18',now='2026-10-19T12:01:00Z')
        self.assertEqual(self.store.inventory()['acquisition_queue'],1)
    def test_missing_day_is_a_gap_and_open_day_makes_no_request(self):
        self.client.get.side_effect=Deferred('http_404')
        r=reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-16',now=NOW)
        self.assertEqual(r['status'],'partial'); self.assertEqual(r['gaps'][0]['reason'],'http_404')
        self.client.get.reset_mock()
        r=reconcile_days(self.store,self.client,start_day='2026-10-18',end_day='2026-10-18',now=NOW)
        self.assertEqual(r['gaps'][0]['reason'],'index_day_not_closed'); self.client.get.assert_not_called()
    def test_observed_timestamp_and_hydration_survive_index_merge(self):
        row=parse_daily_index(RAW,day='2026-10-16')['rows'][0]
        row.update(published='2026-10-16T16:00:00Z',hydration_status='parsed')
        self.store.commit_cycle(cycle_id='feed',source_id='sec-edgar',observed_at=NOW,documents=[],events=[],decisions=[],candidates=[row])
        reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-16',now=NOW)
        self.assertEqual(self.store.candidate('sec-edgar',row['native_id'])['published'],row['published'])
        self.assertEqual(self.store.backlog('sec-edgar'),[])

    def test_shared_accession_preserves_multiple_registrants_without_multiple_origins(self):
        raw=RAW+RAW.splitlines()[-1].replace(b'123|Synthetic Issuer',b'456|Synthetic Co-registrant').replace(b'edgar/data/123/',b'edgar/data/456/')+b'\n'
        result=parse_daily_index(raw,day='2026-10-16')
        self.assertEqual(result['unique_accessions'],1)
        self.assertEqual(len(result['rows'][0]['registrants']),2)

    def test_corrected_registrants_survive_with_receipted_history_and_cached_work(self):
        reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-16',now=NOW)
        row=parse_daily_index(RAW,day='2026-10-16')['rows'][0]
        candidate=self.store.candidate('sec-edgar',row['native_id'])
        candidate.update(published='2026-10-16T16:00:00Z',hydration_status='parsed',
                         document_listing={'documents':[{'url':row['url']+'/synthetic.htm'}],'gaps':[]})
        job={'native_id':row['native_id'],'url':row['url']+'/synthetic.htm','state':'parsed','attempts':1}
        self.store.commit_cycle(cycle_id='feed',source_id='sec-edgar',observed_at=NOW,documents=[],events=[],decisions=[],
            candidates=[candidate],document_jobs=[job],watermark='2026-10-16T16:00:00Z',complete=True)
        cursor=self.store.cursor('sec-edgar')
        self.client.get.return_value=RAW+RAW.splitlines()[-1].replace(b'123|Synthetic Issuer',b'456|Synthetic Co-registrant').replace(b'edgar/data/123/',b'edgar/data/456/')+b'\n'
        result=reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-16',now='2026-10-19T12:01:00Z')
        saved=self.store.candidate('sec-edgar',row['native_id'])
        self.assertEqual([r['cik'] for r in saved['registrants']],['123','456'])
        self.assertEqual(saved['published'],candidate['published']); self.assertEqual(saved['time_precision'],'timestamp')
        self.assertEqual(saved['hydration_status'],'parsed'); self.assertEqual(saved['document_listing'],candidate['document_listing'])
        self.assertEqual(self.store.jobs('sec-edgar',row['native_id']),{job['url']:job})
        self.assertEqual(self.store.cursor('sec-edgar'),cursor)
        change=result['days'][0]['metadata_changes'][0]
        self.assertEqual(change['changed_fields'],['registrants'])
        self.assertNotEqual(change['previous_metadata_hash'],change['current_metadata_hash'])
        self.assertEqual(len(change['previous_metadata']['registrants']),1)
        self.assertEqual(len(change['current_metadata']['registrants']),2)
        self.assertEqual(change['human_review_status'],'pending'); self.assertFalse(change['claim_revocation_performed'])
        receipts=[json.loads(r[0]) for r in self.store.conn.execute('SELECT payload FROM source_receipts ORDER BY rowid')]
        self.assertEqual(receipts[0]['days'][0]['index_rows'],1)
        self.assertEqual(receipts[1]['days'][0]['metadata_changes'][0],change)
        repeated=reconcile_days(self.store,self.client,start_day='2026-10-16',end_day='2026-10-16',now='2026-10-19T12:02:00Z')
        self.assertEqual(repeated['days'][0]['metadata_changes'],[])
        # A subsequent narrow feed entry must not erase index-only identity facts.
        acquisition={'rows':[{k:saved[k] for k in ('native_id','title','url','published')}],
                     'watermark':saved['published'],'complete':True,'initial_seed':False,'continuation':None,'gaps':[]}
        with patch('omni.runner.collect_edgar',return_value=acquisition):
            watch_edgar(self.store,self.client,load_registry(),now='2026-10-19T12:03:00Z')
        self.assertEqual(self.store.candidate('sec-edgar',row['native_id'])['registrants'],saved['registrants'])

    def test_changed_form_does_not_reuse_inapplicable_listing_or_hydration_status(self):
        prior=parse_daily_index(RAW,day='2026-10-16')['rows'][0]
        prior.update(hydration_status='parsed',document_listing={'documents':[{'url':'https://www.sec.gov/old.htm'}]})
        current=parse_daily_index(RAW.replace(b'|8-K|',b'|10-K|'),day='2026-10-16')['rows'][0]
        merged,change=merge_index_candidate(current,prior,observed_at=NOW,raw_sha256='synthetic-hash')
        self.assertEqual(merged['form'],'10-K'); self.assertEqual(merged['hydration_status'],'not_applicable')
        self.assertNotIn('document_listing',merged); self.assertIn('form',change['changed_fields'])


if __name__=='__main__': unittest.main()
