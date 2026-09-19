"""Offline contract tests for profiled exports and fresh-process consumers."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.validate_export_schema import DEFAULT_SCHEMA, load_json, validation_errors
from universe_profile import fingerprint, profile_plan, profile_policy_manifest, validate_profile


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "exports" / "daily_brief.valid.json"


def _version(tickers):
    return hashlib.sha256(",".join(tickers).encode("utf-8")).hexdigest()[:8]


def _profile_for(tickers):
    funds = {"URA", "USO", "GLD", "PSLV", "ITA"}
    return validate_profile({
        "schema_version": 1,
        "name": "synthetic-contract",
        "equities": [
            {"symbol": ticker, "aliases": []}
            for ticker in tickers
            if not ticker.startswith("^") and not ticker.endswith("=F") and ticker not in funds
        ],
        "funds": [ticker for ticker in tickers if ticker in funds],
        "futures": [ticker for ticker in tickers if ticker.endswith("=F")],
        "indices": [ticker for ticker in tickers if ticker.startswith("^")],
        "focus_ticker": None,
        "groups": {"uranium": [], "defense": [], "cash_runway": []},
        "baskets": {"growth": [], "defensive": []},
        "relative_return_pairs": [],
    })


def _profiled_document():
    document = load_json(FIXTURE)
    legacy_tickers = list(document["universe"]["tickers"])
    profile = _profile_for(legacy_tickers)
    tickers = profile_plan(profile)["active_tickers"]
    universe = document["universe"]
    document["schema_version"] = "2.9"
    document["pipeline_version"] = "2.6.4"
    universe.update({
        "profile": profile,
        "coverage_policy": profile_policy_manifest(profile),
        "name": f"{profile['name']}:{fingerprint(profile)}",
        "version": _version(tickers),
        "tickers": tickers,
        "instrumented_version": _version(tickers),
        "instrumented_tickers": tickers,
        "editorial_only_tickers": [],
        "focus_eligible_tickers": tickers,
        "editorial_coverage_mode": "off",
        "configured_focus_ticker": None,
    })
    document["data_quality"]["universe_contract_version"] = "2.9-profile-universe-1"
    dropped = document["data_quality"]["headlines_dropped"]
    document["data_quality"]["headlines_dropped"] = [
        row for row in dropped if row.get("reason") != "editorial_shadow_suppressed"
    ]
    pool = document["data_quality"]["headline_pool"]
    removed = len(dropped) - len(document["data_quality"]["headlines_dropped"])
    pool["fetched_count"] -= removed
    pool["fresh_before_relevance"] -= removed
    pool["accounted_candidate_count"] -= removed
    pool["editorial_shadow"] = {
        "mode": "off", "candidate_count": 0,
        "fresh_candidate_count": 0, "records": [],
    }
    return document


class ProfileExportContractTests(unittest.TestCase):
    def errors(self, document):
        return validation_errors(document, load_json(DEFAULT_SCHEMA))

    def test_profiled_brief_validates_with_default_schema_selection(self):
        document = _profiled_document()
        self.assertEqual([], self.errors(document))

        with TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "profiled.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_export_schema.py"), str(path)],
                cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VALID", result.stdout)

    def test_profile_membership_identity_and_policy_fail_closed(self):
        valid = _profiled_document()
        mutations = []

        membership = copy.deepcopy(valid)
        membership["universe"]["instrumented_tickers"] = membership["universe"]["instrumented_tickers"][:-1]
        mutations.append(membership)

        identity = copy.deepcopy(valid)
        identity["universe"]["name"] = "synthetic-contract:" + "0" * 64
        mutations.append(identity)

        policy = copy.deepcopy(valid)
        policy["universe"]["coverage_policy"]["fingerprint"] = "0" * 64
        mutations.append(policy)

        for document in mutations:
            with self.subTest(universe=document["universe"]):
                self.assertTrue(self.errors(document))

    def test_default_schema_cannot_bypass_profile_contract(self):
        document = _profiled_document()
        document["universe"]["editorial_coverage_mode"] = "active"
        errors = self.errors(document)
        self.assertTrue(errors)
        self.assertTrue(any("off" in error.message for error in errors))

    def test_schema_28_remains_bound_to_default_cohorts(self):
        document = load_json(FIXTURE)
        self.assertEqual([], self.errors(document))
        document["universe"]["instrumented_tickers"][0] = "AAPL"
        self.assertTrue(any(
            "versioned policy" in error.message or "concatenation" in error.message
            for error in self.errors(document)
        ))

    def test_fresh_process_projects_profile_to_all_imported_consumers(self):
        profile = _profile_for(["AAA", "BBB", "SPY", "SI=F", "^VIX"])
        with TemporaryDirectory() as tempdir:
            profile_path = Path(tempdir) / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            code = r'''
import json
import config
from universe_profile import apply_profile, load_profile
apply_profile(config, load_profile(__import__("sys").argv[1]))
import coverage_policy
import export_for_gemini
import ledger.config
import signals
import agents.news_agent
import agents.sec_agent
import agents.social_agent
import agents.watcher_agent
print(json.dumps({
  "config_all": list(config.ALL_TICKERS),
  "config_signal": list(config.SIGNAL_ELIGIBLE_TICKERS),
  "config_editorial": list(config.EDITORIAL_ONLY_TICKERS),
  "config_coverage": list(config.EDITORIAL_COVERAGE_TICKERS),
  "config_mode": config.EDITORIAL_COVERAGE_MODE,
  "schema": config.SCHEMA_VERSION,
  "export_signal": list(export_for_gemini.SIGNAL_ELIGIBLE_TICKERS),
  "export_editorial": list(export_for_gemini.EDITORIAL_ONLY_TICKERS),
  "export_coverage": list(export_for_gemini.EDITORIAL_COVERAGE_TICKERS),
  "ledger": list(ledger.config.UNIVERSE_TICKERS),
  "signals": list(signals.SIGNAL_ELIGIBLE_TICKERS),
  "news_signal": list(agents.news_agent.SIGNAL_ELIGIBLE_TICKERS),
  "news_coverage": list(agents.news_agent.EDITORIAL_COVERAGE_TICKERS),
  "news_editorial": list(agents.news_agent.EDITORIAL_ONLY_TICKER_SET),
  "sec": list(agents.sec_agent.ALL_TICKERS),
  "social": list(agents.social_agent.SIGNAL_ELIGIBLE_TICKERS),
  "policy_prices": list(coverage_policy.coverage_policy_manifest("active")["collector_targets"]["prices_technicals"]),
  "policy_fmp": list(coverage_policy.fmp_acquisition_tickers("active")),
  "watchlist": agents.watcher_agent.WatcherAgent().watchlist,
  "pipeline": export_for_gemini.PIPELINE_VERSION,
}))
'''
            env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
            result = subprocess.run(
                [sys.executable, "-c", code, str(profile_path)], cwd=ROOT, env=env,
                text=True, capture_output=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = json.loads(result.stdout)
        expected = ["AAA", "BBB", "SPY", "SI=F", "^VIX"]
        for key in ("config_all", "config_signal", "config_coverage", "export_signal",
                    "export_coverage", "ledger", "signals", "news_signal",
                    "news_coverage", "sec", "social", "policy_prices"):
            self.assertEqual(actual[key], expected, key)
        self.assertEqual(actual["config_editorial"], [])
        self.assertEqual(actual["export_editorial"], [])
        self.assertEqual(actual["news_editorial"], [])
        self.assertEqual(actual["config_mode"], "off")
        self.assertEqual(actual["schema"], "2.9")
        self.assertEqual(actual["pipeline"], "2.6.4")
        self.assertEqual(actual["policy_fmp"], ["AAA", "BBB", "SPY"])
        self.assertEqual(actual["watchlist"], ["AAA", "BBB", "SPY", "SI=F"])


if __name__ == "__main__":
    unittest.main()
