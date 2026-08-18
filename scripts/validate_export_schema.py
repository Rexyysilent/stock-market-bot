"""Validate deterministic export fixtures against the public JSON Schema."""

import argparse
from collections import Counter, deque
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


HEADLINE_CONTRACT_VERSION = "2.6-headline-lanes-1"
RFC3986_URI_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~:/?#[]@!$&'()*+,;=%"
)
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "daily_brief.schema.json"
DEFAULT_FIXTURE = ROOT / "fixtures" / "exports" / "daily_brief.valid.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _semantic_error(message, path):
    return ValidationError(
        message,
        validator="headline_contract_semantics",
        path=deque(path),
    )


def _has_valid_rfc3986_characters_and_escapes(value):
    if any(character not in RFC3986_URI_CHARACTERS for character in value):
        return False
    for index, character in enumerate(value):
        if character != "%":
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in HEX_DIGITS
            or value[index + 2] not in HEX_DIGITS
        ):
            return False
    return True


def _is_valid_current_web_url(value):
    if not _has_valid_rfc3986_characters_and_escapes(value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        hostname = parsed.hostname
        port = parsed.port  # Access performs stdlib syntax/range validation.
    except (TypeError, ValueError):
        return False
    return bool(hostname) and (port is None or 0 <= port <= 65535)


def _is_valid_current_datetime(value):
    if len(value) < 20 or value[10:11] != "T":
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def _current_transport_errors(rows, path_prefix):
    errors = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for field in ("link", "canonical_url"):
            value = row.get(field)
            if isinstance(value, str) and not _is_valid_current_web_url(value):
                errors.append(_semantic_error(
                    f"{field} must be a valid absolute HTTP(S) URL",
                    [*path_prefix, index, field],
                ))
        for field in ("as_of", "observed_at"):
            value = row.get(field)
            if isinstance(value, str) and not _is_valid_current_datetime(value):
                errors.append(_semantic_error(
                    f"{field} must be a valid timezone-qualified ISO date-time",
                    [*path_prefix, index, field],
                ))
    return errors


def headline_contract_semantic_errors(document):
    """Validate cross-field invariants JSON Schema cannot express."""
    if not isinstance(document, dict):
        return []
    data_quality = document.get("data_quality")
    if not isinstance(data_quality, dict):
        return []
    if data_quality.get("headline_contract_version") != HEADLINE_CONTRACT_VERSION:
        return []

    sections = document.get("sections")
    if not isinstance(sections, dict):
        return []
    headlines = sections.get("headlines")
    headline_pool = data_quality.get("headline_pool")
    dropped = data_quality.get("headlines_dropped")
    if not isinstance(headlines, list):
        return []
    if not isinstance(headline_pool, dict) or not isinstance(dropped, list):
        return []

    errors = []
    errors.extend(_current_transport_errors(
        headlines,
        ["sections", "headlines"],
    ))
    errors.extend(_current_transport_errors(
        dropped,
        ["data_quality", "headlines_dropped"],
    ))
    selected_count = headline_pool.get("selected_count")
    accounted_count = headline_pool.get("accounted_candidate_count")
    fetched_count = headline_pool.get("fetched_count")

    if _is_integer(selected_count) and selected_count != len(headlines):
        errors.append(_semantic_error(
            "selected_count must equal len(sections.headlines): "
            f"{selected_count} != {len(headlines)}",
            ["data_quality", "headline_pool", "selected_count"],
        ))

    declared_lanes = headline_pool.get("selected_lane_counts")
    allowed_lanes = {"universe", "macro", "discovery"}
    actual_lanes = []
    lanes_well_formed = True
    for index, row in enumerate(headlines):
        if isinstance(row, dict):
            provider = row.get("provider")
            source = row.get("source")
            if (
                isinstance(provider, str)
                and isinstance(source, str)
                and source != provider
            ):
                errors.append(_semantic_error(
                    "selected headline source must equal provider compatibility alias",
                    ["sections", "headlines", index, "source"],
                ))
        if not isinstance(row, dict) or row.get("lane") not in allowed_lanes:
            lanes_well_formed = False
            break
        actual_lanes.append(row["lane"])
    if isinstance(declared_lanes, dict) and lanes_well_formed:
        actual_histogram = dict(Counter(actual_lanes))
        if declared_lanes != actual_histogram:
            errors.append(_semantic_error(
                "selected_lane_counts must exactly equal the selected headline "
                f"lane histogram: {declared_lanes!r} != {actual_histogram!r}",
                ["data_quality", "headline_pool", "selected_lane_counts"],
            ))

    if (
        _is_integer(accounted_count)
        and _is_integer(fetched_count)
        and accounted_count != fetched_count
    ):
        errors.append(_semantic_error(
            "accounted_candidate_count must equal fetched_count: "
            f"{accounted_count} != {fetched_count}",
            ["data_quality", "headline_pool", "accounted_candidate_count"],
        ))

    if (
        _is_integer(selected_count)
        and _is_integer(accounted_count)
        and selected_count + len(dropped) != accounted_count
    ):
        errors.append(_semantic_error(
            "selected_count + len(headlines_dropped) must equal "
            f"accounted_candidate_count: {selected_count} + {len(dropped)} "
            f"!= {accounted_count}",
            ["data_quality", "headline_pool", "accounted_candidate_count"],
        ))
    return errors


def validation_errors(document, schema):
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    errors = list(validator.iter_errors(document))
    errors.extend(headline_contract_semantic_errors(document))
    return sorted(
        errors,
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
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
