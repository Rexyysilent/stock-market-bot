"""Offline JSON Schema validation and negative-fixture regression."""

from copy import deepcopy
import sys

sys.path.insert(0, ".")

from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)


schema = load_json(DEFAULT_SCHEMA)
valid = load_json(DEFAULT_FIXTURE)
assert validation_errors(valid, schema) == []
assert valid["pipeline_version"] == "2.6.3"
assert (
    valid["data_quality"]["headline_contract_version"]
    == "2.6-headline-lanes-1"
)

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
future_pipeline["pipeline_version"] = "2.6.4"
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
wrong_fetched_accounting["data_quality"]["headline_pool"]["fetched_count"] = 3
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

print("Daily brief JSON Schema positive and negative checks passed")
