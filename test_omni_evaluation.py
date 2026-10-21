"""OMNI-09 release gates use real denominators and reject simulated pilot rows."""
import unittest
from omni.evaluation import wilson,evaluate,dependent_gates

MANIFEST=dict(implementation_hash="impl",corpus_hash="corpus",source_scope_hash="scope",frozen_at="2026-10-18T00:00:00Z")


class EvaluationTests(unittest.TestCase):
    def test_known_wilson_examples(self):
        self.assertAlmostEqual(wilson(10,10)["lower"],.7224672,places=6)
        self.assertAlmostEqual(wilson(294,300)["lower"],.9570601,places=5)
        self.assertIsNone(wilson(0,0)["estimate"])
    def case(self,i=1):
        return dict(event_case_id=str(i),evidence_kind="prospective",adjudicator="fixture-reviewer",correct=True,
                    adjudication_receipt="synthetic-test-receipt",source_scope_hash="scope",
                    implementation_hash="impl",corpus_hash="corpus",meaningfully_exercised=True,observed_at="2026-10-18T12:00:00Z",
                    outside_cohort=True,proposed_publication=True,family="clinical_result",source_class="issuer")
    def test_synthetic_and_repeated_cases_do_not_mature(self):
        row=self.case(); row["evidence_kind"]="synthetic"
        result=evaluate(MANIFEST,[row]*100)
        self.assertEqual(result["manual_pilot"]["meaningful_new_issuer_cases"],0)
        self.assertEqual(result["manual_pilot"]["status"],"pending")
        result=evaluate(MANIFEST,[self.case()]*100)
        self.assertEqual(result["precision"]["n"],1)
    def test_many_reruns_one_session_and_missing_review_fail(self):
        runs=[dict(execution_mode="scheduled",eligible=True,receipt="fixture-receipt",scheduler_receipt='fixture-schedule',
                   source_cycle_ids=['cycle-'+str(i)],session="2026-10-18",run_id=str(i),implementation_hash="impl",
                   corpus_hash='corpus',source_scope_hash='scope',started_at="2026-10-18T12:00:00Z",completed_at='2026-10-18T12:01:00Z') for i in range(10)]
        cases=[self.case(i) for i in range(100)]; cases[0].update(published=True)
        result=evaluate(MANIFEST,cases,runs)
        self.assertEqual(result["manual_pilot"]["eligible_scheduled_sessions"],0)
        self.assertEqual(result["manual_pilot"]["descriptive_scheduled_sessions"],1)
        self.assertIn("receipt_backed_protocol_required",result["manual_pilot"]["reason_codes"])
        self.assertIn("outside_cohort_publication_without_review",result["manual_pilot"]["reason_codes"])
        self.assertFalse(result["automatic_activation"])
    def test_gold_misses_and_unknown_issuer_are_visible(self):
        gold=[dict(event_id=str(i),prespecified=True,in_source_scope=True,detected=i<95,unknown_issuer=i>=95,
                   adjudication_receipt="synthetic-test-receipt",corpus_hash="corpus",source_scope_hash="scope",implementation_hash='impl',
                   evidence_kind='prospective',evaluation_track='prospective',observed_at='2026-10-18T12:00:00Z',reviewed_at='2026-10-18T13:00:00Z') for i in range(100)]
        r=evaluate(MANIFEST,gold=gold)["recall"]
        self.assertEqual(r["overall"]["estimate"],.95); self.assertEqual(r["unknown_issuers"]["estimate"],0)
        self.assertEqual(r["status"],"pending"); self.assertEqual(len(r["misses"]),5)

    def test_missing_gold_and_replay_cannot_mature_prospective_recall(self):
        rows=[dict(event_id=str(i),prespecified=True,in_source_scope=True,detected=True,unknown_issuer=True,
                   adjudication_receipt='synthetic-test-receipt',corpus_hash='corpus',source_scope_hash='scope',implementation_hash='impl',
                   evidence_kind='prospective',evaluation_track='prospective',observed_at='2026-10-18T12:00:00Z',reviewed_at='2026-10-18T13:00:00Z') for i in range(100)]
        complete=dict(MANIFEST,gold_event_ids=[str(i) for i in range(100)])
        self.assertEqual(evaluate(complete,gold=rows)['recall']['status'],'pending')
        self.assertIn('prospective_inventory_not_verified', evaluate(complete,gold=rows)['recall']['reason_codes'])
        incomplete=dict(complete,gold_event_ids=[str(i) for i in range(200)])
        r=evaluate(incomplete,gold=rows)['recall']
        self.assertEqual(r['overall']['n'],100); self.assertEqual(r['overall']['estimate'],1)
        self.assertEqual((r['n_expected'],r['n_adjudicated'],r['n_unadjudicated']),(200,100,100))
        self.assertEqual(r['status'],'pending'); self.assertIn('gold_adjudications_incomplete',r['reason_codes'])
        for row in rows: row.update(evidence_kind='historical_replay',evaluation_track='frozen_corpus_audit')
        r=evaluate(complete,gold=rows)['recall']
        self.assertEqual(r['overall']['n'],0); self.assertEqual(r['frozen_corpus_audit']['overall']['n'],100)
        self.assertEqual(r['status'],'pending')

    def test_relabelled_sessions_or_reused_cycles_are_excluded(self):
        runs=[dict(execution_mode='scheduled',eligible=True,receipt='synthetic-test-receipt',scheduler_receipt='synthetic-scheduler',
                   session=f'2026-10-{18+i}',run_id=str(i),implementation_hash='impl',corpus_hash='corpus',source_scope_hash='scope',
                   source_cycle_ids=['same-cycle'],started_at='2026-10-18T12:00:00Z',completed_at='2026-10-18T12:01:00Z') for i in range(10)]
        r=evaluate(MANIFEST,runs=runs)
        self.assertEqual(r['manual_pilot']['eligible_scheduled_sessions'],0)
        self.assertEqual(r['manual_pilot']['descriptive_scheduled_sessions'],1)
        self.assertEqual(len(r['excluded_runs']),9)
        for row in runs:
            row['started_at']=row['session']+'T12:00:00Z'; row['completed_at']=row['session']+'T12:01:00Z'
        r=evaluate(MANIFEST,runs=runs)
        self.assertEqual(r['manual_pilot']['eligible_scheduled_sessions'],0)
        self.assertEqual(r['manual_pilot']['descriptive_scheduled_sessions'],1)
        self.assertTrue(all(r['reason']=='run_cycles_missing_or_reused' for r in r['excluded_runs']))
    def test_dependent_work_stays_pending_without_evidence(self):
        self.assertEqual(dependent_gates({},[])["wave_b_candidates"],[])
        r=dependent_gates({},["ALFA"],candidates=["ALFA","BETA","BETA"])
        self.assertEqual(r["wave_b_candidates"],["BETA"])
        with self.assertRaises(ValueError): dependent_gates({},[],candidates="BETA")
        self.assertEqual(r["OMNI-12"]["status"],"pending")


if __name__=="__main__": unittest.main()
