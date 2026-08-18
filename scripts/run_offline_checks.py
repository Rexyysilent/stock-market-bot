"""Run the deterministic, network-free regression suite used by CI."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    "test_pipeline_hygiene.py",
    "test_temporal_integrity.py",
    "test_headline_providers.py",
    "test_headlines_relevance.py",
    "test_headline_lanes.py",
    "test_headline_lane_edges.py",
    "test_headline_export_contract.py",
    "test_export_schema.py",
    "test_signal_ledger.py",
    "test_sniper_signals.py",
    "test_timestamp_policy.py",
    "test_twitter_health.py",
    "test_apewisdom_social.py",
    "test_ceo_ca_verification.py",
    "test_earnings_calendar.py",
    "test_insider_direction.py",
    "test_openinsider_params.py",
    "test_openinsider_resilience.py",
    "test_openinsider_integration.py",
    "test_reddit_rss_fallback.py",
    "test_public_positioning.py",
    "test_security_regressions.py",
)


def main() -> int:
    for relative_path in CHECKS:
        path = ROOT / relative_path
        if not path.is_file():
            print(f"missing offline check: {relative_path}", file=sys.stderr)
            return 2
        print(f"RUN {relative_path}", flush=True)
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            return result.returncode
    print(f"ALL {len(CHECKS)} OFFLINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
