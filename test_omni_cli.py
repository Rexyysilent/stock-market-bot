"""Exercise the actual local CLI from another directory, no provider requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_omni_events import POLICY

ROOT=Path(__file__).resolve().parent


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.folder=Path(self.temp.name)
        self.sources=self.folder/"sources.json"
        self.sources.write_text(json.dumps({"default_mode":"off","policies":{POLICY["policy_id"]:POLICY},"feeds":[]}),encoding="utf-8")
    def tearDown(self): self.temp.cleanup()
    def cli(self,*args,custom=False):
        env=dict(os.environ); env["PYTHONPATH"]=os.pathsep.join((str(ROOT/"scripts/offline_guard"),str(ROOT))); env["PYTHON_DOTENV_DISABLED"]="1"
        cmd=[sys.executable,"-m","omni","--db",str(self.folder/"events.db")]
        if custom: cmd += ["--sources",str(self.sources)]
        p=subprocess.run(cmd+list(args),cwd=self.folder,env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        return json.loads(p.stdout)
    def test_plan_and_watch_default_are_network_free_and_no_db_created(self):
        self.assertEqual(self.cli("plan")["network_requests"],0)
        self.assertEqual(self.cli("watch","sec-edgar")["status"],"dry_run")
        self.assertEqual(self.cli("reconcile-index","--start-day","2026-10-16","--end-day","2026-10-16")["status"],"dry_run")
        self.assertEqual(self.cli("regulatory")["status"],"dry_run")
        self.assertIsNone(self.cli("pilot-plan")["frozen_at"])
        self.assertEqual(self.cli("expansion-plan")["network_requests"],0)
        self.assertFalse(self.cli("vertical-plan")["automatic_activation"])
        query=self.cli("watch","sec-edgar","--target-family","clinical_result")
        self.assertEqual(query["target_family"],"clinical_result")
        self.assertEqual(len(query["query_hash"]),64)
        self.assertFalse(query["measurement_context_validated"])
        self.assertFalse((self.folder/"events.db").exists())
    def test_actual_ingest_queue_and_shadow_brief(self):
        data=dict(url="https://issuer.example/releases/1",native_id="fixture",title="Synthetic input",
            text="INTerpath-001 met its primary endpoint.",published="2026-10-18T08:00:00Z",observed_at="2026-10-18T08:10:00Z",
            source_class="issuer",reporting_party="synthetic-company",body_complete=True,evidence_kind="synthetic")
        path=self.folder/"input.json"; path.write_text(json.dumps(data),encoding="utf-8")
        self.assertEqual(self.cli("ingest",str(path),"--source",POLICY["policy_id"],custom=True)["new_events"],1)
        self.assertEqual(len(self.cli("queue",custom=True)["items"]),1)
        self.assertEqual(self.cli("brief",custom=True)["cards"],[])
    def test_empty_evaluation_never_reports_completion(self):
        path=self.folder/"empty.json"; path.write_text("{}",encoding="utf-8")
        result=self.cli("evaluate",str(path))
        self.assertEqual(result["manual_pilot"]["status"],"pending")
        self.assertEqual(result["dependent_gates"]["OMNI-12"]["status"],"pending")

    def test_ingest_diagnostics_do_not_render_nested_unapproved_source_text(self):
        data=dict(url="https://issuer.example/releases/2",native_id="fixture-abstention",title="Synthetic input",
            text="INTerpath-001 reported a hazard ratio of 0.50.",published="2026-10-18T08:00:00Z",observed_at="2026-10-18T08:10:00Z",
            source_class="issuer",reporting_party="synthetic-company",body_complete=True,evidence_kind="synthetic")
        path=self.folder/"abstention.json"; path.write_text(json.dumps(data),encoding="utf-8")
        result=self.cli("ingest",str(path),"--source",POLICY["policy_id"],custom=True)
        self.assertTrue(result['leads'])
        self.assertNotIn('hazard ratio',json.dumps(result))
        self.assertNotIn('0.50',json.dumps(result))
        self.assertTrue(all(lead['source_fields_withheld'] for lead in result['leads']))

    def test_expansion_commands_dispatch_and_report_structured_missing_evidence(self):
        from unittest.mock import patch
        from omni.__main__ import main
        path=self.folder/"candidates.json"; path.write_text('["MRNA"]',encoding="utf-8")
        with patch('omni.expansion_cli.dispatch',return_value={"status":"pending"}) as dispatch, patch('builtins.print'):
            self.assertEqual(main(["--db",str(self.folder/"events.db"),"expansion-propose","missing",str(path)]),0)
        self.assertEqual(len(dispatch.call_args.args),1)
        self.assertIsNotNone(dispatch.call_args.kwargs['instruments'])
        self.assertIn('current_coverage',dispatch.call_args.kwargs)


if __name__=="__main__": unittest.main()
