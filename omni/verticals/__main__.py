"""Standalone offline command; shared OMNI CLI may register the same hooks."""
import argparse
import json
from pathlib import Path
import sys

from .cli import dispatch, register_commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    register_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    store = None
    try:
        if getattr(args, "persist", False):
            if args.db is None:
                raise ValueError("--db is required to persist local draft receipts")
            from ..store import EventStore
            store = EventStore(args.db)
        result = dispatch(args, store=store)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, OSError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)[:300]}), file=sys.stderr)
        raise SystemExit(1)
