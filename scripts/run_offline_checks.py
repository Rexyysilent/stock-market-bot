"""Run the deterministic, network-free regression suite used by CI."""

import argparse
import json
import os
import time
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECK_TIMEOUT_SECONDS = 120
CHECKS = (
    'test_pipeline_hygiene.py',
    'test_temporal_integrity.py',
    'test_headline_providers.py',
    'test_headlines_relevance.py',
    'test_headline_lanes.py',
    'test_editorial_ticker_tiers.py',
    'test_coverage_policy.py',
    'test_evidence_intake.py',
    'test_shadow_noninterference.py',
    'test_dynamic_focus.py',
    'test_focus_duplicate_occurrence.py',
    'test_headline_lane_edges.py',
    'test_headline_export_contract.py',
    'test_export_schema.py',
    'test_signal_ledger.py',
    'test_sniper_signals.py',
    'test_timestamp_policy.py',
    'test_twitter_health.py',
    'test_apewisdom_social.py',
    'test_ceo_ca_verification.py',
    'test_earnings_calendar.py',
    'test_insider_direction.py',
    'test_openinsider_params.py',
    'test_openinsider_resilience.py',
    'test_openinsider_integration.py',
    'test_reddit_rss_fallback.py',
    'test_security_regressions.py',
    'test_acquisition_security.py',
    'test_ledger_price_cache.py',
    'test_ledger_statistics.py',
    'test_session_returns.py',
    'test_watcher_comparisons.py',
    'test_universe_profile.py',
    'test_brief_tools.py',
    'test_dashboard_server.py',
    'test_dashboard_contract.py',
    'test_public_positioning.py',
    'test_ledger_outcome_retry.py',
    'test_scheduled_launcher.py',
    'test_profile_export_contract.py',
    'test_profile_pipeline.py',
    'test_diagnostics.py',
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = []
    code = 0
    child_env = dict(os.environ)
    child_env["PYTHON_DOTENV_DISABLED"] = "1"
    child_env["EDITORIAL_COVERAGE_MODE"] = "shadow"
    child_env["EVIDENCE_INTAKE_MODE"] = "shadow"
    child_env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "scripts" / "offline_guard"), str(ROOT)))
    try:
        for relative_path in CHECKS:
            path = ROOT / relative_path
            if not path.is_file():
                results.append({"test": relative_path, "status": "missing", "returncode": 2})
                code = 2
                break
            print(f"RUN {relative_path}", flush=True)
            started = time.monotonic()
            try:
                result = subprocess.run([sys.executable, str(path)], cwd=ROOT,
                                        check=False, timeout=CHECK_TIMEOUT_SECONDS, env=child_env)
                code = result.returncode
                status = "passed" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                code, status = 124, "timeout"
                print(f"TIMEOUT {relative_path} after {CHECK_TIMEOUT_SECONDS}s", file=sys.stderr)
            results.append({"test": relative_path, "status": status, "returncode": code,
                            "duration_seconds": round(time.monotonic() - started, 3)})
            if code:
                break
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({
                "python": sys.version, "configured_checks": len(CHECKS),
                "executed_checks": len(results), "returncode": code, "results": results,
                "scope": "deterministic fixtures and local-loopback server checks; no live-provider certification",
            }, indent=2) + "\n", encoding="utf-8")
    if not code:
        print(f"ALL {len(CHECKS)} OFFLINE CHECKS PASSED")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
