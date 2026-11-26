"""Command-line interface for the Signal Ledger."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from config import BRIEF_ARCHIVE_MIRROR_DIR

from .config import ARCHIVE_DIR, DB_PATH, STATS_JSON_PATH, STATS_MD_PATH
from .db import connect
from .ingest import import_legacy, ingest_archives
from .outcomes import mature_outcomes
from .stats import write_stats


def _verification_command(args):
    from .verification import VerificationPolicy, build_verification, render_markdown
    from .monitor import freeze_monitor, monitor_status, run_monitor
    if args.command == "monitor-init":
        declared_at = datetime.now(timezone.utc).isoformat()
        if (args.workspace / "monitor.json").is_file():
            # A retry keeps the original declaration, while freeze_monitor
            # verifies all retained hashes and rejects changed assumptions.
            from portfolio_lab.data import load_json
            previous = VerificationPolicy.from_dict(load_json(args.workspace / "policy.json"))
            declared_at = previous.declared_at
        policy = VerificationPolicy(
            policy_id=args.policy_id,
            declared_at=declared_at,
            move_threshold=args.move_threshold,
            round_trip_cost_bps=args.round_trip_cost_bps,
            horizons=tuple(args.horizons),
            entry_latency_seconds=args.entry_latency_seconds,
        )
        return freeze_monitor(args.workspace, policy, args.archives,
                              min_observation_days=args.min_observation_days)
    if args.command == "monitor-run":
        return run_monitor(args.workspace, fetch=args.fetch, as_of=args.as_of,
                           archive_dir=args.archives)
    if args.command == "monitor-status":
        return monitor_status(args.workspace)
    from portfolio_lab.data import load_json
    policy = VerificationPolicy.from_dict(load_json(args.policy))
    report = build_verification(args.db, policy=policy, as_of=args.as_of, fetch=args.fetch)
    if args.out:
        from portfolio_lab.__main__ import _private_output
        from stateutil import atomic_write_json
        target = _private_output(args.out)
        if target.suffix.lower() == ".md":
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as handle:
                handle.write(render_markdown(report))
        else:
            atomic_write_json(target, report, indent=2)
        return {"report": str(target), "policy_id": policy.policy_id,
                "fetch_requested": args.fetch, "research_only": True}
    return report


def _update():
    ingested = ingest_archives()
    outcomes = mature_outcomes()
    write_stats()
    return {"archives": ingested, **outcomes}


def _rebuild():
    target_dir = Path(DB_PATH).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ledger-rebuild-", dir=target_dir) as temp_dir:
        temp_dir = Path(temp_dir)
        temp_db = temp_dir / "ledger.db"
        temp_json = temp_dir / "stats.json"
        temp_md = temp_dir / "stats.md"
        ingested = ingest_archives(db_path=temp_db)
        outcomes = mature_outcomes(db_path=temp_db)
        write_stats(temp_db, temp_json, temp_md)
        conn = connect(temp_db)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        if Path(DB_PATH).exists():
            current = connect(DB_PATH)
            current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            current.close()
        for suffix in ("-wal", "-shm"):
            Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
        os.replace(temp_db, DB_PATH)
        os.replace(temp_json, STATS_JSON_PATH)
        os.replace(temp_md, STATS_MD_PATH)
    return {"archives": ingested, **outcomes}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append-only Signal Ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="ingest canonical archived briefs")
    sub.add_parser("update", help="ingest, mature outcomes, and write stats")
    sub.add_parser("rebuild", help="atomically rebuild ledger and stats")
    legacy = sub.add_parser("import-legacy", help="copy legacy briefs into canonical archives")
    legacy.add_argument("--source", default="briefs")
    verify = sub.add_parser("verify", help="Measure all archived cohort members, including no-classification runs")
    verify.add_argument("--db", type=Path, default=DB_PATH)
    verify.add_argument("--policy", type=Path, required=True)
    verify.add_argument("--as-of", help="Timezone-qualified historical availability cutoff")
    verify.add_argument("--fetch", action="store_true", help="Allow bounded price acquisition; default is cached data only")
    verify.add_argument("--out", type=Path, help="New private .json or .md report path")
    init = sub.add_parser("monitor-init", help="Freeze a verifier study and its actual local creation time")
    init.add_argument("--workspace", type=Path, required=True)
    init.add_argument("--archives", type=Path, default=ARCHIVE_DIR)
    init.add_argument("--policy-id", required=True)
    init.add_argument("--move-threshold", type=float, required=True,
                      help="Absolute benchmark-relative move fraction; 0.05 means 5 percent")
    init.add_argument("--round-trip-cost-bps", type=float, required=True,
                      help="Explicit diagnostic friction assumption, not observed trading costs")
    init.add_argument("--horizons", type=int, nargs="+", default=[1, 5, 20])
    init.add_argument("--entry-latency-seconds", type=int, default=60)
    init.add_argument("--min-observation-days", type=int, default=28)
    monitor = sub.add_parser("monitor-run", help="Append one integrity-checked cohort review")
    monitor.add_argument("--workspace", type=Path, required=True)
    monitor.add_argument("--as-of")
    monitor.add_argument("--fetch", action="store_true")
    monitor.add_argument("--archives", type=Path,
                         help="Relocated archive directory; recorded in this review without changing the frozen policy")
    status = sub.add_parser("monitor-status", help="Check frozen policy and report receipts without acquisition")
    status.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command in ("verify", "monitor-init", "monitor-run", "monitor-status"):
        try:
            result = _verification_command(args)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as exc:
            parser.exit(2, f"Error: {exc}\n")
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    if args.command == "ingest":
        result = {"archives": ingest_archives()}
    elif args.command == "update":
        result = _update()
    elif args.command == "rebuild":
        result = _rebuild()
    else:
        result = {
            "imported": import_legacy(
                args.source, ARCHIVE_DIR, BRIEF_ARCHIVE_MIRROR_DIR
            )
        }
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
