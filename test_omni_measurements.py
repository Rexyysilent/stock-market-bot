"""OMNI-08 stale/unknown inputs, actual denominators and prospective timing."""
from copy import deepcopy
import unittest
from omni.measurements import quality,readiness,information_sequence


class MeasurementTests(unittest.TestCase):
    def observation(self):
        return dict(source_session="2026-10-16",session_complete=True,source_complete=True,feature_version="test-1",
                    universe_fingerprint="frozen",pipeline_era="shadow-1",kind="price",value=0,baseline={"complete_sessions":30,"required_sessions":30})
    def check(self,row): return quality(row,latest_settled_session="2026-10-16",approved_feature_versions=("test-1",))
    def test_t24_t25_t32_missing_not_zero_and_stale_session(self):
        row=self.observation(); self.assertEqual(self.check(row)["measurement_status"],"usable")
        for field,value,reason in (("value",None,"measurement_unavailable"),("source_complete",False,"source_incomplete"),("source_session","2026-10-15","stale_measurement")):
            changed=deepcopy(row); changed[field]=value; self.assertIn(reason,self.check(changed)["reason_codes"])
    def test_t33_t34_options_context_and_unknown_regime(self):
        row=self.observation(); row["kind"]="options"; self.assertIn("options_context_only",self.check(row)["reason_codes"])
        row["kind"]="regime"; row["regime_inputs"]=[1,None]; self.assertIn("regime_inputs_unknown",self.check(row)["reason_codes"])
    def test_t35_t36_t37_usable_denominators_and_eras(self):
        rows=[dict(pipeline_era="a",feature_version="f",universe_fingerprint="u",outcome_status="measured",return_=1,direction=0) for _ in range(40)]
        for r in rows: r["return"]=r.pop("return_")
        rows[0]["direction"]=1; rows[-1]["outcome_status"]="unpriceable"; rows[-2]["pipeline_era"]="b"
        a=next(c for c in readiness(rows) if c["pipeline_era"]=="a")
        self.assertEqual(a["n_usable"],38); self.assertEqual(a["n_directional"],1); self.assertEqual(a["directional_status"],"accumulating")
        self.assertEqual(a["status_counts"]["unpriceable"],1); self.assertEqual(a["inference"],"withheld")
    def test_t40_no_hindsight_credit(self):
        r=information_sequence(published_at="2026-10-18T09:00:00Z",source_available_at=None,observed_at="2026-10-18T12:00:00Z",measurement_at="2026-10-18T11:00:00Z")
        self.assertFalse(r["hindsight_credit_allowed"]); self.assertIsNone(r["source_available_to_candidate_seconds"])
        self.assertIn("observed_after_measurement",r["reason_codes"])

    def test_uncaptured_population_and_boolean_direction_stay_unready(self):
        rows=[dict(outcome_status="measured",direction=True,**{"return":1}) for _ in range(30)]+[{}]
        r=readiness(rows)[0]
        self.assertEqual(r["n_directional"],0)
        self.assertEqual(r["status_counts"]["None"],1)
        self.assertEqual(r["descriptive_status"],"accumulating")


if __name__=="__main__": unittest.main()
