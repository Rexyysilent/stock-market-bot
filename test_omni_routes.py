"""Disabled canonical routes, strict feed contracts and route approval receipts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from omni.feeds import parse_feed
from omni.identities import digest
from omni.index_snapshots import collect_snapshot, parse_snapshot
from omni.route_contracts import route_plan, check_route, policy_basis, VERSION
from omni.receipts import put_receipt
from omni.attestations import VERSION as ATTESTATION
from omni.store import EventStore
from test_omni_events import NOW
from test_omni_reconciliation import RAW


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.temp.name)/"store.db")
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def policy(self):
        return {"policy_id":"synthetic-sec","version":"1","evidence":["synthetic_fixture"],
            "uses":{"acquire":True},"routes":[{"host":"www.sec.gov","path_prefix":"/Archives/edgar/full-index/"}]}
    def grant(self,policy,**att_fields):
        plan=route_plan("sec-full-index")
        grant={"contract":VERSION,"route_id":"sec-full-index","route_hash":digest(plan),"url":plan["url"],"purpose":"acquire"}
        body={"schema":ATTESTATION,"purpose":"source_route_approval","subject_hash":digest(grant),
            "reviewer":"synthetic","decision":"approved","reviewed_at":NOW,"evidence_kind":"synthetic",
            "external_reference":{"reference":"synthetic rights exercise","sha256":digest(grant)},
            "source_policy_id":policy["policy_id"],"source_policy_fingerprint":policy_basis(policy),**att_fields}
        receipt=put_receipt(self.store,"operator_attestation",body,observed_at=NOW)
        policy["route_contracts"]={"sec-full-index":{**grant,"approval_ref":receipt["receipt_id"]}}
        return receipt
    def test_only_verified_routes_and_exact_quarter_are_planned(self):
        for route in ("sec-full-index","fda-oncology-index","fda-medwatch-rss","beam-issuer-rss"):
            plan=route_plan(route); self.assertFalse(plan["enabled"]); self.assertEqual(plan["network_requests"],0)
        with self.assertRaises(ValueError): route_plan("guessed-issuer-feed")
        with self.assertRaises(ValueError): route_plan("sec-quarterly-index")
        self.assertTrue(route_plan("sec-quarterly-index",year=2026,quarter=4)["url"].endswith('/2026/QTR4/master.idx'))
        with self.assertRaises(ValueError): parse_snapshot(RAW,route_id="sec-quarterly-index",year=2026,quarter=3,observed_at=NOW)
    def test_placeholder_missing_synthetic_expired_revoked_and_drifted_grants_fail_before_transport(self):
        policy=self.policy(); client=Mock(); client.policy=policy
        for grant in ({},{"contract":VERSION,"approved":True,"receipt_ref":"yes"}):
            policy["route_contracts"]={"sec-full-index":grant}
            with self.assertRaises(PermissionError): collect_snapshot(self.store,client,route_id="sec-full-index",now=NOW)
        client.get.assert_not_called()
        att=self.grant(policy); url=route_plan('sec-full-index')['url']
        with self.assertRaises(PermissionError): check_route(policy,'sec-full-index',url,store=self.store,now=NOW)
        self.assertEqual(check_route(policy,'sec-full-index',url,store=self.store,now=NOW,allow_synthetic=True)['route_id'],'sec-full-index')
        changed=deepcopy(policy); changed['version']='2'
        with self.assertRaises(PermissionError): check_route(changed,'sec-full-index',url,store=self.store,now=NOW,allow_synthetic=True)
        put_receipt(self.store,'attestation_revocation',{'attestation_ref':att['receipt_id']},observed_at=NOW,refs=[att['receipt_id']])
        with self.assertRaises(PermissionError): check_route(policy,'sec-full-index',url,store=self.store,now=NOW,allow_synthetic=True)
        policy=self.policy(); self.grant(policy,expires_at=NOW)
        with self.assertRaises(PermissionError): check_route(policy,'sec-full-index',url,store=self.store,now=NOW,allow_synthetic=True)
    def test_contract_feed_requires_guid_timezone_and_unique_native_identity(self):
        route=route_plan('beam-issuer-rss'); url=route['url']
        item='<item><title>Fixture</title><link>https://investors.beamtx.com/news/fixture</link><guid>R1</guid><pubDate>Tue, 20 Oct 2026 07:00:00 -0400</pubDate></item>'
        def parse(items): return parse_feed(('<rss><channel>'+items+'</channel></rss>').encode(),url=url,route_id='beam-issuer-rss')
        self.assertEqual(parse(item)['gaps'],[])
        self.assertIn('stable_native_id_missing',parse(item.replace('<guid>R1</guid>',''))['gaps'])
        self.assertIn('unusable_source_time',parse(item.replace(' -0400',''))['gaps'])
        self.assertIn('conflicting_duplicate_native_id',parse(item+item.replace('Fixture','Changed'))['gaps'])
        self.assertTrue(parse(item*100)['result_cap_reached'])
        self.assertIn('empty_feed_inventory',parse('')['gaps'])
        with self.assertRaises(ValueError): parse_feed(b'<rss/>',url=url+'/wrong',route_id='beam-issuer-rss')
        with self.assertRaises(ValueError): parse_feed(b'<!DOCTYPE rss><rss/>',url=url,route_id='beam-issuer-rss')
    def test_canonical_fda_route_preserves_redirect_boundary_and_midnight_semantics(self):
        plan=route_plan('fda-medwatch-rss')
        self.assertEqual(plan['url'],'https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml')
        raw=b'<rss><channel><item><title>Fixture</title><guid>R1</guid><link>https://www.fda.gov/safety/fixture</link><pubDate>Tue, 20 Oct 2026 00:00:00 EDT</pubDate></item></channel></rss>'
        row=parse_feed(raw,url=plan['url'],route_id='fda-medwatch-rss')['rows'][0]
        self.assertEqual(row['time_semantics'],'feed_publication_not_proven_first_availability')
        self.assertEqual(plan['retention'],'unknown')
    def test_full_index_quarter_rollover_cannot_be_treated_as_mass_removal(self):
        from omni.index_snapshots import record_snapshot, compare_snapshots
        old=parse_snapshot(RAW,route_id="sec-full-index",observed_at=NOW)
        self.assertEqual((old["year"],old["quarter"]),(2026,4))
        first=record_snapshot(self.store,old,source_id="sec-edgar",policy_hash=digest({}),now=NOW)
        replay=record_snapshot(self.store,old,source_id="sec-edgar",policy_hash=digest({}),now="2026-10-18T12:01:00Z")
        self.assertEqual(first["receipt_id"],replay["receipt_id"])
        future="2027-01-05T12:00:00Z"
        with self.assertRaisesRegex(ValueError,"out-of-scope"):
            parse_snapshot(RAW,route_id="sec-full-index",observed_at=future)
        empty=b"CIK|Company Name|Form Type|Date Filed|Filename\n--------------------\n"
        second=record_snapshot(self.store,parse_snapshot(empty,route_id="sec-full-index",observed_at=future),source_id="sec-edgar",policy_hash=digest({}),now=future)
        with self.assertRaisesRegex(ValueError,"scope/version mismatch"):
            compare_snapshots(self.store,first["receipt_id"],second["receipt_id"],now=future)


if __name__=='__main__': unittest.main()
