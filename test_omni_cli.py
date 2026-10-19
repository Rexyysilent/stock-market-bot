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


if __name__=="__main__": unittest.main()
