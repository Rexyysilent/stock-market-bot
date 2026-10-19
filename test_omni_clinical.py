"""OMNI-06 schedule/posted changes/results, query seeds and pagination."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from omni.clinical import normalize_study, apply_studies, collect_studies, watch_studies
from omni.store import EventStore
from omni.regulatory import parse_notifications,watch_notifications
from test_omni_events import NOW


STUDY={"protocolSection":{"identificationModule":{"nctId":"NCT00000001","briefTitle":"Synthetic trial"},
      "statusModule":{"overallStatus":"RECRUITING","primaryCompletionDateStruct":{"date":"2026-12"},
                      "lastUpdatePostDateStruct":{"date":"2026-10-17"}},"designModule":{"phases":["PHASE1","PHASE2"]}},"hasResults":False}


class ClinicalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.temp.name)/"omni.db")
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def test_t16_t26_first_import_and_query_change_are_seeds(self):
        s=normalize_study(STUDY)
        self.assertEqual(s["dates"]["primaryCompletionDateStruct"]["precision"],"month")
        for query in ("one", "two"):
            r=apply_studies(self.store,[STUDY],query=query,observed_at=NOW)
            self.assertEqual(r["seeded"],1); self.assertEqual(r["changes"],[])
        r=apply_studies(self.store,[],query="one",observed_at=NOW)
        self.assertEqual(r["changes"],[])
        self.assertEqual(self.store.inventory()["trial_snapshots"],2)
    def test_results_posting_is_not_approval_or_primary_outcome(self):
        apply_studies(self.store,[STUDY],query="one",observed_at=NOW)
        changed=deepcopy(STUDY); changed["hasResults"]=True; changed["resultsSection"]={"synthetic":1}
        changed["protocolSection"]["statusModule"]["resultsFirstPostDateStruct"]={"date":"2026-10-18"}
        result=apply_studies(self.store,[changed],query="one",observed_at=NOW)
        results=next(c for c in result["changes"] if c["kind"]=="results_change")
        self.assertEqual(results["source_posted_time"]["start"],"2026-10-18")
        self.assertEqual(results["clinical_outcome"],"not_established")
        self.assertFalse(results["signal_eligible"])
        self.assertEqual(apply_studies(self.store,[changed],query="one",observed_at=NOW)["changes"],[])
    def test_t23_pagination_budget_and_version_skip(self):
        client=Mock(); client.get.side_effect=[json.dumps({"dataTimestamp":"v1"}).encode(),json.dumps({"studies":[STUDY],"nextPageToken":"next"}).encode(),json.dumps({"dataTimestamp":"v1"}).encode()]
        r=collect_studies(client,query="synthetic",max_pages=1)
        self.assertFalse(r["complete"]); self.assertEqual(r["continuation"],"next")
        client=Mock(); client.get.return_value=json.dumps({"dataTimestamp":"v1"}).encode()
        self.assertTrue(collect_studies(client,query="synthetic",previous_data_timestamp="v1")["unchanged_source"])
        self.assertEqual(client.get.call_count,1)

    def test_registry_refresh_discards_mixed_pages(self):
        client=Mock(); client.get.side_effect=[json.dumps(r).encode() for r in
            ({'dataTimestamp':'v1'},{'studies':[STUDY]},{'dataTimestamp':'v2'})]
        page=collect_studies(client,query='one')
        self.assertEqual(page['studies'],[]); self.assertFalse(page['complete'])
        self.assertIn('registry_changed_during_scan',page['gaps'])

    def test_cursor_token_does_not_cross_dataset_versions(self):
        client=Mock(); client.get.side_effect=[json.dumps(r).encode() for r in
            ({'dataTimestamp':'v2'},{'studies':[STUDY]},{'dataTimestamp':'v2'})]
        page=collect_studies(client,query='one',continuation='old',continuation_version='v1')
        self.assertNotIn('pageToken',client.get.call_args_list[1].args[0])
        self.assertIn('registry_snapshot_changed_restart',page['notices'])
        self.assertTrue(page['complete'])

    def test_trial_cursor_and_version_commit_or_rollback_together(self):
        def client():
            c=Mock(); c.counts={'attempt':3,'metadata':3,'document':0}
            c.get.side_effect=[json.dumps(r).encode() for r in
                ({'dataTimestamp':'v1'},{'studies':[STUDY]},{'dataTimestamp':'v1'})]
            return c
        with self.assertRaisesRegex(RuntimeError,'injected crash'):
            watch_studies(self.store,client(),query='one',now=NOW,fail_at='after_trials')
        self.assertEqual(self.store.inventory()['trial_snapshots'],0)
        self.assertEqual(self.store.inventory()['source_cycles'],0)
        self.assertEqual(watch_studies(self.store,client(),query='one',now=NOW)['seeded'],1)
        self.assertEqual(self.store.inventory()['source_cycles'],1)
    def test_future_posted_date_remains_review(self):
        apply_studies(self.store,[STUDY],query="one",observed_at=NOW)
        changed=deepcopy(STUDY); changed["protocolSection"]["statusModule"].update(overallStatus="COMPLETED",lastUpdatePostDateStruct={"date":"2026-12-01"})
        r=apply_studies(self.store,[changed],query="one",observed_at=NOW)
        self.assertIn("future_posting_time",r["changes"][0]["reason_codes"])

    def test_regulatory_index_retains_date_precision_without_asserting_approval(self):
        policy={'policy_id':'fda-oncology','version':'test','evidence':['synthetic'],
                'routes':[{'host':'www.fda.gov','path_prefix':'/drugs/'}],'uses':{'acquire':True}}
        raw=b'<table><tr><th>Webpage</th><th>Description</th><th>Date</th></tr><tr><td><a href="/drugs/synthetic">Synthetic approval notice</a></td><td>Fixture only</td><td>10/16/2026</td></tr></table>'
        parsed=parse_notifications(raw,policy=policy)
        row=parsed['rows'][0]
        self.assertEqual(row['publication_date']['precision'],'date'); self.assertIsNone(row['published'])
        self.assertEqual(row['regulatory_claim_status'],'body_not_validated')
        client=Mock(); client.policy=policy; client.counts={'metadata':1,'attempt':1,'document':0}; client.get.return_value=raw
        result=watch_notifications(self.store,client,now=NOW)
        self.assertEqual(result['claims_validated'],0); self.assertTrue(result['initial_seed'])
        self.assertEqual(self.store.inventory()['events'],0)

    def test_regulatory_missing_table_unknown_date_and_cap_stay_visible(self):
        policy={'policy_id':'fda-oncology','version':'test','evidence':['synthetic'],
                'routes':[{'host':'www.fda.gov','path_prefix':'/drugs/'}],'uses':{'acquire':True}}
        with self.assertRaisesRegex(ValueError,'structure_missing'): parse_notifications(b'<html>empty shell</html>',policy=policy)
        row='<tr><td><a href="/drugs/synthetic">Synthetic</a></td><td>Fixture</td><td>Unknown</td></tr>'
        raw=('<table><tr><th>Webpage</th><th>Description</th><th>Date</th></tr>'+row+row+'</table>').encode()
        result=parse_notifications(raw,policy=policy,cap=1)
        self.assertIn('regulatory_index_cap',result['gaps']); self.assertIn('regulatory_source_date_unknown',result['gaps'])


if __name__ == "__main__": unittest.main()
