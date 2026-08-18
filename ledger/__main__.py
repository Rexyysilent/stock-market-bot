"""Command-line interface for the Signal Ledger."""
from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path

from config import BRIEF_ARCHIVE_MIRROR_DIR

from .config import ARCHIVE_DIR, DB_PATH, STATS_JSON_PATH, STATS_MD_PATH
from .db import connect
from .ingest import import_legacy, ingest_archives
from .outcomes import mature_outcomes
from .stats import write_stats


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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

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
