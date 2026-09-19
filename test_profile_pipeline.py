"""Offline end-to-end profile export through ``marketbot.run_profile``."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.validate_export_schema import DEFAULT_SCHEMA, load_json, validation_errors


ROOT = Path(__file__).resolve().parent


PROFILE = {
    "schema_version": 1,
    "name": "pipeline-synthetic",
    "equities": [
        {"symbol": "AAA", "aliases": ["alpha synthetic"]},
        {"symbol": "BBB", "aliases": ["beta synthetic"]},
    ],
    "funds": ["SPY"],
    "futures": [],
    "indices": [],
    "focus_ticker": None,
    "groups": {"uranium": [], "defense": [], "cash_runway": []},
    "baskets": {"growth": [], "defensive": []},
    "relative_return_pairs": [],
}


SUBPROCESS = r'''
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import sys


class ExportPatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def create_module(self, spec):
        creator = getattr(self.wrapped, "create_module", None)
        return creator(spec) if creator else None

    def exec_module(self, module):
        self.wrapped.exec_module(module)

        module.OpenInsiderAgent.acquire_run_trades = lambda self: []
        module.OpenInsiderAgent.get_health = lambda self: {}
        module.OpenInsiderAgent.close = lambda self: None

        module.NewsAgent.get_global_headlines = lambda self: []
        module.NewsAgent.get_scored_headlines = lambda self: []
        module.NewsAgent.get_dropped_headlines = lambda self: []
        module.NewsAgent.get_pool_diagnostics = lambda self: {
            "fetched_count": 0, "fresh_before_relevance": 0,
            "fresh_relevant_count": 0, "selected_count": 0,
            "accounted_candidate_count": 0, "selected_lane_counts": {},
            "editorial_shadow": {
                "mode": "off", "candidate_count": 0,
                "fresh_candidate_count": 0, "records": [],
            },
        }

        module.SocialAgent.get_whisper = lambda self: []
        module.SocialAgent.get_apewisdom_attention = lambda self: []
        module.SocialAgent.get_apewisdom_top200 = lambda self: []
        module.SocialAgent.get_retail_contrarian_index = lambda self: {
            "subreddits": [], "biz": [], "alerts": [], "as_of": None,
        }
        module.SocialAgent.get_health = lambda self: {}

        module.WatcherAgent.get_full_report_data = lambda self: {
            "AAA": {"price": 0.0, "change_pct": 0.0, "as_of": None,
                    "observed_at": None, "source_session": None,
                    "session_complete": False},
            "BBB": None,
        }
        module.WatcherAgent.get_all_technicals = lambda self: {}
        module.WatcherAgent.get_all_options_flow = lambda self: {}
        module.WatcherAgent.get_earnings_calendar = lambda self: []
        module.WatcherAgent.get_sector_rotation = lambda self: {
            "alerts": [], "coverage": {}, "comparison_calendar": "NYSE",
            "as_of": None, "growth_5d": 0.0, "defensive_5d": None,
            "spread_5d": None, "growth_20d": 0.0,
            "defensive_20d": None, "spread_20d": None,
            "signal": "UNAVAILABLE",
            "interpretation": "Synthetic missing/zero rendering fixture.",
        }
        module.WatcherAgent.get_vix_term_structure = lambda self: {
            "vix": None, "vix3m": None, "vix_vix3m_ratio": None,
            "structure": "UNAVAILABLE", "regime": "UNAVAILABLE", "as_of": None,
        }
        module.WatcherAgent.check_instrument_relative_return_spread = lambda self: {
            "pairs": [], "alerts": [], "excluded_pairs": [],
            "comparison_calendar": "NYSE", "as_of": None,
        }

        module.ResearchAgent.get_ceo_ca_signals = lambda self: []
        module.ResearchAgent.get_clinical_trials = lambda self: []
        module.ResearchAgent.get_pdufa_with_financials = lambda self: {
            "catalysts": [], "alerts": [], "financials": {},
        }
        module.ResearchAgent.get_adcom_calendar = lambda self: []
        module.ResearchAgent.get_dropped_ceo_signals = lambda self: []
        module.ResearchAgent.get_dropped_ceo_quality = lambda self: []
        module.ResearchAgent.get_ceo_ca_health = lambda self: {}

        module.TwitterAgent.get_twitter_intel = lambda self, *_args: []
        module.TwitterAgent.get_dropped_twitter_signals = lambda self: []
        module.TwitterAgent.get_health = lambda self: {}

        module.SECAgent.scan_all_watchlist = lambda self, *_args: []
        module.SECAgent.detect_insider_clusters = lambda self, *_args: []
        module.SECAgent.get_health = lambda self: {}


class ExportPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != "export_for_gemini":
            return None
        sys.meta_path.remove(self)
        try:
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        spec.loader = ExportPatchLoader(spec.loader)
        return spec


sys.meta_path.insert(0, ExportPatchFinder())
from marketbot import run_profile
from universe_profile import load_profile

profile = load_profile(sys.argv[1])
raise SystemExit(run_profile(profile, sys.argv[2], "run"))
'''


class ProfilePipelineTests(unittest.TestCase):
    def test_actual_export_pipeline_is_profiled_isolated_and_missing_safe(self):
        with TemporaryDirectory() as tempdir:
            base = Path(tempdir)
            workspace = base / "workspace"
            profile_path = base / "profile.json"
            profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            legacy = base / "legacy"
            legacy.mkdir()
            sentinel = legacy / "preserve.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")

            env = dict(os.environ, PYTHON_DOTENV_DISABLED="1",
                       SEC_USER_AGENT="Offline Contract offline@example.test")
            pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(ROOT) if not pythonpath else str(ROOT) + os.pathsep + pythonpath
            result = subprocess.run(
                [sys.executable, "-c", SUBPROCESS, str(profile_path), str(workspace)],
                cwd=base, env=env, text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")
            self.assertEqual(set(base.iterdir()), {workspace, profile_path, legacy})

            brief_path = workspace / "daily_brief.json"
            text_path = workspace / "daily_brief.txt"
            self.assertTrue(brief_path.is_file())
            self.assertTrue(text_path.is_file())
            payload = brief_path.read_bytes()
            document = json.loads(payload)

            self.assertEqual(document["schema_version"], "2.9")
            self.assertEqual(document["pipeline_version"], "2.6.4")
            self.assertEqual(document["universe"]["profile"], PROFILE)
            self.assertEqual(document["universe"]["instrumented_tickers"], ["AAA", "BBB", "SPY"])
            self.assertEqual(document["universe"]["editorial_only_tickers"], [])
            self.assertEqual(document["universe"]["editorial_coverage_mode"], "off")
            self.assertEqual([], validation_errors(document, load_json(DEFAULT_SCHEMA)))

            prices = {row["ticker"]: row for row in document["sections"]["prices"]}
            self.assertEqual(prices["AAA"]["price"], 0.0)
            self.assertEqual(prices["AAA"]["change_pct"], 0.0)
            self.assertIsNone(prices["BBB"]["price"])
            rotation = document["sections"]["sector_rotation"]
            self.assertEqual(rotation["growth_5d"], 0.0)
            self.assertIsNone(rotation["defensive_5d"])
            rendered = text_path.read_text(encoding="utf-8")
            self.assertIn("AAA: $0.00", rendered)
            self.assertIn("BBB: N/A", rendered)
            self.assertIn("Growth +0.0% | Defensive N/A", rendered)

            canonical_hash = hashlib.sha256(payload).hexdigest()
            archives = list((workspace / "archive" / "briefs").glob("*.json"))
            snapshots = list((workspace / "briefs").rglob("daily_brief*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(hashlib.sha256(archives[0].read_bytes()).hexdigest(), canonical_hash)
            self.assertEqual(hashlib.sha256(snapshots[0].read_bytes()).hexdigest(), canonical_hash)


if __name__ == "__main__":
    unittest.main()
