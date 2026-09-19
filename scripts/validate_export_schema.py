"""Validate deterministic export fixtures against the public JSON Schema."""

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


UNIVERSE_CONTRACT_VERSION = "2.8-tiered-universe-1"
PROFILE_UNIVERSE_CONTRACT_VERSION = "2.9-profile-universe-1"
LEGACY_UNIVERSE_CONTRACT_VERSION = "2.7-tiered-universe-1"
HEADLINE_CONTRACT_VERSION = "2.7-headline-lanes-1"
FOCUS_CONTRACT_VERSION = "2.7-editorial-focus-1"
LEGACY_HEADLINE_CONTRACT_VERSION = "2.6-headline-lanes-1"
LEGACY_FOCUS_CONTRACT_VERSION = "2.6-editorial-focus-1"
SIGNAL_ELIGIBILITY_AUDIT_VERSION = "2.7-signal-eligibility-1"
FOCUS_CONFIDENCE_BY_SOURCE_CLASS = {
    "official": 3,
    "official_central_bank": 3,
    "official_exchange": 3,
    "official_regulator": 3,
    "aggregator": 2,
    "press_release": 2,
    "api_news_discovery": 1,
    "global_discovery": 1,
}
FOCUS_EVIDENCE_SCORE_FIELDS = (
    "impact",
    "confidence",
    "novelty",
    "source_authority",
    "audience_relevance",
    "timeliness",
    "independent_corroboration",
)
RFC3986_URI_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~:/?#[]@!$&'()*+,;=%"
)
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
ROOT = Path(__file__).resolve().parents[1]
# Direct script execution must resolve the same policy modules as -m execution.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SCHEMA = ROOT / "schemas" / "daily_brief.schema.json"
LEGACY_SCHEMA = ROOT / "schemas" / "daily_brief.2.6.schema.json"
LEGACY_TIERED_SCHEMA = ROOT / "schemas" / "daily_brief.2.7.schema.json"
DEFAULT_FIXTURE = ROOT / "fixtures" / "exports" / "daily_brief.valid.json"
SCHEMA_PATHS = {
    "2.6": LEGACY_SCHEMA,
    "2.7": LEGACY_TIERED_SCHEMA,
    "2.8": DEFAULT_SCHEMA,
    "2.9": ROOT / "schemas" / "daily_brief.2.9.schema.json",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_focus_component(value):
    if not _is_integer(value):
        return None
    return max(0, min(3, value))


def _focus_confidence(source_class):
    normalized = str(source_class or "").strip()
    return FOCUS_CONFIDENCE_BY_SOURCE_CLASS.get(normalized, 1)


def _focus_datetime(value):
    if not (isinstance(value, str) and _is_valid_current_datetime(value)):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return (
        datetime.fromisoformat(normalized)
        .astimezone(timezone.utc)
        .replace(microsecond=0)
    )


def _focus_timeliness(as_of, generated_at):
    evidence_dt = _focus_datetime(as_of)
    generated_dt = _focus_datetime(generated_at)
    if evidence_dt is None or generated_dt is None:
        return None
    age_seconds = (generated_dt - evidence_dt).total_seconds()
    if age_seconds < -3600:
        return 0
    age_days = max(0.0, age_seconds / 86400.0)
    if age_days <= 1:
        return 3
    if age_days <= 2:
        return 2
    if age_days <= 3:
        return 1
    return 0


def _expected_evidence_grade(components):
    corroboration = components.get("independent_corroboration")
    authority = components.get("source_authority")
    if not (_is_integer(corroboration) and _is_integer(authority)):
        return None
    if corroboration and authority >= 2:
        return "multiple_selected_sources"
    if authority >= 3:
        return "official_source"
    if authority >= 2:
        return "single_source_secondary"
    return "limited_source"


def _semantic_error(message, path):
    return ValidationError(
        message,
        validator="headline_contract_semantics",
        path=deque(path),
    )


def _focus_semantic_error(message, path):
    return ValidationError(
        message,
        validator="editorial_focus_contract_semantics",
        path=deque(path),
    )

def _tier_semantic_error(message, path):
    return ValidationError(
        message,
        validator="tiered_universe_contract_semantics",
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
    expected_contract = (
        HEADLINE_CONTRACT_VERSION if document.get("schema_version") in ("2.7", "2.8", "2.9")
        else LEGACY_HEADLINE_CONTRACT_VERSION
    )
    if data_quality.get("headline_contract_version") != expected_contract:
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


def _is_selected_duplicate_occurrence(row, selected_by_source_id):
    """A discarded copy is not a rejected story; require exact provenance.

    A shared ID alone is insufficient. Rejected-only rows, stale copies,
    malformed identities and conflicting occurrences still veto focus evidence.
    """
    source_id = row.get("source_record_id")
    selected = selected_by_source_id.get(source_id)
    if (
        not isinstance(selected, dict)
        or row.get("reason") != "duplicate"
        or not source_id
        or row.get("kept_source_record_id") != source_id
    ):
        return False
    return all(
        isinstance(row.get(field), str)
        and bool(row[field].strip())
        and row[field] == selected.get(field)
        for field in (
            "title", "canonical_url", "provider", "publisher",
            "source_class", "source_time_kind", "as_of",
        )
    )


def editorial_focus_contract_semantic_errors(document):
    """Validate the additive editorial-focus contract and evidence links."""
    if not isinstance(document, dict):
        return []
    data_quality = document.get("data_quality")
    if not isinstance(data_quality, dict):
        return []
    expected_contract = (
        FOCUS_CONTRACT_VERSION if document.get("schema_version") in ("2.7", "2.8", "2.9")
        else LEGACY_FOCUS_CONTRACT_VERSION
    )
    if data_quality.get("focus_contract_version") != expected_contract:
        return []

    sections = document.get("sections")
    universe = document.get("universe")
    if not isinstance(sections, dict) or not isinstance(universe, dict):
        return []
    focus = sections.get("deep_dive")
    if not isinstance(focus, dict):
        return []

    errors = []
    status = focus.get("status")
    ticker = focus.get("ticker")
    selection_mode = focus.get("selection_mode")
    requested_ticker = focus.get("requested_ticker")
    configured_ticker = universe.get("configured_focus_ticker")
    universe_focus = universe.get("focus_ticker")
    universe_mode = universe.get("focus_selection_mode")
    universe_tickers = universe.get("tickers")

    if requested_ticker != configured_ticker:
        errors.append(_focus_semantic_error(
            "requested_ticker must equal universe.configured_focus_ticker",
            ["sections", "deep_dive", "requested_ticker"],
        ))

    if status == "no_focus":
        if universe_focus is not None:
            errors.append(_focus_semantic_error(
                "universe.focus_ticker must be null when status=no_focus",
                ["universe", "focus_ticker"],
            ))
        if universe_mode != "none":
            errors.append(_focus_semantic_error(
                "universe.focus_selection_mode must be none when status=no_focus",
                ["universe", "focus_selection_mode"],
            ))
        rejection = focus.get("pin_rejection_reason")
        if requested_ticker is None and rejection is not None:
            errors.append(_focus_semantic_error(
                "an unrequested no-focus result cannot carry a pin rejection",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))
        if requested_ticker is not None and rejection is None:
            errors.append(_focus_semantic_error(
                "a rejected configured focus must expose its rejection reason",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))
        return errors

    if status != "selected":
        return errors

    if ticker != universe_focus:
        errors.append(_focus_semantic_error(
            "selected ticker must equal universe.focus_ticker",
            ["universe", "focus_ticker"],
        ))
    if selection_mode != universe_mode:
        errors.append(_focus_semantic_error(
            "selection_mode must equal universe.focus_selection_mode",
            ["universe", "focus_selection_mode"],
        ))
    if isinstance(universe_tickers, list) and ticker not in universe_tickers:
        errors.append(_focus_semantic_error(
            "selected ticker must belong to universe.tickers",
            ["sections", "deep_dive", "ticker"],
        ))
    if focus.get("entity_ids") != [f"ticker:{ticker}"]:
        errors.append(_focus_semantic_error(
            "entity_ids must contain exactly the selected ticker entity",
            ["sections", "deep_dive", "entity_ids"],
        ))

    rejection = focus.get("pin_rejection_reason")
    reason = focus.get("reason")
    if selection_mode == "configured":
        if requested_ticker != ticker or rejection is not None:
            errors.append(_focus_semantic_error(
                "configured selection requires a matching supported request",
                ["sections", "deep_dive", "requested_ticker"],
            ))
        if reason != "configured_focus_supported":
            errors.append(_focus_semantic_error(
                "configured selection must use reason=configured_focus_supported",
                ["sections", "deep_dive", "reason"],
            ))
    elif selection_mode == "dynamic":
        if reason != "highest_ranked_supported_candidate":
            errors.append(_focus_semantic_error(
                "dynamic selection must use the deterministic-ranking reason",
                ["sections", "deep_dive", "reason"],
            ))
        if requested_ticker is None and rejection is not None:
            errors.append(_focus_semantic_error(
                "dynamic selection without a configured request cannot carry a rejection",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))
        if requested_ticker is not None and rejection is None:
            errors.append(_focus_semantic_error(
                "dynamic fallback from a configured request must expose its rejection",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))

    headlines = sections.get("headlines")
    dropped = data_quality.get("headlines_dropped")
    selected_by_source_id = {}
    if isinstance(headlines, list):
        selected_by_source_id = {
            row.get("source_record_id"): row
            for row in headlines
            if isinstance(row, dict) and isinstance(row.get("source_record_id"), str)
        }
    dropped_source_ids = {
        row.get("source_record_id")
        for row in (dropped if isinstance(dropped, list) else [])
        if isinstance(row, dict) and isinstance(row.get("source_record_id"), str)
        and not _is_selected_duplicate_occurrence(row, selected_by_source_id)
    }

    generated_at = document.get("generated_at")
    generated_dt = None
    if isinstance(generated_at, str) and _is_valid_current_datetime(generated_at):
        normalized = generated_at[:-1] + "+00:00" if generated_at.endswith("Z") else generated_at
        generated_dt = datetime.fromisoformat(normalized)

    evidence = focus.get("evidence")
    evidence_ids = []
    primary_matches = []
    if isinstance(evidence, list):
        for index, row in enumerate(evidence):
            if not isinstance(row, dict):
                continue
            evidence_id = row.get("evidence_id")
            if isinstance(evidence_id, str):
                evidence_ids.append(evidence_id)
            if (
                row.get("title") == focus.get("headline")
                and row.get("as_of") == focus.get("as_of")
            ):
                primary_matches.append(row)
            if row.get("ticker") != ticker:
                errors.append(_focus_semantic_error(
                    "every evidence row must map to the selected ticker",
                    ["sections", "deep_dive", "evidence", index, "ticker"],
                ))
            for field in ("link", "canonical_url"):
                value = row.get(field)
                if isinstance(value, str) and not _is_valid_current_web_url(value):
                    errors.append(_focus_semantic_error(
                        f"{field} must be a valid absolute HTTP(S) URL",
                        ["sections", "deep_dive", "evidence", index, field],
                    ))
            for field in ("as_of", "observed_at"):
                value = row.get(field)
                if isinstance(value, str) and not _is_valid_current_datetime(value):
                    errors.append(_focus_semantic_error(
                        f"{field} must be a valid timezone-qualified ISO date-time",
                        ["sections", "deep_dive", "evidence", index, field],
                    ))

            as_of = row.get("as_of")
            if (
                generated_dt is not None
                and isinstance(as_of, str)
                and _is_valid_current_datetime(as_of)
            ):
                normalized = as_of[:-1] + "+00:00" if as_of.endswith("Z") else as_of
                evidence_dt = datetime.fromisoformat(normalized)
                age_seconds = (generated_dt - evidence_dt).total_seconds()
                if age_seconds > 3 * 86400 or age_seconds < -3600:
                    errors.append(_focus_semantic_error(
                        "focus evidence must be within the selector's trailing three-day window",
                        ["sections", "deep_dive", "evidence", index, "as_of"],
                    ))

            source_record_id = row.get("source_record_id")
            selected_row = selected_by_source_id.get(source_record_id)
            if selected_row is None:
                errors.append(_focus_semantic_error(
                    "focus evidence source_record_id must resolve to sections.headlines",
                    ["sections", "deep_dive", "evidence", index, "source_record_id"],
                ))
            else:
                if ticker not in (selected_row.get("universe_tickers") or []):
                    errors.append(_focus_semantic_error(
                        "focus evidence must resolve to a selected headline mapped to the focus ticker",
                        ["sections", "deep_dive", "evidence", index, "source_record_id"],
                    ))
                for field in (
                    "title", "link", "canonical_url", "provider", "publisher",
                    "publisher_domain", "source_class", "as_of", "observed_at",
                    "source_time_kind",
                ):
                    if row.get(field) != selected_row.get(field):
                        errors.append(_focus_semantic_error(
                            f"focus evidence {field} must match its selected headline",
                            ["sections", "deep_dive", "evidence", index, field],
                        ))
                for field in ("duplicate_providers", "duplicate_publishers"):
                    if field in row and row.get(field) != selected_row.get(field):
                        errors.append(_focus_semantic_error(
                            f"focus evidence {field} must match its selected headline",
                            ["sections", "deep_dive", "evidence", index, field],
                        ))
                primary_publisher = str(row.get("publisher") or "").strip()
                independent_publishers = {
                    str(value).strip()
                    for value in (row.get("duplicate_publishers") or [])
                    if str(value).strip()
                }
                independent_publishers.discard(primary_publisher)
                expected_corroboration = min(3, len(independent_publishers))
                if row.get("independent_corroboration") != expected_corroboration:
                    errors.append(_focus_semantic_error(
                        "independent_corroboration must equal distinct duplicate publisher lineage",
                        [
                            "sections", "deep_dive", "evidence", index,
                            "independent_corroboration",
                        ],
                    ))

                selected_components = selected_row.get("score_components")
                if not isinstance(selected_components, dict):
                    selected_components = {}
                expected_scores = {
                    "impact": _bounded_focus_component(
                        selected_components.get("impact")
                    ),
                    "confidence": _focus_confidence(
                        selected_row.get("source_class")
                    ),
                    "novelty": _bounded_focus_component(
                        selected_components.get("novelty")
                    ),
                    "source_authority": _bounded_focus_component(
                        selected_components.get("authority")
                    ),
                    "audience_relevance": _bounded_focus_component(
                        selected_components.get("issuer_relevance")
                    ),
                    "timeliness": _focus_timeliness(
                        row.get("as_of"), generated_at
                    ),
                }
                for field, expected in expected_scores.items():
                    if row.get(field) != expected:
                        errors.append(_focus_semantic_error(
                            f"focus evidence {field} must equal its "
                            "deterministic selected-headline projection",
                            [
                                "sections", "deep_dive", "evidence", index,
                                field,
                            ],
                        ))
            if source_record_id in dropped_source_ids:
                errors.append(_focus_semantic_error(
                    "a dropped headline cannot supply editorial-focus evidence",
                    ["sections", "deep_dive", "evidence", index, "source_record_id"],
                ))

    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append(_focus_semantic_error(
            "evidence_id values must be unique",
            ["sections", "deep_dive", "evidence"],
        ))
    evidence_id_set = set(evidence_ids)
    for claim_name in ("what_changed", "why_it_matters"):
        claim = focus.get(claim_name)
        if not isinstance(claim, dict):
            continue
        for reference in claim.get("evidence_ids") or []:
            if reference not in evidence_id_set:
                errors.append(_focus_semantic_error(
                    f"{claim_name} evidence ID does not resolve: {reference}",
                    ["sections", "deep_dive", claim_name, "evidence_ids"],
                ))

    if len(primary_matches) != 1:
        errors.append(_focus_semantic_error(
            "headline and as_of must identify exactly one included evidence row",
            ["sections", "deep_dive", "headline"],
        ))
    else:
        primary = primary_matches[0]
        expected_focus_components = {
            field: primary.get(field)
            for field in FOCUS_EVIDENCE_SCORE_FIELDS
        }
        contradiction_count = primary.get("contradiction_count")
        expected_focus_components[
            "unresolved_contradiction_penalty"
        ] = (
            min(3, contradiction_count)
            if _is_integer(contradiction_count)
            else None
        )
        if focus.get("score_components") != expected_focus_components:
            errors.append(_focus_semantic_error(
                "focus score_components must exactly equal the primary "
                "evidence projection",
                ["sections", "deep_dive", "score_components"],
            ))

        expected_grade = _expected_evidence_grade(
            expected_focus_components
        )
        if focus.get("evidence_grade") != expected_grade:
            errors.append(_focus_semantic_error(
                "evidence_grade must match primary-evidence authority and "
                "independent corroboration",
                ["sections", "deep_dive", "evidence_grade"],
            ))
    return errors

def _universe_fingerprint(tickers):
    return hashlib.sha256(",".join(tickers).encode("utf-8")).hexdigest()[:8]


def _expected_coverage_metadata(tickers, instrumented, editorial_only):
    mapped = {str(ticker).upper() for ticker in (tickers or [])}
    has_instrumented = bool(mapped & instrumented)
    has_editorial = bool(mapped & editorial_only)
    if has_instrumented and has_editorial:
        return "mixed", False, "mixed_coverage_requires_ticker_filter"
    if has_editorial:
        return "editorial_only", False, "editorial_only_coverage"
    if has_instrumented:
        return "instrumented", True, "instrumented_universe"
    return None, False, "no_mapped_ticker"


def _iter_explicit_tickers(value, path):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = [*path, key]
            if key == "ticker" and isinstance(child, str):
                yield child_path, child.upper()
            elif key == "tickers" and isinstance(child, list):
                for index, ticker in enumerate(child):
                    if isinstance(ticker, str):
                        yield [*child_path, index], ticker.upper()
            else:
                yield from _iter_explicit_tickers(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_explicit_tickers(child, [*path, index])


def tiered_universe_contract_semantic_errors(document):
    """Validate each deployed cohort under its own immutable contract."""
    if not isinstance(document, dict) or document.get("schema_version") not in ("2.7", "2.8", "2.9"):
        return []
    profiled = document.get("schema_version") == "2.9"
    current = document.get("schema_version") in ("2.8", "2.9")
    contract = (PROFILE_UNIVERSE_CONTRACT_VERSION if profiled else
                UNIVERSE_CONTRACT_VERSION if current else LEGACY_UNIVERSE_CONTRACT_VERSION)
    data_quality = document.get("data_quality")
    if not isinstance(data_quality, dict) or data_quality.get(
            "universe_contract_version") != contract:
        return []
    universe = document.get("universe")
    if not isinstance(universe, dict):
        return []
    coverage = universe.get("tickers")
    instrumented = universe.get("instrumented_tickers")
    editorial = universe.get("editorial_only_tickers")
    focus_eligible = universe.get("focus_eligible_tickers")
    if not all(isinstance(value, list) for value in (
            coverage, instrumented, editorial, focus_eligible)):
        return []

    errors = []
    expected_counts = ((coverage, 42 if current else 41, "tickers"),
                       (instrumented, 29, "instrumented_tickers"),
                       (editorial, 13 if current else 12, "editorial_only_tickers"))
    if profiled:
        from universe_profile import validate_profile, profile_plan, profile_policy_manifest, fingerprint
        try:
            profile = validate_profile(universe.get("profile"))
        except (ValueError, TypeError, KeyError) as exc:
            return [_tier_semantic_error(f"Invalid universe profile: {exc}", ["universe", "profile"])]
        expected = profile_plan(profile)["active_tickers"]
        expected_counts = ((coverage, len(expected), "tickers"),
                           (instrumented, len(expected), "instrumented_tickers"),
                           (editorial, 0, "editorial_only_tickers"))
        if instrumented != expected or universe.get("editorial_coverage_mode") != "off":
            errors.append(_tier_semantic_error("Profile membership must match its instrumented cohort with editorial mode off", ["universe"]))
        if universe.get("coverage_policy") != profile_policy_manifest(profile):
            errors.append(_tier_semantic_error("Profile coverage policy must match its configuration", ["universe", "coverage_policy"]))
        if universe.get("name") != f"{profile['name']}:{fingerprint(profile)}":
            errors.append(_tier_semantic_error("Profile identity must include the full configuration fingerprint", ["universe", "name"]))
        if universe.get("configured_focus_ticker") != profile["focus_ticker"]:
            errors.append(_tier_semantic_error("Configured focus must match the profile", ["universe", "configured_focus_ticker"]))
    elif current:
        from coverage_policy import coverage_policy_manifest
        from config import SIGNAL_ELIGIBLE_TICKERS, EDITORIAL_ONLY_TICKERS
        if instrumented != list(SIGNAL_ELIGIBLE_TICKERS) or editorial != list(EDITORIAL_ONLY_TICKERS):
            errors.append(_tier_semantic_error(
                "ordered instrumented/editorial cohorts must match the versioned policy",
                ["universe", "tickers"],
            ))
        if universe.get("coverage_policy") != coverage_policy_manifest(universe.get("editorial_coverage_mode")):
            errors.append(_tier_semantic_error(
                "coverage_policy must match the versioned capabilities, purposes and acquisition targets",
                ["universe", "coverage_policy"],
            ))
    for values, expected, field in expected_counts:
        if len(values) != expected:
            errors.append(_tier_semantic_error(
                f"universe.{field} must contain exactly {expected} tickers",
                ["universe", field],
            ))
    if coverage != instrumented + editorial:
        errors.append(_tier_semantic_error(
            "universe.tickers must equal instrumented_tickers followed by editorial_only_tickers",
            ["universe", "tickers"],
        ))
    if len(coverage) != len(set(coverage)):
        errors.append(_tier_semantic_error(
            "universe.tickers must be unique", ["universe", "tickers"]
        ))
    if set(instrumented) & set(editorial):
        errors.append(_tier_semantic_error(
            "instrumented_tickers and editorial_only_tickers must be disjoint",
            ["universe", "editorial_only_tickers"],
        ))
    if universe.get("version") != _universe_fingerprint(coverage):
        errors.append(_tier_semantic_error(
            "universe.version must fingerprint the ordered coverage universe",
            ["universe", "version"],
        ))
    if universe.get("instrumented_version") != _universe_fingerprint(instrumented):
        errors.append(_tier_semantic_error(
            "universe.instrumented_version must fingerprint the ordered instrumented universe",
            ["universe", "instrumented_version"],
        ))

    mode = universe.get("editorial_coverage_mode")
    expected_focus_eligible = coverage if mode == "active" else instrumented
    if focus_eligible != expected_focus_eligible:
        errors.append(_tier_semantic_error(
            "focus_eligible_tickers must equal coverage tickers only in active mode; otherwise instrumented tickers",
            ["universe", "focus_eligible_tickers"],
        ))
    configured = universe.get("configured_focus_ticker")

    instrumented_set = set(instrumented)
    editorial_set = set(editorial)
    coverage_set = set(coverage)
    sections = document.get("sections")
    sections = sections if isinstance(sections, dict) else {}
    focus = sections.get("deep_dive")
    focus = focus if isinstance(focus, dict) else {}
    focus_ticker = universe.get("focus_ticker")
    if focus_ticker is None:
        expected_focus = (None, None, "no_focus")
    else:
        expected_focus = _expected_coverage_metadata(
            [focus_ticker], instrumented_set, editorial_set
        )
    universe_focus_values = (
        universe.get("focus_coverage_tier"),
        universe.get("focus_signal_eligible"),
        universe.get("focus_signal_eligibility_reason"),
    )
    if universe_focus_values != expected_focus:
        errors.append(_tier_semantic_error(
            "universe focus tier/eligibility metadata must match focus_ticker",
            ["universe", "focus_coverage_tier"],
        ))
    focus_values = (
        focus.get("coverage_tier"), focus.get("signal_eligible"),
        focus.get("signal_eligibility_reason"),
    )
    if focus_values != expected_focus:
        errors.append(_tier_semantic_error(
            "deep_dive tier/eligibility metadata must match universe.focus_ticker",
            ["sections", "deep_dive", "coverage_tier"],
        ))
    if focus_ticker is not None and focus_ticker not in focus_eligible:
        errors.append(_tier_semantic_error(
            "selected focus_ticker must belong to focus_eligible_tickers",
            ["universe", "focus_ticker"],
        ))
    rejection = focus.get("pin_rejection_reason")
    if configured is not None:
        if configured not in coverage_set and rejection != "unmapped_or_outside_universe":
            errors.append(_tier_semantic_error(
                "an outside-coverage configured focus must expose unmapped_or_outside_universe",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))
        if (configured in editorial_set and mode != "active"
                and rejection != "editorial_coverage_not_active"):
            errors.append(_tier_semantic_error(
                "an editorial-only configured focus requires active mode",
                ["sections", "deep_dive", "pin_rejection_reason"],
            ))

    data_quality_headlines = data_quality.get("headlines_dropped")
    terminal_rows_by_id = {}
    headline_sets = (
        (sections.get("headlines"), ["sections", "headlines"]),
        (data_quality_headlines, ["data_quality", "headlines_dropped"]),
    )
    for rows, prefix in headline_sets:
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            source_id = row.get("source_record_id")
            if isinstance(source_id, str) and source_id:
                terminal_rows_by_id.setdefault(source_id, []).append(
                    (prefix, row))
            mapped = row.get("universe_tickers")
            if isinstance(mapped, list):
                outside = set(mapped) - coverage_set
                if outside:
                    errors.append(_tier_semantic_error(
                        "headline universe_tickers must be a subset of universe.tickers",
                        [*prefix, index, "universe_tickers"],
                    ))
                expected = _expected_coverage_metadata(
                    mapped, instrumented_set, editorial_set
                )
                if (prefix == ["sections", "headlines"]
                        and mode != "active"
                        and set(mapped) & editorial_set):
                    errors.append(_tier_semantic_error(
                        "selected headlines cannot expose editorial-only mappings outside active mode",
                        [*prefix, index, "universe_tickers"],
                    ))
                actual = (row.get("coverage_tier"), row.get("signal_eligible"),
                          row.get("signal_eligibility_reason"))
                if actual != expected:
                    errors.append(_tier_semantic_error(
                        "headline tier/eligibility metadata must match universe_tickers",
                        [*prefix, index, "coverage_tier"],
                    ))

    pool = data_quality.get("headline_pool")
    shadow = pool.get("editorial_shadow") if isinstance(pool, dict) else None
    if isinstance(shadow, dict):
        if shadow.get("mode") != mode:
            errors.append(_tier_semantic_error(
                "editorial_shadow.mode must equal universe.editorial_coverage_mode",
                ["data_quality", "headline_pool", "editorial_shadow", "mode"],
            ))
        candidate_count = shadow.get("candidate_count")
        fresh_count = shadow.get("fresh_candidate_count")
        records = shadow.get("records")
        if (_is_integer(candidate_count) and _is_integer(fresh_count)
                and fresh_count > candidate_count):
            errors.append(_tier_semantic_error(
                "fresh_candidate_count cannot exceed candidate_count",
                ["data_quality", "headline_pool", "editorial_shadow", "fresh_candidate_count"],
            ))
        if isinstance(records, list):
            if _is_integer(fresh_count) and len(records) > fresh_count:
                errors.append(_tier_semantic_error(
                    "shadow record count cannot exceed fresh_candidate_count",
                    ["data_quality", "headline_pool", "editorial_shadow", "records"],
                ))
            if mode != "shadow" and records:
                errors.append(_tier_semantic_error(
                    "shadow records must be empty outside shadow mode",
                    ["data_quality", "headline_pool", "editorial_shadow", "records"],
                ))
            if (mode != "shadow" and (
                    candidate_count != 0 or fresh_count != 0)):
                errors.append(_tier_semantic_error(
                    "shadow counts must be zero outside shadow mode",
                    ["data_quality", "headline_pool", "editorial_shadow", "candidate_count"],
                ))
            source_ids = []
            for index, row in enumerate(records):
                if not isinstance(row, dict):
                    continue
                source_ids.append(row.get("source_record_id"))
                tickers = row.get("tickers")
                if (not isinstance(tickers, list) or not tickers
                        or set(tickers) - editorial_set):
                    errors.append(_tier_semantic_error(
                        "all shadow record tickers must belong to editorial_only_tickers",
                        ["data_quality", "headline_pool", "editorial_shadow", "records", index, "tickers"],
                    ))
                elif tickers != sorted(set(tickers)):
                    errors.append(_tier_semantic_error(
                        "shadow record tickers must be sorted and unique",
                        ["data_quality", "headline_pool", "editorial_shadow", "records", index, "tickers"],
                    ))
                terminal_matches = terminal_rows_by_id.get(
                    row.get("source_record_id"), [])
                if len(terminal_matches) != 1:
                    errors.append(_tier_semantic_error(
                        "each shadow record must resolve to exactly one selected or dropped headline",
                        ["data_quality", "headline_pool", "editorial_shadow", "records", index, "source_record_id"],
                    ))
                    continue
                terminal_prefix, terminal = terminal_matches[0]
                for field in ("title", "link", "as_of"):
                    if row.get(field) != terminal.get(field):
                        errors.append(_tier_semantic_error(
                            f"shadow record {field} must match its terminal headline",
                            ["data_quality", "headline_pool", "editorial_shadow", "records", index, field],
                        ))
                terminal_mapped = terminal.get("universe_tickers")
                terminal_mapped = (
                    set(terminal_mapped)
                    if isinstance(terminal_mapped, list)
                    else set()
                )
                disposition = row.get("disposition")
                if disposition == "suppressed":
                    valid_suppressed = (
                        terminal_prefix == ["data_quality", "headlines_dropped"]
                        and terminal.get("reason") == "editorial_shadow_suppressed"
                        and bool(terminal_mapped)
                        and terminal_mapped <= editorial_set
                        and set(tickers or []) <= terminal_mapped
                    )
                    if not valid_suppressed:
                        errors.append(_tier_semantic_error(
                            "a suppressed shadow record must resolve to its editorial-only dropped headline",
                            ["data_quality", "headline_pool", "editorial_shadow", "records", index, "disposition"],
                        ))
                elif disposition == "legacy_projection":
                    if not current or terminal_mapped or row.get("reason") != "editorial_mapping_shadowed":
                        errors.append(_tier_semantic_error(
                            "a legacy_projection must preserve an unmapped legacy terminal without editorial promotion",
                            ["data_quality", "headline_pool", "editorial_shadow", "records", index, "disposition"],
                        ))
                elif disposition == "core_projection":
                    if not terminal_mapped or not terminal_mapped <= instrumented_set:
                        errors.append(_tier_semantic_error(
                            "a core_projection shadow record must resolve to a core-only terminal headline",
                            ["data_quality", "headline_pool", "editorial_shadow", "records", index, "disposition"],
                        ))
            if len(source_ids) != len(set(source_ids)):
                errors.append(_tier_semantic_error(
                    "shadow source_record_id values must be unique",
                    ["data_quality", "headline_pool", "editorial_shadow", "records"],
                ))
    return errors


SIGNAL_BEARING_SECTIONS = (
    "confluence", "prices", "technicals", "sec_filings",
    "clinical_catalysts", "fda_catalysts", "registry_changes",
    "insider_clusters", "options_flow", "earnings_calendar",
    "ceo_ca_signals", "social_attention", "social_alerts",
    "cash_runway_alerts",
    "baseline_alerts",
)


def _signal_rows_for_audit(section_name, value, eligible_tickers=None):
    if not isinstance(value, list):
        return value
    if section_name == "social_attention":
        return [row for row in value if isinstance(row, dict) and (
            row.get("universe_member") is True
            or row.get("signal_eligible") is True
        )]
    if section_name == "clinical_catalysts":
        return [row for row in value if isinstance(row, dict) and row.get("fragile_alert")]
    if section_name == "fda_catalysts":
        return [row for row in value if isinstance(row, dict) and (
            row.get("alert") or row.get("fragile_alert")
        )]
    if section_name == "ceo_ca_signals":
        eligible = eligible_tickers or set()
        return [row for row in value if isinstance(row, dict) and (
            str(row.get("ticker") or "").upper() in eligible
            or row.get("signal_eligible") is True
        )]
    return value


def signal_eligibility_semantic_errors(document):
    if not isinstance(document, dict) or document.get("schema_version") not in ("2.7", "2.8", "2.9"):
        return []
    universe = document.get("universe")
    sections = document.get("sections")
    if not isinstance(universe, dict) or not isinstance(sections, dict):
        return []
    eligible = set(universe.get("instrumented_tickers") or [])
    errors = []
    for section_name in SIGNAL_BEARING_SECTIONS:
        value = sections.get(section_name)
        if value is None:
            continue
        if section_name == "options_flow" and isinstance(value, dict):
            for ticker in value:
                normalized = str(ticker).upper()
                if normalized not in eligible:
                    errors.append(ValidationError(
                        "options_flow key must belong to instrumented_tickers",
                        validator="signal_eligibility_semantics",
                        path=deque(["sections", "options_flow", ticker]),
                    ))
        audited_value = _signal_rows_for_audit(section_name, value, eligible)
        for path, ticker in _iter_explicit_tickers(
                audited_value, ["sections", section_name]):
            if ticker not in eligible:
                errors.append(ValidationError(
                    "structured signal/measurement ticker must belong to instrumented_tickers",
                    validator="signal_eligibility_semantics",
                    path=deque(path),
                ))
    return errors


@lru_cache(maxsize=None)
def _schema_for_version(version):
    path = SCHEMA_PATHS.get(version)
    return load_json(path) if path is not None else None


def _schema_const(schema):
    try:
        return schema["properties"]["schema_version"]["const"]
    except (KeyError, TypeError):
        return None


def validation_errors(document, schema):
    version = document.get("schema_version") if isinstance(document, dict) else None
    selected_schema = schema
    if _schema_const(schema) != version:
        selected_schema = _schema_for_version(version) or schema
    validator = Draft202012Validator(
        selected_schema,
        format_checker=FormatChecker(),
    )
    errors = list(validator.iter_errors(document))
    errors.extend(headline_contract_semantic_errors(document))
    errors.extend(editorial_focus_contract_semantic_errors(document))
    errors.extend(tiered_universe_contract_semantic_errors(document))
    errors.extend(signal_eligibility_semantic_errors(document))
    if isinstance(document, dict):
        quality = document.get("data_quality")
        pool = quality.get("headline_pool") if isinstance(quality, dict) else None
        if isinstance(pool, dict) and "evidence_intake" in pool:
            from evidence_intake import validate_manifest
            for message in validate_manifest(pool["evidence_intake"]):
                errors.append(ValidationError(
                    message, validator="evidence_intake_semantics",
                    path=deque(["data_quality", "headline_pool", "evidence_intake"]),
                ))
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
