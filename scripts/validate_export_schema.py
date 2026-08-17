"""Validate deterministic export fixtures against the public JSON Schema."""

import argparse
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "daily_brief.schema.json"
DEFAULT_FIXTURE = ROOT / "fixtures" / "exports" / "daily_brief.valid.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validation_errors(document, schema):
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    return sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )


def format_error(error):
    location = "$"
    for part in error.absolute_path:
        location += f"[{part}]" if isinstance(part, int) else f".{part}"
    return f"{location}: {error.message}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", nargs="*", default=[str(DEFAULT_FIXTURE)])
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    args = parser.parse_args(argv)

    schema = load_json(args.schema)
    failed = False
    for document_path in args.documents:
        errors = validation_errors(load_json(document_path), schema)
        if errors:
            failed = True
            print(f"INVALID {document_path}", file=sys.stderr)
            for error in errors:
                print(f"  {format_error(error)}", file=sys.stderr)
        else:
            print(f"VALID {document_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
