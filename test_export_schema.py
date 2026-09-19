"""Offline JSON Schema validation and negative-fixture regression."""

from copy import deepcopy
from coverage_policy import coverage_policy_manifest
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, ".")

from config import (
    EDITORIAL_COVERAGE_TICKERS,
    EDITORIAL_ONLY_TICKERS,
    SIGNAL_ELIGIBLE_TICKERS,
)
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)


schema = load_json(DEFAULT_SCHEMA)
current = load_json(DEFAULT_FIXTURE)
assert validation_errors(current, schema) == []

# Do not let the suite's PYTHONPATH hide direct-CLI import failures. Isolated
# Python and an unrelated cwd must still validate all three supported versions.
with tempfile.TemporaryDirectory(prefix="schema-cli-") as cli_cwd:
    cli = subprocess.run(
        [sys.executable, "-I", "-B",
         str(Path(__file__).resolve().parent / "scripts/validate_export_schema.py"),
         str(DEFAULT_FIXTURE),
         str(DEFAULT_FIXTURE.with_name("daily_brief.current-2.7.json")),
         str(DEFAULT_FIXTURE.with_name("daily_brief.current-2.6.json"))],
        cwd=cli_cwd, capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert cli.stdout.count("VALID ") == 3

assert current["schema_version"] == "2.8"
assert current["pipeline_version"] == "2.6.3"
assert current["universe"]["tickers"] == list(EDITORIAL_COVERAGE_TICKERS)
assert current["universe"]["instrumented_tickers"] == list(
    SIGNAL_ELIGIBLE_TICKERS)
assert current["universe"]["editorial_only_tickers"] == list(
    EDITORIAL_ONLY_TICKERS)
assert current["universe"]["tickers"] == (
    current["universe"]["instrumented_tickers"]
    + current["universe"]["editorial_only_tickers"]
)
assert current["universe"]["version"] == "a6277806"
assert current["universe"]["instrumented_version"] == "df0f60af"
assert current["universe"]["editorial_coverage_mode"] == "shadow"
assert current["universe"]["focus_eligible_tickers"] == list(
    SIGNAL_ELIGIBLE_TICKERS)
assert current["data_quality"]["headline_pool"]["editorial_shadow"][
    "records"
]

# Keep the marked 2.6.2 contract as an immutable regression target. The
# validator dispatches by the document's schema_version, not by today's schema.
valid = load_json(DEFAULT_FIXTURE.with_name("daily_brief.current-2.6.json"))
assert validation_errors(valid, schema) == []
assert valid["pipeline_version"] == "2.6.2"
assert (
    valid["data_quality"]["headline_contract_version"]
    == "2.6-headline-lanes-1"
)
assert (
    valid["data_quality"]["focus_contract_version"]
    == "2.6-editorial-focus-1"
)
assert valid["sections"]["deep_dive"]["status"] == "selected"
assert valid["universe"]["focus_ticker"] == "NXE"

assert (
    valid["data_quality"]["headline_pool"]["accounted_candidate_count"]
    == valid["data_quality"]["headline_pool"]["fetched_count"]
)

legacy_path = DEFAULT_FIXTURE.with_name("daily_brief.legacy-2.6.json")
legacy = load_json(legacy_path)
assert validation_errors(legacy, schema) == []
assert "headline_contract_version" not in legacy["data_quality"]
assert legacy["pipeline_version"] == "2.6.0"
assert "headline_pool" not in legacy["data_quality"]
legacy_headline = legacy["sections"]["headlines"][0]
for field in (
    "provider",
    "publisher",
    "publisher_domain",
    "source_class",
    "source_record_id",
    "ticker_metadata_kind",
    "lane",
    "universe_tickers",
    "score_components",
):
    assert field not in legacy_headline

legacy_partial_lane = deepcopy(legacy)
legacy_partial_lane["sections"]["headlines"][0]["lane"] = "macro"
errors = validation_errors(legacy_partial_lane, schema)
assert any(error.validator == "required" for error in errors)

legacy_261 = deepcopy(legacy)
legacy_261["pipeline_version"] = "2.6.1"
legacy_261["data_quality"]["headline_pool"] = {
    "fetched_count": 2,
    "fresh_before_relevance": 1,
}
assert validation_errors(legacy_261, schema) == []

marked_261 = deepcopy(valid)
del marked_261["data_quality"]["focus_contract_version"]
del marked_261["sections"]["deep_dive"]
del marked_261["universe"]
marked_261["pipeline_version"] = "2.6.1"
assert validation_errors(marked_261, schema) == []

legacy_partial_pool = deepcopy(legacy_261)
legacy_partial_pool["data_quality"]["headline_pool"][
    "accounted_candidate_count"
] = 2
errors = validation_errors(legacy_partial_pool, schema)
assert any(error.validator == "dependentRequired" for error in errors)

missing_required = deepcopy(valid)
del missing_required["generated_at"]
errors = validation_errors(missing_required, schema)
assert any(error.validator == "required" for error in errors)

invalid_lane = deepcopy(valid)
invalid_lane["sections"]["headlines"][0]["lane"] = "exchange_prefix"
errors = validation_errors(invalid_lane, schema)
assert any(error.validator == "enum" for error in errors)

invalid_components = deepcopy(valid)
del invalid_components["sections"]["headlines"][0]["score_components"]["impact"]
errors = validation_errors(invalid_components, schema)
assert any(error.validator == "required" for error in errors)

missing_current_lane = deepcopy(valid)
del missing_current_lane["sections"]["headlines"][0]["lane"]
errors = validation_errors(missing_current_lane, schema)
assert any(error.validator == "required" for error in errors)

missing_current_accounting = deepcopy(valid)
del missing_current_accounting["data_quality"]["headline_pool"][
    "accounted_candidate_count"
]
errors = validation_errors(missing_current_accounting, schema)
assert any(error.validator == "required" for error in errors)

missing_drop_reason = deepcopy(valid)
del missing_drop_reason["data_quality"]["headlines_dropped"][0]["reason"]
errors = validation_errors(missing_drop_reason, schema)
assert any(error.validator == "required" for error in errors)

invalid_drop_identity = deepcopy(valid)
invalid_drop_identity["data_quality"]["headlines_dropped"][0][
    "source_record_id"
] = None
errors = validation_errors(invalid_drop_identity, schema)
assert any(error.validator == "type" for error in errors)

invalid_lane_integrity = deepcopy(valid)
invalid_lane_integrity["sections"]["headlines"][0]["score_components"][
    "macro_relevance"
] = 0
errors = validation_errors(invalid_lane_integrity, schema)
assert any(error.validator == "minimum" for error in errors)

unknown_contract = deepcopy(valid)
unknown_contract["data_quality"]["headline_contract_version"] = "unknown"
errors = validation_errors(unknown_contract, schema)
assert any(error.validator == "const" for error in errors)

current_wrong_pipeline = deepcopy(valid)
current_wrong_pipeline["pipeline_version"] = "2.6.0"
errors = validation_errors(current_wrong_pipeline, schema)
assert any(
    error.validator == "enum" and list(error.absolute_path) == ["pipeline_version"]
    for error in errors
)

future_pipeline = deepcopy(valid)
future_pipeline["pipeline_version"] = "2.6.3"
errors = validation_errors(future_pipeline, schema)
assert any(
    error.validator == "enum" and list(error.absolute_path) == ["pipeline_version"]
    for error in errors
)

missing_current_pool = deepcopy(valid)
del missing_current_pool["data_quality"]["headline_pool"]
errors = validation_errors(missing_current_pool, schema)
assert any(error.validator == "required" for error in errors)

for pool_field in (
    "selected_count",
    "accounted_candidate_count",
    "selected_lane_counts",
):
    missing_pool_field = deepcopy(valid)
    del missing_pool_field["data_quality"]["headline_pool"][pool_field]
    errors = validation_errors(missing_pool_field, schema)
    assert any(error.validator == "required" for error in errors), pool_field

for headline_field in (
    "canonical_url",
    "published",
    "observed_at",
    "source_time_kind",
    "lane",
    "universe_tickers",
    "score_components",
    "provider",
    "publisher",
    "publisher_domain",
    "source_class",
    "source_record_id",
    "tickers",
    "summary",
    "duplicate_providers",
    "duplicate_publishers",
    "source",
):
    missing_headline_field = deepcopy(valid)
    del missing_headline_field["sections"]["headlines"][0][headline_field]
    errors = validation_errors(missing_headline_field, schema)
    assert any(error.validator == "required" for error in errors), headline_field

nullable_current_provenance = deepcopy(valid)
nullable_headline = nullable_current_provenance["sections"]["headlines"][0]
nullable_headline["published"] = None
nullable_headline["publisher"] = None
nullable_headline["publisher_domain"] = None
assert validation_errors(nullable_current_provenance, schema) == []

invalid_current_uri = deepcopy(valid)
invalid_current_uri["sections"]["headlines"][0]["canonical_url"] = "not a URI"
errors = validation_errors(invalid_current_uri, schema)
assert any(error.validator in {"format", "pattern"} for error in errors)

invalid_current_time_kind = deepcopy(valid)
invalid_current_time_kind["sections"]["headlines"][0][
    "source_time_kind"
] = "scrape_time"
errors = validation_errors(invalid_current_time_kind, schema)
assert any(error.validator == "enum" for error in errors)

null_current_identity = deepcopy(valid)
null_current_identity["sections"]["headlines"][0]["source_record_id"] = None
errors = validation_errors(null_current_identity, schema)
assert any(error.validator == "type" for error in errors)

invalid_drop_uri = deepcopy(valid)
invalid_drop_uri["data_quality"]["headlines_dropped"][0][
    "canonical_url"
] = "not a URI"
errors = validation_errors(invalid_drop_uri, schema)
assert any(error.validator in {"format", "pattern"} for error in errors)

invalid_drop_time_kind = deepcopy(valid)
invalid_drop_time_kind["data_quality"]["headlines_dropped"][0][
    "source_time_kind"
] = "scrape_time"
errors = validation_errors(invalid_drop_time_kind, schema)
assert any(error.validator == "enum" for error in errors)

optional_ticker_metadata_kind = deepcopy(valid)
del optional_ticker_metadata_kind["sections"]["headlines"][1][
    "ticker_metadata_kind"
]
assert validation_errors(optional_ticker_metadata_kind, schema) == []

invalid_selected_ticker_metadata_kind = deepcopy(valid)
invalid_selected_ticker_metadata_kind["sections"]["headlines"][1][
    "ticker_metadata_kind"
] = "exchange_prefix"
errors = validation_errors(invalid_selected_ticker_metadata_kind, schema)
assert any(error.validator == "enum" for error in errors)

invalid_dropped_ticker_metadata_kind = deepcopy(valid)
invalid_dropped_ticker_metadata_kind["data_quality"]["headlines_dropped"][0][
    "ticker_metadata_kind"
] = "exchange_prefix"
errors = validation_errors(invalid_dropped_ticker_metadata_kind, schema)
assert any(error.validator == "enum" for error in errors)

invalid_web_urls = (
    "relative/path",
    "https://",
    "https://?",
    "http://[",
    "javascript:alert(1)",
    "x:y",
    "https://example.com/%ZZ",
    "https://example.com/\\evil",
    "https://example.com/|bad",
    "https://example.com/\x01bad",
)
for url_field in ("link", "canonical_url"):
    for invalid_url in invalid_web_urls:
        invalid_selected_url = deepcopy(valid)
        invalid_selected_url["sections"]["headlines"][0][url_field] = invalid_url
        errors = validation_errors(invalid_selected_url, schema)
        assert any(error.validator == "pattern" for error in errors), (
            url_field,
            invalid_url,
        )
        assert any(
            error.validator == "headline_contract_semantics"
            and "valid absolute HTTP(S) URL" in error.message
            for error in errors
        ), (url_field, invalid_url)

        invalid_dropped_url = deepcopy(valid)
        invalid_dropped_url["data_quality"]["headlines_dropped"][0][
            url_field
        ] = invalid_url
        errors = validation_errors(invalid_dropped_url, schema)
        assert any(error.validator == "pattern" for error in errors), (
            url_field,
            invalid_url,
        )
        assert any(
            error.validator == "headline_contract_semantics"
            and "valid absolute HTTP(S) URL" in error.message
            for error in errors
        ), (url_field, invalid_url)

valid_web_urls = (
    "https://example.com/reports/alpha%20beta?q=a%2Fb&x=1#section-2",
    "HTTPS://news.example-domain.com:8443/a~b/c?tag=one+two&ok=true",
)
for valid_url in valid_web_urls:
    valid_url_document = deepcopy(valid)
    valid_selected = valid_url_document["sections"]["headlines"][0]
    valid_dropped = valid_url_document["data_quality"]["headlines_dropped"][0]
    for url_field in ("link", "canonical_url"):
        valid_selected[url_field] = valid_url
        valid_dropped[url_field] = valid_url
    assert validation_errors(valid_url_document, schema) == [], valid_url

invalid_date_times = (
    "yesterday",
    "2026-08-17T15:34:26",
    "2026-13-17T15:34:26Z",
)
for time_field in ("as_of", "observed_at"):
    for invalid_date_time in invalid_date_times:
        invalid_selected_time = deepcopy(valid)
        invalid_selected_time["sections"]["headlines"][0][
            time_field
        ] = invalid_date_time
        errors = validation_errors(invalid_selected_time, schema)
        assert any(error.validator == "pattern" for error in errors), (
            time_field,
            invalid_date_time,
        )

        invalid_dropped_time = deepcopy(valid)
        invalid_dropped_time["data_quality"]["headlines_dropped"][0][
            time_field
        ] = invalid_date_time
        errors = validation_errors(invalid_dropped_time, schema)
        assert any(error.validator == "pattern" for error in errors), (
            time_field,
            invalid_date_time,
        )

for url_field in ("link", "canonical_url"):
    invalid_port_url = deepcopy(valid)
    invalid_port_url["sections"]["headlines"][0][
        url_field
    ] = "https://example.com:99999/path"
    errors = validation_errors(invalid_port_url, schema)
    assert any(
        error.validator == "headline_contract_semantics"
        and "valid absolute HTTP(S) URL" in error.message
        for error in errors
    ), url_field

    invalid_drop_port_url = deepcopy(valid)
    invalid_drop_port_url["data_quality"]["headlines_dropped"][0][
        url_field
    ] = "https://example.com:99999/path"
    errors = validation_errors(invalid_drop_port_url, schema)
    assert any(
        error.validator == "headline_contract_semantics"
        and "valid absolute HTTP(S) URL" in error.message
        for error in errors
    ), url_field

for time_field in ("as_of", "observed_at"):
    impossible_selected_date = deepcopy(valid)
    impossible_selected_date["sections"]["headlines"][0][
        time_field
    ] = "2026-02-31T00:00:00Z"
    errors = validation_errors(impossible_selected_date, schema)
    assert any(
        error.validator == "headline_contract_semantics"
        and "timezone-qualified ISO date-time" in error.message
        for error in errors
    ), time_field

    impossible_dropped_date = deepcopy(valid)
    impossible_dropped_date["data_quality"]["headlines_dropped"][0][
        time_field
    ] = "2026-02-31T00:00:00Z"
    errors = validation_errors(impossible_dropped_date, schema)
    assert any(
        error.validator == "headline_contract_semantics"
        and "timezone-qualified ISO date-time" in error.message
        for error in errors
    ), time_field

nullable_current_transport = deepcopy(valid)
nullable_transport_headline = nullable_current_transport["sections"]["headlines"][0]
nullable_transport_headline["link"] = None
nullable_transport_headline["canonical_url"] = None
nullable_transport_headline["observed_at"] = None
assert validation_errors(nullable_current_transport, schema) == []

nullable_dropped_transport = deepcopy(valid)
nullable_transport_drop = nullable_dropped_transport["data_quality"][
    "headlines_dropped"
][0]
nullable_transport_drop["link"] = None
nullable_transport_drop["canonical_url"] = None
nullable_transport_drop["as_of"] = None
nullable_transport_drop["observed_at"] = None
assert validation_errors(nullable_dropped_transport, schema) == []

null_lane_wrong_reason = deepcopy(valid)
null_lane_wrong_reason["data_quality"]["headlines_dropped"][0][
    "reason"
] = "publisher_cap"
errors = validation_errors(null_lane_wrong_reason, schema)
assert any(error.validator == "const" for error in errors)

no_lane_reason_with_lane = deepcopy(valid)
no_lane_drop = no_lane_reason_with_lane["data_quality"]["headlines_dropped"][0]
no_lane_drop["lane"] = "macro"
no_lane_drop["score_components"]["macro_relevance"] = 1
errors = validation_errors(no_lane_reason_with_lane, schema)
assert any(error.validator == "const" for error in errors)

null_lane_with_mapping = deepcopy(valid)
null_lane_with_mapping["data_quality"]["headlines_dropped"][0][
    "universe_tickers"
] = ["DUOT"]
errors = validation_errors(null_lane_with_mapping, schema)
assert any(error.validator == "maxItems" for error in errors)

null_lane_with_issuer_score = deepcopy(valid)
null_lane_with_issuer_score["data_quality"]["headlines_dropped"][0][
    "score_components"
]["issuer_relevance"] = 1
errors = validation_errors(null_lane_with_issuer_score, schema)
assert any(error.validator == "const" for error in errors)

null_lane_with_macro_score = deepcopy(valid)
null_lane_with_macro_score["data_quality"]["headlines_dropped"][0][
    "score_components"
]["macro_relevance"] = 1
errors = validation_errors(null_lane_with_macro_score, schema)
assert any(error.validator == "const" for error in errors)

null_lane_with_discovery_predicate = deepcopy(valid)
null_discovery_components = null_lane_with_discovery_predicate["data_quality"][
    "headlines_dropped"
][0]["score_components"]
null_discovery_components["vertical_relevance"] = 1
null_discovery_components["impact"] = 2
errors = validation_errors(null_lane_with_discovery_predicate, schema)
assert any(error.validator == "not" for error in errors)

wrong_selected_count = deepcopy(valid)
wrong_selected_count["data_quality"]["headline_pool"]["selected_count"] = 0
errors = validation_errors(wrong_selected_count, schema)
assert any(
    error.validator == "headline_contract_semantics"
    and "selected_count must equal" in error.message
    for error in errors
)

wrong_lane_histogram = deepcopy(valid)
wrong_lane_histogram["data_quality"]["headline_pool"][
    "selected_lane_counts"
] = {"universe": 1}
errors = validation_errors(wrong_lane_histogram, schema)
assert any(
    error.validator == "headline_contract_semantics"
    and "selected_lane_counts must exactly equal" in error.message
    for error in errors
)

wrong_fetched_accounting = deepcopy(valid)
wrong_fetched_accounting["data_quality"]["headline_pool"]["fetched_count"] = 4
errors = validation_errors(wrong_fetched_accounting, schema)
assert any(
    error.validator == "headline_contract_semantics"
    and "accounted_candidate_count must equal fetched_count" in error.message
    for error in errors
)

wrong_terminal_accounting = deepcopy(valid)
wrong_terminal_accounting["data_quality"]["headlines_dropped"] = []
errors = validation_errors(wrong_terminal_accounting, schema)
assert any(
    error.validator == "headline_contract_semantics"
    and "selected_count + len(headlines_dropped)" in error.message
    for error in errors
)

wrong_source_alias = deepcopy(valid)
wrong_source_alias["sections"]["headlines"][0]["source"] = "Different Provider"
errors = validation_errors(wrong_source_alias, schema)
assert any(
    error.validator == "headline_contract_semantics"
    and "source must equal provider" in error.message
    for error in errors
)

legacy_semantic_bypass = deepcopy(legacy_261)
legacy_semantic_bypass["data_quality"]["headline_pool"]["selected_count"] = 5
assert validation_errors(legacy_semantic_bypass, schema) == []

malformed_current = deepcopy(valid)
malformed_current["sections"]["headlines"] = None
assert validation_errors(malformed_current, schema)

unknown_focus_contract = deepcopy(valid)
unknown_focus_contract["data_quality"]["focus_contract_version"] = "unknown"
errors = validation_errors(unknown_focus_contract, schema)
assert any(error.validator == "const" for error in errors)

missing_focus_section = deepcopy(valid)
del missing_focus_section["sections"]["deep_dive"]
errors = validation_errors(missing_focus_section, schema)
assert any(error.validator == "required" for error in errors)

focus_wrong_pipeline = deepcopy(valid)
focus_wrong_pipeline["pipeline_version"] = "2.6.1"
errors = validation_errors(focus_wrong_pipeline, schema)
assert any(
    error.validator == "const" and list(error.absolute_path) == ["pipeline_version"]
    for error in errors
)

missing_focus_headline_contract = deepcopy(valid)
del missing_focus_headline_contract["data_quality"]["headline_contract_version"]
errors = validation_errors(missing_focus_headline_contract, schema)
assert any(error.validator == "required" for error in errors)

focus_ticker_mismatch = deepcopy(valid)
focus_ticker_mismatch["universe"]["focus_ticker"] = "ROKU"
errors = validation_errors(focus_ticker_mismatch, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "selected ticker must equal" in error.message
    for error in errors
)

focus_outside_universe = deepcopy(valid)
focus_outside_universe["universe"]["tickers"] = ["ROKU"]
errors = validation_errors(focus_outside_universe, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must belong to universe.tickers" in error.message
    for error in errors
)

orphaned_focus_claim = deepcopy(valid)
orphaned_focus_claim["sections"]["deep_dive"]["what_changed"][
    "evidence_ids"
] = ["missing:evidence"]
errors = validation_errors(orphaned_focus_claim, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "does not resolve" in error.message
    for error in errors
)

dropped_focus_evidence = deepcopy(valid)
dropped_focus_evidence["sections"]["deep_dive"]["evidence"][0][
    "source_record_id"
] = "fixture:dropped:one"
errors = validation_errors(dropped_focus_evidence, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "cannot supply" in error.message
    for error in errors
)

unknown_focus_evidence = deepcopy(valid)
unknown_focus_evidence["sections"]["deep_dive"]["evidence"][0][
    "source_record_id"
] = "fixture:unknown"
errors = validation_errors(unknown_focus_evidence, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must resolve to sections.headlines" in error.message
    for error in errors
)

stale_focus_evidence = deepcopy(valid)
stale_focus_evidence["sections"]["headlines"][1]["as_of"] = (
    "2026-08-01T15:00:00Z"
)
stale_focus_evidence["sections"]["deep_dive"]["as_of"] = (
    "2026-08-01T15:00:00Z"
)
stale_focus_evidence["sections"]["deep_dive"]["evidence"][0]["as_of"] = (
    "2026-08-01T15:00:00Z"
)
errors = validation_errors(stale_focus_evidence, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "trailing three-day window" in error.message
    for error in errors
)

altered_focus_projection = deepcopy(valid)
altered_focus_projection["sections"]["deep_dive"]["evidence"][0][
    "title"
] = "Unsupported rewrite"
errors = validation_errors(altered_focus_projection, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must match its selected headline" in error.message
    for error in errors
)

missing_focus_time_kind = deepcopy(valid)
del missing_focus_time_kind["sections"]["deep_dive"]["evidence"][0][
    "source_time_kind"
]
errors = validation_errors(missing_focus_time_kind, schema)
assert any(error.validator in {"required", "oneOf"} for error in errors)

false_focus_corroboration = deepcopy(valid)
false_focus_corroboration["sections"]["deep_dive"]["evidence"][0][
    "independent_corroboration"
] = 1
errors = validation_errors(false_focus_corroboration, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "distinct duplicate publisher lineage" in error.message
    for error in errors
)

# Jointly rewriting the evidence and top-level projection must not evade the
# source binding. Each derived score is recomputed from the selected headline,
# its source class, or its source-time distance from generated_at.
joint_focus_score_rewrites = {
    "impact": 3,
    "confidence": 3,
    "novelty": 0,
    "source_authority": 3,
    "audience_relevance": 3,
    "timeliness": 2,
}
for field, replacement in joint_focus_score_rewrites.items():
    rewritten_score = deepcopy(valid)
    rewritten_score["sections"]["deep_dive"]["evidence"][0][
        field
    ] = replacement
    rewritten_score["sections"]["deep_dive"]["score_components"][
        field
    ] = replacement
    errors = validation_errors(rewritten_score, schema)
    assert any(
        error.validator == "editorial_focus_contract_semantics"
        and f"focus evidence {field}" in error.message
        and "deterministic selected-headline projection" in error.message
        for error in errors
    ), field

top_level_score_rewrite = deepcopy(valid)
top_level_score_rewrite["sections"]["deep_dive"]["score_components"][
    "impact"
] = 3
errors = validation_errors(top_level_score_rewrite, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must exactly equal the primary evidence projection" in error.message
    for error in errors
)

rewritten_evidence_grade = deepcopy(valid)
rewritten_evidence_grade["sections"]["deep_dive"][
    "evidence_grade"
] = "official_source"
errors = validation_errors(rewritten_evidence_grade, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must match primary-evidence authority" in error.message
    for error in errors
)

# A clean same-story corroboration projection changes the coherent grade. The
# corresponding wrong grade must fail even when evidence and top-level numeric
# components agree with one another.
corroborated_focus = deepcopy(valid)
focus = corroborated_focus["sections"]["deep_dive"]
focus_evidence = focus["evidence"][0]
source_record_id = focus_evidence["source_record_id"]
selected_headline = next(
    row for row in corroborated_focus["sections"]["headlines"]
    if row["source_record_id"] == source_record_id
)
selected_headline["duplicate_publishers"] = ["Independent Wire"]
focus_evidence["duplicate_publishers"] = ["Independent Wire"]
focus_evidence["independent_corroboration"] = 1
focus["score_components"]["independent_corroboration"] = 1
focus["evidence_grade"] = "multiple_selected_sources"
assert validation_errors(corroborated_focus, schema) == []

wrong_corroborated_grade = deepcopy(corroborated_focus)
wrong_corroborated_grade["sections"]["deep_dive"][
    "evidence_grade"
] = "single_source_secondary"
errors = validation_errors(wrong_corroborated_grade, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "independent corroboration" in error.message
    for error in errors
)

extra_weighted_score = deepcopy(valid)
extra_weighted_score["sections"]["deep_dive"]["score"] = 42
errors = validation_errors(extra_weighted_score, schema)
assert any(error.validator in {"additionalProperties", "oneOf"} for error in errors)

configured_focus = deepcopy(valid)
configured_focus["universe"]["configured_focus_ticker"] = "NXE"
configured_focus["universe"]["focus_selection_mode"] = "configured"
configured_focus["sections"]["deep_dive"]["selection_mode"] = "configured"
configured_focus["sections"]["deep_dive"]["requested_ticker"] = "NXE"
configured_focus["sections"]["deep_dive"]["reason"] = (
    "configured_focus_supported"
)
assert validation_errors(configured_focus, schema) == []

dynamic_after_rejected_config = deepcopy(valid)
dynamic_after_rejected_config["universe"]["configured_focus_ticker"] = "ROKU"
dynamic_after_rejected_config["sections"]["deep_dive"][
    "requested_ticker"
] = "ROKU"
dynamic_after_rejected_config["sections"]["deep_dive"][
    "pin_rejection_reason"
] = "no_usable_fresh_evidence"
assert validation_errors(dynamic_after_rejected_config, schema) == []

instrument_ticker_syntax = deepcopy(valid)
instrument_ticker_syntax["universe"]["tickers"] = ["NXE", "^VIX", "SI=F"]
assert validation_errors(instrument_ticker_syntax, schema) == []

configured_focus_mismatch = deepcopy(configured_focus)
configured_focus_mismatch["sections"]["deep_dive"]["requested_ticker"] = (
    "ROKU"
)
errors = validation_errors(configured_focus_mismatch, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "requested_ticker must equal" in error.message
    for error in errors
)


def no_focus_document():
    document = deepcopy(valid)
    document["summary"]["total_headlines"] = 1
    document["summary"]["editorial_focus"] = {
        "status": "no_focus",
        "ticker": None,
        "selection_mode": "none",
        "eligible_candidate_count": 0,
    }
    document["universe"]["focus_ticker"] = None
    document["universe"]["configured_focus_ticker"] = None
    document["universe"]["focus_selection_mode"] = "none"
    document["sections"]["headlines"] = [
        document["sections"]["headlines"][0]
    ]
    document["sections"]["deep_dive"] = {
        "status": "no_focus",
        "selection_mode": "none",
        "ticker": None,
        "entity_ids": [],
        "requested_ticker": None,
        "pin_rejection_reason": None,
        "reason": "insufficient_supported_evidence",
        "eligible_candidate_count": 0,
        "score_components": None,
        "headline": None,
        "what_changed": None,
        "why_it_matters": None,
        "evidence_grade": None,
        "market_reaction": None,
        "next_checkpoint": None,
        "contradictions_assessed": False,
        "contradictions": [],
        "as_of": None,
        "observed_at": None,
        "evidence": [],
        "note": "No editorial focus met the mapped fresh-evidence threshold.",
        "sec_filing_accessions": [],
        "has_insider_cluster": False,
        "social_mentions": [],
        "news_headlines": [],
        "twitter_signals": [],
    }
    pool = document["data_quality"]["headline_pool"]
    pool["fetched_count"] = 2
    pool["fresh_before_relevance"] = 2
    pool["selected_count"] = 1
    pool["accounted_candidate_count"] = 2
    pool["selected_lane_counts"] = {"macro": 1}
    return document


no_focus = no_focus_document()
assert validation_errors(no_focus, schema) == []

rejected_no_focus = no_focus_document()
rejected_no_focus["universe"]["configured_focus_ticker"] = "ROKU"
rejected_no_focus["sections"]["deep_dive"]["requested_ticker"] = "ROKU"
rejected_no_focus["sections"]["deep_dive"][
    "pin_rejection_reason"
] = "no_usable_fresh_evidence"
assert validation_errors(rejected_no_focus, schema) == []

missing_no_focus_rejection = deepcopy(rejected_no_focus)
missing_no_focus_rejection["sections"]["deep_dive"][
    "pin_rejection_reason"
] = None
errors = validation_errors(missing_no_focus_rejection, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must expose its rejection" in error.message
    for error in errors
)

no_focus_with_ticker = no_focus_document()
no_focus_with_ticker["universe"]["focus_ticker"] = "ROKU"
errors = validation_errors(no_focus_with_ticker, schema)
assert any(
    error.validator == "editorial_focus_contract_semantics"
    and "must be null" in error.message
    for error in errors
)

no_focus_with_evidence = no_focus_document()
no_focus_with_evidence["sections"]["deep_dive"]["evidence"] = [
    valid["sections"]["deep_dive"]["evidence"][0]
]
errors = validation_errors(no_focus_with_evidence, schema)
assert any(error.validator in {"maxItems", "oneOf"} for error in errors)

unmarked_focus_compatibility = deepcopy(valid)
del unmarked_focus_compatibility["data_quality"]["focus_contract_version"]
unmarked_focus_compatibility["sections"]["deep_dive"] = {"ticker": "ROKU"}
assert validation_errors(unmarked_focus_compatibility, schema) == []


def has_semantic_error(errors, validator, text):
    return any(
        error.validator == validator and text in error.message
        for error in errors
    )


wrong_coverage_hash = deepcopy(current)
wrong_coverage_hash["universe"]["version"] = "00000000"
errors = validation_errors(wrong_coverage_hash, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "fingerprint")

wrong_instrumented_hash = deepcopy(current)
wrong_instrumented_hash["universe"]["instrumented_version"] = "00000000"
errors = validation_errors(wrong_instrumented_hash, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "fingerprint")

wrong_partition_order = deepcopy(current)
wrong_partition_order["universe"]["tickers"][-2:] = reversed(
    wrong_partition_order["universe"]["tickers"][-2:]
)
errors = validation_errors(wrong_partition_order, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "followed by")

overlapping_partitions = deepcopy(current)
overlapping_partitions["universe"]["editorial_only_tickers"][0] = "TSLA"
errors = validation_errors(overlapping_partitions, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "disjoint")

wrong_shadow_focus_set = deepcopy(current)
wrong_shadow_focus_set["universe"]["focus_eligible_tickers"] = list(
    wrong_shadow_focus_set["universe"]["tickers"])
errors = validation_errors(wrong_shadow_focus_set, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "active mode")

wrong_headline_tier = deepcopy(current)
wrong_headline_tier["sections"]["headlines"][1]["coverage_tier"] = (
    "editorial_only"
)
errors = validation_errors(wrong_headline_tier, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "headline tier")

shadow_selected_editorial = deepcopy(current)
shadow_story = shadow_selected_editorial["sections"]["headlines"][1]
shadow_story.update({
    "universe_tickers": ["MRNA"],
    "coverage_tier": "editorial_only",
    "signal_eligible": False,
    "signal_eligibility_reason": "editorial_only_coverage",
})
errors = validation_errors(shadow_selected_editorial, schema)
assert has_semantic_error(
    errors, "tiered_universe_contract_semantics", "outside active mode")

active_editorial = deepcopy(current)
active_editorial["universe"]["editorial_coverage_mode"] = "active"
active_editorial["universe"]["coverage_policy"] = coverage_policy_manifest("active")
active_editorial["universe"]["focus_eligible_tickers"] = list(
    active_editorial["universe"]["tickers"])
active_editorial["data_quality"]["headline_pool"]["editorial_shadow"] = {
    "mode": "active", "candidate_count": 0,
    "fresh_candidate_count": 0, "records": [],
}
mrna_story = deepcopy(active_editorial["sections"]["headlines"][0])
mrna_story.update({
    "text": "Moderna reports a material clinical development (https://investors.modernatx.com/active)",
    "title": "Moderna reports a material clinical development",
    "link": "https://investors.modernatx.com/active",
    "canonical_url": "https://investors.modernatx.com/active",
    "published": "2026-08-17T15:20:00Z",
    "as_of": "2026-08-17T15:20:00Z",
    "lane": "universe",
    "universe_tickers": ["MRNA"],
    "coverage_tier": "editorial_only",
    "signal_eligible": False,
    "signal_eligibility_reason": "editorial_only_coverage",
    "score_components": {
        "issuer_relevance": 2, "macro_relevance": 0,
        "vertical_relevance": 1, "authority": 3,
        "novelty": 1, "impact": 3,
    },
    "provider": "Official Feeds",
    "publisher": "Moderna",
    "publisher_domain": "investors.modernatx.com",
    "source_class": "official_company",
    "source_record_id": "fixture:mrna:active",
    "tickers": ["MRNA"],
    "source": "Official Feeds",
    "record_id": "1234567890abcdea",
})
active_editorial["sections"]["headlines"].append(mrna_story)
active_pool = active_editorial["data_quality"]["headline_pool"]
active_pool.update({
    "fetched_count": 5, "fresh_before_relevance": 5,
    "selected_count": 3, "accounted_candidate_count": 5,
    "selected_lane_counts": {"macro": 1, "universe": 2},
})
active_editorial["summary"]["total_headlines"] = 3
assert validation_errors(active_editorial, schema) == []

orphan_shadow = deepcopy(current)
orphan_shadow["data_quality"]["headline_pool"]["editorial_shadow"][
    "records"
][0]["source_record_id"] = "fixture:missing"
errors = validation_errors(orphan_shadow, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "exactly one")

mismatched_shadow = deepcopy(current)
mismatched_shadow["data_quality"]["headline_pool"]["editorial_shadow"][
    "records"
][0]["title"] = "Fabricated audit title"
errors = validation_errors(mismatched_shadow, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "must match")

wrong_suppressed_terminal = deepcopy(current)
wrong_suppressed_terminal["data_quality"]["headlines_dropped"][0][
    "reason"
] = "selection_limit"
errors = validation_errors(wrong_suppressed_terminal, schema)
assert has_semantic_error(errors, "tiered_universe_contract_semantics", "suppressed")

core_projection_shadow = deepcopy(current)
core_record = core_projection_shadow["data_quality"]["headline_pool"][
    "editorial_shadow"
]["records"][0]
core_terminal = core_projection_shadow["sections"]["headlines"][1]
for field in ("source_record_id", "title", "link", "as_of"):
    core_record[field] = core_terminal[field]
core_record.update({
    "tickers": ["MRNA"], "disposition": "core_projection",
    "reason": "editorial_mapping_shadowed",
})
assert validation_errors(core_projection_shadow, schema) == []

outside_pin = deepcopy(current)
outside_pin["universe"]["configured_focus_ticker"] = "OUTSIDE"
outside_pin["sections"]["deep_dive"]["requested_ticker"] = "OUTSIDE"
outside_pin["sections"]["deep_dive"][
    "pin_rejection_reason"
] = "unmapped_or_outside_universe"
assert validation_errors(outside_pin, schema) == []

shadow_editorial_pin = deepcopy(current)
shadow_editorial_pin["universe"]["configured_focus_ticker"] = "MRNA"
shadow_editorial_pin["sections"]["deep_dive"]["requested_ticker"] = "MRNA"
shadow_editorial_pin["sections"]["deep_dive"][
    "pin_rejection_reason"
] = "editorial_coverage_not_active"
assert validation_errors(shadow_editorial_pin, schema) == []

raw_context = deepcopy(current)
raw_context["sections"].update({
    "social_attention": [{"ticker": "MRNA", "universe_member": False}],
    "clinical_catalysts": [{"ticker": "VRTX", "fragile_alert": None}],
    "fda_catalysts": [{"ticker": "BEAM", "alert": None}],
    "fda_advisory_meetings": [{"ticker": "RARE"}],
    "cash_runway": [{"ticker": "MRNA"}],
    "ceo_ca_signals": [
        {"ticker": "SECTOR", "signal_eligible": False},
        {"ticker": "MRNA", "signal_eligible": False},
    ],
})
assert validation_errors(raw_context, schema) == []

for section_name, row in (
    ("confluence", {"ticker": "MRNA"}),
    ("social_attention", {"ticker": "MRNA", "universe_member": True}),
    ("clinical_catalysts", {"ticker": "VRTX", "fragile_alert": "FRAGILE_CATALYST"}),
    ("fda_catalysts", {"ticker": "BEAM", "alert": "FDA_CATALYST_NEAR"}),
    ("ceo_ca_signals", {"ticker": "MRNA", "signal_eligible": True}),
    ("cash_runway_alerts", {"ticker": "MRNA"}),
    ("cash_runway_alerts", {"ticker": "OUTSIDE"}),
):
    leaked = deepcopy(current)
    leaked["sections"][section_name] = [row]
    errors = validation_errors(leaked, schema)
    assert has_semantic_error(
        errors, "signal_eligibility_semantics", "instrumented_tickers"
    ), section_name

print("Daily brief JSON Schema positive and negative checks passed")
