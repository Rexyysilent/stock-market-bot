"""Local source lifecycle, bounded acquisition planning and migration commands."""
from __future__ import annotations

import json
from pathlib import Path

from .identities import digest

COMMANDS = {"receipt-import", "source-observation-import", "coverage-record", "correspondence-import",
            "route-plan", "repair-plan", "feed-contract-parse", "index-snapshot", "index-compare",
            "index-collect", "store-upgrade", "store-backup", "store-restore"}


def _read(path):
    data = Path(path).read_bytes()
    if len(data) > 5_000_000:
        raise ValueError("input size limit")
    return json.loads(data)


def register_commands(sub):
    for name in ("receipt-import", "source-observation-import", "coverage-record", "correspondence-import"):
        p = sub.add_parser(name); p.add_argument("input", type=Path)
        if name == "correspondence-import": p.add_argument("--synthetic", action="store_true")
    route = sub.add_parser("route-plan"); route.add_argument("route_id")
    route.add_argument("--year", type=int); route.add_argument("--quarter", type=int)
    repair = sub.add_parser("repair-plan"); repair.add_argument("--start-day", required=True); repair.add_argument("--end-day", required=True)
    feed = sub.add_parser("feed-contract-parse"); feed.add_argument("route_id"); feed.add_argument("input", type=Path)
    for name in ("index-snapshot", "index-collect"):
        p = sub.add_parser(name); p.add_argument("route_id"); p.add_argument("--source", default="sec-edgar")
        p.add_argument("--year", type=int); p.add_argument("--quarter", type=int)
        if name == "index-snapshot": p.add_argument("input", type=Path)
        else: p.add_argument("--execute", action="store_true"); p.add_argument("--user-agent")
    comparison = sub.add_parser("index-compare"); comparison.add_argument("previous_ref"); comparison.add_argument("current_ref")
    sub.add_parser("store-upgrade")
    backup = sub.add_parser("store-backup"); backup.add_argument("destination", type=Path)
    restore = sub.add_parser("store-restore"); restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path); restore.add_argument("--sha256", required=True)


def dispatch(args, store, policies, *, now):
    from .coverage import record_observation, record_coverage, repair_plan
    from .receipts import import_receipt, transaction
    from .route_contracts import route_plan
    from .index_snapshots import parse_snapshot, record_snapshot, compare_snapshots, collect_snapshot
    if args.command == "receipt-import":
        return import_receipt(store, _read(args.input))
    if args.command == "source-observation-import":
        record = _read(args.input)
        with transaction(store):
            receipt = record_observation(store, record, now=now, manage_transaction=False)
            # An imported acquisition remains an import. A new local source-cycle
            # links the exact inventory; it cannot fabricate a past successful poll.
            store.commit_cycle(cycle_id="import-cycle:"+receipt["receipt_id"], source_id=record["source_id"],
                observed_at=now, documents=[], events=[], decisions=[], coverage_observations=[record],
                complete=False, preserve_cursor=True, gaps=["import_not_live_source_cycle"], manage_transaction=False)
        return receipt
    if args.command == "coverage-record": return record_coverage(store, _read(args.input), now=now)
    if args.command == "correspondence-import":
        from .lifecycle import record_correspondence
        return record_correspondence(store, _read(args.input), now=now, allow_synthetic=args.synthetic)
    if args.command == "route-plan": return route_plan(args.route_id, year=args.year, quarter=args.quarter)
    if args.command == "repair-plan": return repair_plan(start_day=args.start_day, end_day=args.end_day)
    if args.command == "feed-contract-parse":
        from .feeds import parse_feed
        return parse_feed(args.input.read_bytes(), url=route_plan(args.route_id)["url"], route_id=args.route_id)
    if args.command == "index-snapshot":
        parsed = parse_snapshot(args.input.read_bytes(), route_id=args.route_id, observed_at=now,
                                year=args.year, quarter=args.quarter)
        return record_snapshot(store, parsed, source_id=args.source, policy_hash=digest(policies[args.source]), now=now,
                               evidence_kind="historical_audit")
    if args.command == "index-compare": return compare_snapshots(store, args.previous_ref, args.current_ref, now=now)
    if args.command == "index-collect":
        if not args.execute: return route_plan(args.route_id, year=args.year, quarter=args.quarter)
        from .acquisition import SourceClient
        from config import SEC_USER_AGENT
        client = SourceClient(store, policies[args.source], now=now, user_agent=args.user_agent or SEC_USER_AGENT)
        return collect_snapshot(store, client, route_id=args.route_id, now=now, year=args.year, quarter=args.quarter)
    if args.command == "store-upgrade": return store.migration
    if args.command == "store-backup":
        from .migrations import backup
        return backup(store, args.destination)
    if args.command == "store-restore":
        from .migrations import restore
        return restore(args.backup, args.destination, expected_sha256=args.sha256)
    raise ValueError("unknown source lifecycle command")
