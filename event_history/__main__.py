"""python -m event_history <command>

import     index archives (default: archive/briefs/*.json) into the sidecar
anchor     one event per SEC accession, decided now (filing-anchor-1)
annotate   record that an archive's time label is earlier than its capture
view       print a strict knowledge view at a cutoff as JSON
status     counts per archive status, adapter and knowledge quality
"""
import argparse
import json
from pathlib import Path
import sys

from .db import DEFAULT_DB, connect
from .store import anchor_filings, annotate_archive, import_paths
from .views import knowledge_view, view_digest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m event_history", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("paths", nargs="*", type=Path)
    imp.add_argument("--archive-dir", type=Path, default=Path("archive") / "briefs")
    sub.add_parser("anchor")
    ann = sub.add_parser("annotate")
    ann.add_argument("archive_sha")
    ann.add_argument("--observed-not-before", required=True)
    ann.add_argument("--reason", required=True)
    view = sub.add_parser("view")
    view.add_argument("--cutoff", required=True)
    view.add_argument("--mode", choices=("as_operated", "retrospective"), default="as_operated")
    view.add_argument("--resolver", action="append", dest="resolvers")
    view.add_argument("--include-legacy", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    try:
        if args.command == "import":
            paths = args.paths or sorted(args.archive_dir.glob("*.json"))
            result = import_paths(conn, paths)
            result.pop("reports")
        elif args.command == "anchor":
            result = anchor_filings(conn)
        elif args.command == "annotate":
            result = {"annotation_id": annotate_archive(
                conn, args.archive_sha, observed_at_not_before=args.observed_not_before,
                reason=args.reason)}
        elif args.command == "view":
            result = knowledge_view(conn, args.cutoff, mode=args.mode,
                                    resolvers=args.resolvers, include_legacy=args.include_legacy)
            result["view_sha256"] = view_digest(result)
        else:
            result = {
                "archives": [dict(r) for r in conn.execute(
                    "SELECT status, adapter, knowledge_quality, count(*) AS n FROM archives "
                    "GROUP BY 1,2,3 ORDER BY 1,2,3")],
                "evidence_versions": conn.execute("SELECT count(*) FROM evidence_versions").fetchone()[0],
                "sightings": conn.execute("SELECT count(*) FROM sightings").fetchone()[0],
                "events": conn.execute("SELECT count(*) FROM events").fetchone()[0],
            }
    except ValueError as error:
        print(f"event_history: {error}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
