"""Daily index backfill preserves date precision, gaps and existing feed clocks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from omni.acquisition import Deferred
from omni.reconciliation import parse_daily_index,reconcile_days,daily_index_url
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


if __name__=='__main__': unittest.main()
