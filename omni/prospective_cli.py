"""Local prospective-evidence commands; no acquisition, scheduler or activation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .identities import ROOT, load_registry
from .store import EventStore
from . import prospective as p
from . import timing, holdout, publications, intervals

COMMANDS = (
    "prospective-request", "prospective-freeze", "prospective-enumerate",
    "prospective-map", "prospective-closure-request", "prospective-close",
    "prospective-adjudication-request", "prospective-adjudicate",
    "prospective-run-request", "prospective-run", "prospective-coverage",
    "prospective-clock-request", "prospective-clock-import", "prospective-timing",
    "prospective-seal-request", "prospective-seal", "prospective-access",
    "prospective-inventory-access", "prospective-consumption-request",
    "prospective-consume", "prospective-untouched-status",
    "prospective-report", "prospective-migrate-legacy")
COMMANDS += ("prospective-publication-closure-request", "prospective-publication-close",
             "prospective-publication-adjudication-request", "prospective-publication-adjudicate")
COMMANDS += ("prospective-retention-request", "prospective-retention-import",
             "prospective-interval-request", "prospective-interval-import")
COMMANDS += ("prospective-record-evaluation", "prospective-verify-evaluation")
COMMANDS += ("prospective-publication-packet",)


def read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError("input exceeds 5 MB")
    return json.loads(raw)


def register_commands(subparsers):
    for name in COMMANDS:
        parser = subparsers.add_parser(name)
        if name in ("prospective-request", "prospective-freeze"):
            parser.add_argument("input", type=Path)
        elif name == "prospective-migrate-legacy":
            continue
        elif name == "prospective-verify-evaluation":
            parser.add_argument("receipt_id")
        else:
            parser.add_argument("protocol_id")
            if name in ("prospective-enumerate", "prospective-map",
                        "prospective-adjudication-request", "prospective-adjudicate",
                        "prospective-run-request", "prospective-run",
                        "prospective-timing", "prospective-access",
                        "prospective-inventory-access", "prospective-consume"):
                parser.add_argument("input", type=Path)
            elif name in ("prospective-publication-adjudication-request", "prospective-publication-adjudicate",
                          "prospective-publication-packet"):
                parser.add_argument("input", type=Path)
            elif name in ("prospective-retention-request", "prospective-retention-import",
                          "prospective-interval-request", "prospective-interval-import"):
                parser.add_argument("input", type=Path)
            elif name in ("prospective-clock-request", "prospective-clock-import"):
                parser.add_argument("kind", choices=("availability", "acquisition", "candidate"))
                parser.add_argument("input", type=Path)
            elif name in ("prospective-coverage", "prospective-seal"):
                parser.add_argument("receipt_id")
            elif name == "prospective-close":
                parser.add_argument("--review-receipt")
            elif name == "prospective-publication-close":
                parser.add_argument("review_receipt_id")
            elif name in ("prospective-report", "prospective-record-evaluation"):
                parser.add_argument("--untouched-receipt")


def dispatch(args, store, registry, policies, coverage, *, now):
    name = args.command
    data = read_json(args.input) if hasattr(args, "input") else None
    pid = getattr(args, "protocol_id", None)
    if name == "prospective-request":
        return p.protocol_request(registry, policies, list(coverage), data, now=now)
    if name == "prospective-freeze":
        return p.freeze_protocol(store, registry, policies, list(coverage), data, now=now)
    if name == "prospective-enumerate":
        return p.enumerate_sources(store, pid, registry, policies, data["coverage_receipt_ids"], now=now)
    if name == "prospective-map":
        return p.map_bundled_events(store, pid, registry, policies, data, now=now)
    if name == "prospective-closure-request":
        return p.closure_request(store, pid, registry, policies, now=now)
    if name == "prospective-close":
        return p.close_inventory(store, pid, registry, policies, args.review_receipt, now=now)
    if name == "prospective-adjudication-request":
        return p.adjudication_request(store, pid, registry, policies, data, now=now)
    if name == "prospective-adjudicate":
        return p.adjudicate(store, pid, registry, policies, data, now=now)
    if name == "prospective-run-request":
        return p.run_request(store, pid, registry, policies, data, now=now)
    if name == "prospective-run":
        return p.import_run(store, pid, registry, policies, data, now=now)
    if name == "prospective-coverage":
        return p.import_coverage(store, pid, registry, policies, args.receipt_id, now=now)
    if name == "prospective-clock-request":
        return timing.clock_proof_request(store, pid, registry, policies, args.kind, data, now=now)
    if name == "prospective-clock-import":
        return timing.import_clock_proof(store, pid, registry, policies, args.kind, data, now=now)
    if name == "prospective-timing":
        return timing.import_timing(store, pid, registry, policies, data, now=now)
    if name == "prospective-seal-request":
        return holdout.seal_request(store, pid, registry, policies, now=now)
    if name == "prospective-seal":
        return holdout.seal_partition(store, pid, registry, policies, args.receipt_id, now=now)
    if name == "prospective-access":
        return holdout.record_access(store, pid, registry, policies, data, now=now)
    if name == "prospective-inventory-access":
        return holdout.inventory_access(store, pid, registry, policies, data, now=now)
    if name == "prospective-consumption-request":
        return holdout.consumption_request(store, pid, registry, policies, now=now)
    if name == "prospective-consume":
        return holdout.consume_partition(store, pid, registry, policies, data, now=now)
    if name == "prospective-untouched-status":
        return holdout.untouched_state(store, pid, registry, policies, now=now)
    if name == "prospective-report":
        return p.evaluation_report(store, pid, registry, policies, now=now,
                                   untouched_receipt_id=args.untouched_receipt)
    if name == "prospective-record-evaluation":
        return p.record_evaluation(store, pid, registry, policies, now=now,
                                    untouched_receipt_id=args.untouched_receipt)
    if name == "prospective-verify-evaluation":
        return p.verify_evaluation_receipt(store, args.receipt_id, registry, policies, now=now)
    if name == "prospective-migrate-legacy":
        return p.migrate_legacy(store, now=now)
    if name == "prospective-publication-closure-request":
        return publications.closure_request(store, pid, registry, policies, now=now)
    if name == "prospective-publication-close":
        return publications.close_inventory(store, pid, registry, policies, args.review_receipt_id, now=now)
    if name == "prospective-publication-adjudication-request":
        return publications.adjudication_request(store, pid, registry, policies, data, now=now)
    if name == "prospective-publication-adjudicate":
        return publications.adjudicate(store, pid, registry, policies, data, now=now)
    if name == "prospective-publication-packet":
        return publications.inspect_packet(store, pid, registry, policies, data, now=now)
    if name == "prospective-retention-request":
        return intervals.retention_request(store, pid, registry, policies, data, now=now)
    if name == "prospective-retention-import":
        return intervals.import_retention(store, pid, registry, policies, data, now=now)
    if name == "prospective-interval-request":
        return intervals.interval_request(store, pid, registry, policies, data, now=now)
    if name == "prospective-interval-import":
        return intervals.import_interval(store, pid, registry, policies, data, now=now)
    raise ValueError("unsupported prospective command")


def main(argv=None, *, clock=None):
    """Standalone public command path; the parent OMNI CLI uses the same dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "history/omni-events.db")
    parser.add_argument("--sources", type=Path, default=ROOT / "registry/event-sources.json")
    parser.add_argument("--clinical-catalog",type=Path,help="explicit reviewed definition catalog; absent by default")
    parser.add_argument("--coverage", type=Path,
                        help="explicit coverage snapshot for isolated offline/operator review")
    sub = parser.add_subparsers(dest="command", required=True)
    register_commands(sub)
    args = parser.parse_args(argv)
    policies = read_json(args.sources)["policies"]
    registry = load_registry(clinical_catalog_path=args.clinical_catalog) if args.clinical_catalog else load_registry()
    if args.coverage:
        coverage = read_json(args.coverage)
    else:
        from config import EDITORIAL_COVERAGE_TICKERS
        coverage = list(EDITORIAL_COVERAGE_TICKERS)
    now = (clock() if clock else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    if args.command == "prospective-request":
        result = dispatch(args, None, registry, policies, coverage, now=now)
    else:
        store = EventStore(args.db)
        try:
            result = dispatch(args, store, registry, policies, coverage, now=now)
        finally:
            store.close()
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, OSError) as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__,
                          "detail": str(error)[:300]}), file=sys.stderr)
        raise SystemExit(1)
