"""Replay permitted metadata from an exported brief, without fetching sources."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from evidence_intake import replay


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("document")
    args = parser.parse_args(argv)
    value = json.loads(Path(args.document).read_text(encoding="utf-8"))
    manifest = (value["data_quality"]["headline_pool"]["evidence_intake"]
                if "data_quality" in value else value)
    try:
        result = replay(manifest)
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["selection_matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
