"""OMNI-05 source budgets, durable cursors and crash/outbox acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from omni.acquisition import SourceClient, Deferred, collect_edgar, filing_documents, extract_document_text
from omni.classifier import extract
from omni.identities import load_registry
from omni.store import EventStore
from omni.validator import validate
from omni.runner import watch_edgar, analyze_documents
from omni.feeds import parse_feed, watch_feed
from omni.evidence import source_decision
from source_request_gate import wait_for_sec
from test_omni_events import make_doc, POLICY, POLICIES, NOW


def response(body=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>', status=200, content_type="application/atom+xml"):
    r=Mock(status_code=status,headers={"Content-Type":content_type}); r.iter_content.return_value=[body]; return r


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.store=EventStore(self.root/"events.db")

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def client(self, session=None, **kwargs):
        policy=deepcopy(POLICY); policy["routes"].append({"host":"www.sec.gov","path_prefix":"/"})
        return SourceClient(self.store,policy,user_agent="Synthetic tests qa@example.org",now=NOW,
                            session=session or Mock(),resolver=lambda *a,**k:[(None,None,None,None,("93.184.216.34",443))],
                            sec_gate=lambda:None,sleep=lambda n:None,**kwargs)

    def test_t23_cap_before_watermark_keeps_gap(self):
        entries=''.join(f'<entry><id>{i}</id><title>8-K - Synthetic issuer</title><updated>2026-10-18T10:00:00Z</updated><link href="https://www.sec.gov/Archives/edgar/data/1/{i}-index.htm"/></entry>' for i in range(100))
        session=Mock(); session.get.side_effect=[response(('<feed xmlns="http://www.w3.org/2005/Atom">'+entries+'</feed>').encode()) for _ in range(2)]
        result=collect_edgar(self.client(session),watermark="2026-10-17T00:00:00Z",max_pages=2)
        self.assertFalse(result["complete"]); self.assertIn("result_cap_before_watermark",result["gaps"])
        self.assertEqual(len(result["rows"]),100)
        self.assertEqual(session.get.call_count,2)
        session=Mock(); session.get.return_value=response()
        resumed=collect_edgar(self.client(session),watermark="2026-10-17T00:00:00Z",continuation=result["continuation"],max_pages=1)
        self.assertIn("start=100",session.get.call_args.args[0])
        self.assertFalse(resumed["complete"])
        self.assertIn("reconciliation_requires_contiguous_verification",resumed["gaps"])

    def test_t22_exhibits_not_assumed_and_empty_shell_rejected(self):
        html=b'<table class="tableFile"><tr><td>1</td><td>Release</td><td><a href="release-other.htm">doc</a></td><td>EX-99.2</td></tr></table>'
        docs=filing_documents(html,filing_url="https://www.sec.gov/Archives/edgar/data/1/000000012326000004/index.htm")
        self.assertTrue(docs["documents"][0]["url"].endswith("release-other.htm"))
        self.assertEqual(filing_documents(b'<html></html>',filing_url="https://www.sec.gov/Archives/edgar/data/1/x")["gaps"],["exhibit_listing_missing"])
        self.assertFalse(extract_document_text(b'<html><script>fake results</script>Enable Javascript</html>')["body_complete"])

    def test_t24_t44_budget_retries_and_throttle(self):
        session=Mock(); session.get.return_value=response(status=500)
        client=self.client(session,max_attempts=2)
        with self.assertRaisesRegex(Deferred,"budget_deferred:attempt"):
            client.get("https://issuer.example/releases/1")
        self.assertEqual(session.get.call_count,2)
        throttle=response(status=429); throttle.headers["Retry-After"]="3600"
        session=Mock(); session.get.return_value=throttle
        client=self.client(session)
        with self.assertRaisesRegex(Deferred,"source_throttled:3600"):
            client.get("https://issuer.example/releases/1")
        with self.assertRaisesRegex(Deferred,"source_circuit_open"):
            client.get("https://issuer.example/releases/2")
        self.assertEqual(session.get.call_count,1)

    def test_t29_redirect_dns_and_size_fail_closed(self):
        session=Mock(); session.get.return_value=response(status=302)
        with self.assertRaisesRegex(Deferred,"http_302"):
            self.client(session).get("https://issuer.example/releases/1")
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        client=self.client(); client.resolver=lambda *a,**k:[(None,None,None,None,("127.0.0.1",443))]
        with self.assertRaises(PermissionError): client.get("https://issuer.example/releases/1")
        client.session.get.assert_not_called()
        session=Mock(); session.get.return_value=response(b'x'*5_000_001)
        with self.assertRaisesRegex(Deferred,"response_size_limit"):
            self.client(session).get("https://issuer.example/releases/1")

    def test_noncanonical_paths_are_rejected_before_dns_budget_or_request(self):
        policy=json.loads((Path(__file__).parent/'registry/event-sources.json').read_text())['policies']['sec-edgar']
        client=self.client(); client.policy=policy; client.resolver=Mock()
        good='https://www.sec.gov/Archives/edgar/data/123/0000000123-26-000001-index.html'
        self.assertIsNone(source_decision(policy,good,'acquire'))
        for path in ('../../../../files/unapproved.pdf','%2e%2e/%2e%2e/files/unapproved.pdf',
                     '%252e%252e/files/unapproved.pdf','123%2f..%2ffiles/unapproved.pdf','123/%5cfiles/unapproved.pdf',
                     '123/%00file.htm'):
            url='https://www.sec.gov/Archives/edgar/data/'+path
            with self.subTest(path=path),self.assertRaisesRegex(PermissionError,'untrusted_url'):
                client.get(url)
        client.resolver.assert_not_called(); client.session.get.assert_not_called()
        self.assertEqual(client.counts,{'metadata':0,'attempt':0,'document':0})

    def test_sec_gate_failure_persists_coverage_debt_without_http(self):
        client=self.client(); client.sec_gate=Mock(side_effect=RuntimeError('SEC request gate backlog or clock skew'))
        result=watch_edgar(self.store,client,load_registry(),now=NOW)
        self.assertIn('sec_rate_gate_unavailable',result['coverage']['gaps'])
        self.assertFalse(result['coverage']['complete']); self.assertEqual(result['status'],'partial')
        self.assertIsNone(self.store.cursor(client.policy['policy_id'])['watermark'])
        self.assertEqual(self.store.inventory()['source_cycles'],1)
        client.session.get.assert_not_called()

    def batch(self):
        registry=load_registry(); doc=make_doc("INTerpath-001 met its primary endpoint.")
        events=extract(doc,registry,POLICY,known_by=NOW)["events"]
        decisions=[validate(e,[doc],registry,POLICIES,now=NOW) for e in events]
        return dict(cycle_id="cycle-1",source_id="synthetic",observed_at=NOW,documents=[doc],events=events,
                    decisions=decisions,watermark="2026-10-18T10:00:00Z",complete=True)

    def test_t30_crash_retry_and_ack_idempotence(self):
        batch=self.batch()
        with self.assertRaises(RuntimeError): self.store.commit_cycle(**batch,fail_at="after_events")
        self.assertEqual(self.store.inventory()["events"],0)
        self.assertIsNone(self.store.cursor("synthetic")["watermark"])
        self.assertEqual(self.store.commit_cycle(**batch)["new_events"],1)
        self.assertEqual(self.store.commit_cycle(**batch)["status"],"duplicate_cycle")
        delivery=self.store.pending()[0]["delivery_id"]
        self.store.acknowledge(delivery,at=NOW); self.store.acknowledge(delivery,at=NOW)
        self.assertEqual(self.store.pending(),[])

    def test_t23_partial_cursor_and_initial_seed(self):
        batch=self.batch(); batch.update(complete=False,gaps=["initial_window_only"],initial_seed=True)
        self.store.commit_cycle(**batch)
        original=self.store.cursor("synthetic")["watermark"]
        batch.update(cycle_id="cycle-2",initial_seed=False,gaps=["result_cap_before_watermark"],watermark="2026-10-18T11:00:00Z",continuation="300")
        self.store.commit_cycle(**batch)
        self.assertEqual(self.store.cursor("synthetic")["watermark"],original)
        self.assertEqual(self.store.cursor("synthetic")["continuation"],"300")

    def test_t17_same_claim_updated_timestamp_does_not_create_event(self):
        batch=self.batch(); self.store.commit_cycle(**batch)
        doc=make_doc("INTerpath-001 met its primary endpoint.",published="2026-10-18T11:00:00Z")
        registry=load_registry(); events=extract(doc,registry,POLICY,known_by=NOW)["events"]
        batch.update(cycle_id="cycle-2",documents=[doc],events=events,decisions=[validate(e,[doc],registry,POLICIES,now=NOW) for e in events])
        self.assertEqual(self.store.commit_cycle(**batch)["new_events"],0)
        self.assertEqual(self.store.inventory()["evidence"],2)
        self.assertEqual(len(self.store.pending()),1)

    def test_unreviewed_caller_origin_cannot_merge_distinct_evidence(self):
        from omni.review import event_packet
        registry=load_registry()
        for i in range(2):
            doc=make_doc("INTerpath-001 met its primary endpoint.",native_id=str(i),origin_reference="verified-synthetic-origin")
            events=extract(doc,registry,POLICY,known_by=NOW)["events"]
            self.store.commit_cycle(cycle_id=str(i),source_id="synthetic",observed_at=NOW,documents=[doc],events=events,
                decisions=[validate(e,[doc],registry,POLICIES,now=NOW) for e in events])
        event=events[0]
        packet=event_packet(self.store,event["event_id"],event["event_revision"])
        self.assertEqual(self.store.inventory()["events"],2)
        self.assertEqual(packet["alternate_evidence_paths"],[])

    def test_refuse_production_database_before_journal_mutation(self):
        path=self.root/"ledger.db"
        c=sqlite3.connect(path); c.execute("CREATE TABLE runs(id TEXT)"); c.commit(); c.close()
        before=path.read_bytes()
        with self.assertRaises(ValueError): EventStore(path)
        self.assertEqual(path.read_bytes(),before)

    def test_shared_sec_rate_reservations(self):
        delays=[]; path=self.root/"rate.db"
        for _ in range(3): wait_for_sec(path=path,clock=lambda:100,sleep=delays.append)
        self.assertAlmostEqual(delays[0],1/3); self.assertAlmostEqual(delays[1],2/3)

    def test_watch_path_recovers_incomplete_body_without_advancing_event(self):
        feed=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:tag:sec.gov,2008:accession-number=0000000123-26-000001</id><title>8-K - Synthetic issuer</title><updated>2026-10-18T10:00:00Z</updated><link href="https://www.sec.gov/Archives/edgar/data/1/000000012326000004/index.htm"/></entry></feed>'
        listing=b'<table class="tableFile"><tr><td>1</td><td>Release</td><td><a href="other.htm">doc</a></td><td>EX-99.2</td></tr></table>'
        complete=b'<p>INTerpath-001 met its primary endpoint.</p><p>This is a synthetic filing fixture for a deterministic acquisition test. No real clinical result is asserted by this document.</p>'
        def client_for(body):
            session=Mock()
            session.get.side_effect=lambda url,**kwargs: response(feed if 'browse-edgar?' in url else listing if url.endswith('index.htm') else body)
            client=self.client(session)
            client.policy.update(source_class="filing",reporting_parties=["filing_issuer"])
            return client
        first=watch_edgar(self.store,client_for(b'Enable Javascript'),load_registry(),now=NOW,hydrate=True)
        self.assertIn("content_incomplete",first["document_gaps"])
        self.assertEqual(self.store.backlog(POLICY["policy_id"])[0]["hydration_status"],"content_incomplete")
        second=watch_edgar(self.store,client_for(complete),load_registry(),now="2026-10-18T12:01:00Z",hydrate=True)
        self.assertEqual(second["new_events"],1)
        self.assertEqual(self.store.backlog(POLICY["policy_id"]),[])
        self.assertEqual(len(self.store.pending()),1)
        third=watch_edgar(self.store,client_for(complete),load_registry(),now="2026-10-18T12:02:00Z",hydrate=True)
        self.assertEqual(third["new_events"],0)
        self.assertEqual(len(self.store.pending()),1)

    def test_more_than_three_exhibits_resume_without_refetching_completed_bodies(self):
        feed=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:tag:sec.gov,2008:accession-number=0000000123-26-000005</id><title>8-K - Synthetic issuer</title><updated>2026-10-18T10:00:00Z</updated><link href="https://www.sec.gov/Archives/edgar/data/1/000000012326000004/index.htm"/></entry></feed>'
        listing=('<table class="tableFile">'+''.join(f'<tr><td>{i}</td><td>Release</td><td><a href="{i}.htm">doc</a></td><td>EX-99.{i}</td></tr>' for i in range(5))+'</table>').encode()
        body=b'<p>INTerpath-001 met its primary endpoint.</p><p>This synthetic filing is deliberately long enough to exercise complete document hydration with five exhibits.</p>'
        calls=[]
        def get(url,**kwargs):
            calls.append(url)
            return response(feed if 'browse-edgar?' in url else listing if url.endswith('index.htm') else body)
        for now in (NOW,'2026-10-18T12:01:00Z','2026-10-18T12:02:00Z'):
            session=Mock(); session.get.side_effect=get
            client=self.client(session); client.policy.update(source_class='filing',reporting_parties=['filing_issuer'])
            result=watch_edgar(self.store,client,load_registry(),now=now,hydrate=True)
        for i in range(5): self.assertEqual(sum(url.endswith(f'/{i}.htm') for url in calls),1)
        self.assertEqual(sum(url.endswith('index.htm') for url in calls),1)
        self.assertEqual(self.store.backlog(POLICY['policy_id']),[])
        self.assertEqual(result['new_events'],0)

    def test_rss_unknown_timezone_and_empty_window_do_not_claim_coverage(self):
        raw=b'<rss><channel><item><title>Synthetic</title><link>https://issuer.example/releases/1</link><pubDate>Sun, 18 Oct 2026 10:00:00</pubDate></item></channel></rss>'
        parsed=parse_feed(raw,url="https://issuer.example/releases/feed")
        self.assertIsNone(parsed["rows"][0]["published"])
        self.assertIn("unusable_source_time",parsed["gaps"])
        legacy=raw.replace(b'https://issuer.example/releases/1',b'http://issuer.example/releases/1')
        row=parse_feed(legacy,url="https://issuer.example/releases/feed")["rows"][0]
        self.assertEqual(row["url"],"https://issuer.example/releases/1")
        self.assertEqual(row["source_link"],"http://issuer.example/releases/1")
        rss=Mock(); rss.get.return_value=response(raw,content_type="application/rss+xml; charset=utf-8")
        self.assertEqual(self.client(rss).get("https://issuer.example/releases/feed"),raw)
        batch=self.batch(); batch.update(source_id=POLICY["policy_id"])
        self.store.commit_cycle(**batch)
        session=Mock(); session.get.return_value=response(b'<rss><channel/></rss>')
        result=watch_feed(self.store,self.client(session),load_registry(),feed_url="https://issuer.example/releases/feed",now=NOW)
        self.assertIn("empty_feed_inventory",result["coverage_gaps"])
        self.assertIn("initial_window_only",result["coverage_gaps"])
        self.assertIsNone(self.store.cursor(POLICY["policy_id"])["watermark"])

    def test_feed_intervals_use_prior_observation_clock_and_exact_route_query_family(self):
        from email.utils import format_datetime
        from omni.receipts import get_receipt
        from omni.identities import digest, instant
        from omni.feeds import feed_query_plan
        url="https://issuer.example/releases/feed"
        def poll(at, published, *, target_family="clinical_result", feed_url=url):
            rows=''.join(f'<item><guid>native-{i}</guid><title>Source row {i}</title><link>https://issuer.example/releases/{i}</link><pubDate>{format_datetime(instant(stamp))}</pubDate></item>' for i,stamp in enumerate(published))
            session=Mock(); session.get.return_value=response(('<rss><channel>'+rows+'</channel></rss>').encode())
            watch_feed(self.store,self.client(session),load_registry(),feed_url=feed_url,now=at,target_family=target_family)
            health=json.loads(self.store.conn.execute('SELECT health FROM source_cycles ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            return get_receipt(self.store,health['observation_receipt_ids'][0])['payload'],health
        first,_=poll(NOW,["2026-10-18T10:00:00Z"])
        later="2026-10-18T12:01:00Z"
        second,health=poll(later,["2026-10-18T10:00:00Z","2026-10-18T11:00:00Z"])
        self.assertEqual((first['window_start'],first['window_end']),(NOW,NOW))
        self.assertEqual((second['window_start'],second['window_end']),(NOW,later))
        self.assertEqual(first['query_hash'],second['query_hash'])
        self.assertEqual(second['query_hash'],digest(feed_query_plan(url)))
        self.assertNotEqual(first['request_context_hash'],second['request_context_hash'])
        self.assertEqual(health['metrics']['request_counts']['attempt'],1)
        changed_at="2026-10-18T12:02:00Z"
        third,_=poll(changed_at,["2026-10-18T11:00:00Z"],feed_url=url+"-different")
        self.assertEqual((third['window_start'],third['window_end']),(changed_at,changed_at))
        changed_family_at="2026-10-18T12:03:00Z"
        fourth,_=poll(changed_family_at,["2026-10-18T11:00:00Z"],target_family="regulatory",feed_url=url+"-different")
        self.assertEqual((fourth['window_start'],fourth['window_end']),(changed_family_at,changed_family_at))

    def test_measured_feed_preserves_all_native_rows_and_actual_request_counts(self):
        import test_omni_expansion as expansion_support
        import test_omni_prospective as prospective_support
        from omni.feeds import feed_query_plan
        from omni.identities import digest
        from omni.receipts import get_receipt
        h=expansion_support.ExpansionTests(); h.setUp(); self.addCleanup(h.doCleanups)
        url="https://issuer.example/releases/feed"
        scope={**h.h.spec()['source_scope'][0],'route_id':'explicit-policy-feed','query_hash':digest(feed_query_plan(url))}
        proposal=h.freeze(spec_changes={'source_scope':[scope]})
        raw=b'<rss><channel><item><guid>unknown-native-issuer</guid><title>Unresolved source record</title><link>https://issuer.example/releases/unknown</link><pubDate>Wed, 21 Oct 2026 09:30:00 +0000</pubDate></item></channel></rss>'
        session=Mock(); session.get.return_value=response(raw)
        client=SourceClient(h.store,h.policies[prospective_support.SOURCE],user_agent="Synthetic tests qa@example.org",
            now=prospective_support.OBSERVED,session=session,
            resolver=lambda *a,**k:[(None,None,None,None,("93.184.216.34",443))],sleep=lambda n:None)
        kwargs=dict(feed_url=url,now=prospective_support.OBSERVED,policies=h.policies,
            measurement_proposal=proposal['receipt_id'],measurement_arm='expanded',current_coverage=h.current,
            instruments=h.instruments)
        with self.assertRaisesRegex(ValueError,'actual prospective'):
            watch_feed(h.store,client,h.registry,**kwargs)
        session.get.assert_not_called()
        with self.assertRaisesRegex(ValueError,'query differs'):
            watch_feed(h.store,client,h.registry,**{**kwargs,'feed_url':url+'-different'},allow_synthetic_measurement=True)
        session.get.assert_not_called()
        watch_feed(h.store,client,h.registry,**kwargs,allow_synthetic_measurement=True)
        health=json.loads(h.store.conn.execute('SELECT health FROM source_cycles ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        observation=get_receipt(h.store,health['observation_receipt_ids'][0])['payload']
        self.assertEqual(observation['family'],'clinical_result')
        self.assertEqual([row['native_id'] for row in observation['native_inventory']],['unknown-native-issuer'])
        self.assertEqual(health['metrics']['expansion_scope_hash'],proposal['payload']['expanded_scope_hash'])
        self.assertEqual(health['metrics']['request_counts']['attempt'],session.get.call_count)
        self.assertFalse(health['metrics']['collection_scope']['classifier_filter_applied'])
        self.assertEqual(h.store.inventory()['events'],0)

    def test_measurement_pairs_and_family_fail_before_transport(self):
        client=self.client()
        for kwargs in ({'measurement_proposal':'unresolved'}, {'measurement_arm':'baseline'},
                       {'target_family':'invalid family with source text'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                watch_edgar(self.store,client,load_registry(),now=NOW,**kwargs)
        client.session.get.assert_not_called()
        self.assertEqual(self.store.inventory()['source_cycles'],0)

    def test_conflicting_exhibits_are_combined_and_deferred_exhibits_block_review(self):
        native='urn:tag:sec.gov,2008:accession-number=0000000123-26-000001'
        filing_policy={**POLICY,'source_class':'filing'}
        docs=[make_doc(text,policy=filing_policy,source_class='filing',native_id=native+'/'+str(i),origin_reference=['filing-accession',native]) for i,text in enumerate((
            'INTerpath-001 met its primary endpoint.','INTerpath-001 did not meet its primary endpoint.'))]
        events,decisions,_=analyze_documents(docs,load_registry(),{POLICY["policy_id"]:filing_policy},now=NOW)
        self.assertEqual(len(events),1)
        self.assertEqual(set(c['value'] for c in events[0]['claims']),{'met','not_met'})
        self.assertIn('contradiction_unresolved',decisions[0]['reason_codes'])
        self.assertEqual(decisions[0]['assertion_origin_units'],1)
        self.assertIsNone(decisions[0]['independent_origins'])
        partial,checked,_=analyze_documents(docs[:1],load_registry(),{POLICY["policy_id"]:filing_policy},now=NOW,gaps=['document_budget_deferred'])
        self.assertIn('filing_documents_incomplete',checked[0]['reason_codes'])
        complete,_,_=analyze_documents(docs[:1],load_registry(),{POLICY["policy_id"]:filing_policy},now=NOW)
        self.assertNotEqual(partial[0]['event_revision'],complete[0]['event_revision'])

    def test_conflict_discovered_on_resumed_exhibit_batch_keeps_previous_claims(self):
        import json
        feed=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:tag:sec.gov,2008:accession-number=0000000123-26-000004</id><title>8-K - Synthetic issuer</title><updated>2026-10-18T10:00:00Z</updated><link href="https://www.sec.gov/Archives/edgar/data/1/000000012326000004/index.htm"/></entry></feed>'
        listing=('<table class="tableFile">'+''.join(f'<tr><td>{i}</td><td>Release</td><td><a href="{i}.htm">doc</a></td><td>EX-99.{i}</td></tr>' for i in range(4))+'</table>').encode()
        def get(url,**kwargs):
            text=('INTerpath-001 did not meet its primary endpoint.' if url.endswith('/3.htm') else 'INTerpath-001 met its primary endpoint.')
            body=('<p>'+text+'</p><p>This is a synthetic acquisition fixture with complete text and no actual claim about a real clinical trial.</p>').encode()
            return response(feed if 'browse-edgar?' in url else listing if url.endswith('index.htm') else body)
        for now in (NOW,'2026-10-18T12:01:00Z'):
            session=Mock(); session.get.side_effect=get
            client=self.client(session); client.policy.update(source_class='filing',reporting_parties=['filing_issuer'])
            watch_edgar(self.store,client,load_registry(),now=now,hydrate=True)
        row=self.store.conn.execute('SELECT e.payload,e.decision FROM events e JOIN event_heads h ON e.event_id=h.event_id AND e.revision_id=h.revision_id').fetchone()
        event,decision=map(json.loads,row)
        self.assertEqual(set(c['value'] for c in event['claims']),{'met','not_met'})
        self.assertEqual(len(event['evidence_ids']),4)
        self.assertIn('contradiction_unresolved',decision['reason_codes'])
        self.assertNotIn('filing_documents_incomplete',decision['reason_codes'])


if __name__ == "__main__": unittest.main()
