"""Offline regression for additive headline export and health contracts."""

import sys

sys.path.insert(0, ".")

from export_for_gemini import (
    RECORD_ID_KEYS,
    HEADLINE_CONTRACT_VERSION,
    build_dropped_headline_export_records,
    normalize_headline_pool_diagnostics,
    add_record_metadata,
    build_export_health,
    build_headline_export_records,
)
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)



selected = [{
    "text": "Fed holds rates (https://reuters.com/fed)",
    "title": "Fed holds rates",
    "link": "https://reuters.com/fed?utm_source=gdelt",
    "canonical_url": "https://reuters.com/fed",
    "published": None,
    "provider_seen_at": "2026-08-13T11:00:00Z",
    "source_time_kind": "provider_seen",
    "relevance": 1,
    "lane": "macro",
    "universe_tickers": [],
    "score_components": {
        "issuer_relevance": 0,
        "macro_relevance": 1,
        "vertical_relevance": 0,
        "authority": 1,
        "novelty": 0,
        "impact": 0,
    },
    "provider": "GDELT",
    "publisher": "Reuters",
    "publisher_domain": "reuters.com",
    "source_class": "global_discovery",
    "source_record_id": "gdelt:one",
    "tickers": [],
    "summary": None,
    "duplicate_providers": ["Alpha Vantage News", "GDELT"],
    "duplicate_publishers": ["Reuters"],
}]

records = build_headline_export_records(selected)
assert len(records) == 1
record = records[0]
for legacy_key in ("text", "title", "link", "published", "relevance", "source"):
    assert legacy_key in record
for provenance_key in (
    "canonical_url",
    "as_of",
    "observed_at",
    "source_time_kind",
    "provider",
    "publisher",
    "publisher_domain",
    "source_class",
    "source_record_id",
    "duplicate_providers",
    "duplicate_publishers",
    "tickers",
    "summary",
    "source",
    "lane",
    "universe_tickers",
    "score_components",
):
    assert provenance_key in record
assert RECORD_ID_KEYS["headlines"] == ("text",)
assert record["source"] == record["provider"] == "GDELT"
assert record["as_of"] == "2026-08-13T11:00:00Z"

assert record["observed_at"] is None
sections = {"headlines": records}
as_of_nulled = add_record_metadata(sections)
assert as_of_nulled == []
assert sections["headlines"][0]["as_of"] == "2026-08-13T11:00:00Z"
assert sections["headlines"][0]["observed_at"] is None
first_record_id = sections["headlines"][0]["record_id"]

records_again = build_headline_export_records(selected)
add_record_metadata({"headlines": records_again})
assert records_again[0]["record_id"] == first_record_id
published_row = dict(selected[0])
published_row.update({
    "provider": "FMP",
    "source_time_kind": "published",
    "published": "Thu, 13 Aug 2026 09:00:00 GMT",
    "as_of": "2026-08-13T09:00:00Z",
    "provider_seen_at": "2026-08-13T11:05:00Z",
})
published_export = build_headline_export_records([published_row])[0]
assert published_export["published"] == "Thu, 13 Aug 2026 09:00:00 GMT"
assert published_export["as_of"] == "2026-08-13T09:00:00Z"
assert published_export["observed_at"] == "2026-08-13T11:05:00Z"
rss_sections = {"headlines": [published_export]}
assert add_record_metadata(rss_sections) == []
rss_document = load_json(DEFAULT_FIXTURE)
rss_document["sections"]["headlines"] = rss_sections["headlines"]
assert validation_errors(
    rss_document,
    load_json(DEFAULT_SCHEMA),
) == []
undated_row = dict(published_row)
undated_row["published"] = None
undated_row["as_of"] = None
undated_export = build_headline_export_records([undated_row])[0]
assert undated_export["as_of"] is None
assert undated_export["observed_at"] == "2026-08-13T11:05:00Z"
undated_sections = {"headlines": [undated_export]}
assert add_record_metadata(undated_sections) == ["$.sections.headlines[0]"]
assert undated_sections["headlines"][0]["as_of"] is None

drop_source = dict(published_row)
drop_source.update({
    "drop_reason": "publisher_cap",
    "kept_source_record_id": None,
})
dropped_records = build_dropped_headline_export_records([drop_source])
assert len(dropped_records) == 1
dropped_record = dropped_records[0]
for key in (
    "text",
    "title",
    "link",
    "canonical_url",
    "published",
    "as_of",
    "observed_at",
    "source_time_kind",
    "relevance",
    "reason",
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
    "kept_source_record_id",
):
    assert key in dropped_record
assert dropped_record["reason"] == "publisher_cap"
assert dropped_record["link"] == drop_source["link"]
assert dropped_record["canonical_url"] == drop_source["canonical_url"]
assert dropped_record["as_of"] == "2026-08-13T09:00:00Z"
assert dropped_record["observed_at"] == "2026-08-13T11:05:00Z"
assert dropped_record["source_class"] == "global_discovery"
assert dropped_record["tickers"] == []
assert dropped_record["summary"] is None

provider_seen_drop = dict(selected[0])
provider_seen_drop["drop_reason"] = "duplicate"
provider_seen_drop["kept_source_record_id"] = "gdelt:kept"
provider_seen_export = build_dropped_headline_export_records(
    [provider_seen_drop]
)[0]
assert provider_seen_export["as_of"] == "2026-08-13T11:00:00Z"
assert provider_seen_export["observed_at"] is None
assert provider_seen_export["kept_source_record_id"] == "gdelt:kept"

derived_pool = normalize_headline_pool_diagnostics(
    {
        "fetched_count": 2,
        "fresh_before_relevance": 2,
        "selected_count": 99,
        "accounted_candidate_count": 99,
        "selected_lane_counts": {"universe": 99},
    },
    selected,
    [drop_source],
)
assert derived_pool["selected_count"] == 1
assert derived_pool["accounted_candidate_count"] == 2
assert derived_pool["selected_lane_counts"] == {"macro": 1}

try:
    normalize_headline_pool_diagnostics(
        {"fetched_count": 3, "fresh_before_relevance": 2},
        selected,
        [drop_source],
    )
except ValueError as exc:
    assert "headline candidate accounting mismatch" in str(exc)
    assert "fetched_count=3" in str(exc)
else:
    raise AssertionError("contradictory headline accounting did not fail")

fail_soft_pool = normalize_headline_pool_diagnostics({}, [], [])
assert fail_soft_pool == {
    "fetched_count": 0,
    "fresh_before_relevance": 0,
    "selected_count": 0,
    "accounted_candidate_count": 0,
    "selected_lane_counts": {},
}

fail_soft_document = load_json(DEFAULT_FIXTURE)
fail_soft_document["sections"]["headlines"] = []
fail_soft_document["data_quality"]["headlines_dropped"] = []
fail_soft_document["data_quality"]["headline_pool"] = fail_soft_pool
assert (
    fail_soft_document["data_quality"]["headline_contract_version"]
    == HEADLINE_CONTRACT_VERSION
)
assert validation_errors(fail_soft_document, load_json(DEFAULT_SCHEMA)) == []



class HealthAgent:
    def __init__(self, health=None):
        self.health = health or {}

    def get_health(self):
        return dict(self.health)


class NewsHealth:
    def get_pool_diagnostics(self):
        return {
            "fetched_count": 10,
            "fresh_before_relevance": 8,
            "fresh_relevant_count": 5,
            "selected_count": 4,
            "fallback_used": False,
            "providers": [
                {
                    "name": "FMP",
                    "status": "error",
                    "error": "http_status_403",
                },
                {
                    "name": "Official Feeds",
                    "status": "ok",
                    "partial_failures": 1,
                },
                {
                    "name": "FMP",
                    "status": "ok",
                    "error": None,
                    "fallback_used": True,
                },
            ],
        }


class ResearchHealth:
    adcom_parse_failed = False
    adcom_detail_failures = []

    def get_ceo_ca_health(self):
        return {}


health = build_export_health(
    "2026-08-13T12:00:00Z",
    1.25,
    ["[OpenInsider/STALE] cached narrative row"],
    {},
    {},
    [{"account": "Direct", "title": "one"}],
    [{"accession_number": "one"}],
    {},
    [{"ticker": "TSLA"}],
    [],
    [],
    NewsHealth(),
    HealthAgent(),
    HealthAgent({
        "status": "WARN",
        "failure_reason": "connection_refused",
        "error": "connection refused",
        "attempts": 4,
        "retries": 3,
        "cache_used": True,
        "cache_stale": True,
        "run_origin": "stale_cache",
    }),
    HealthAgent(),
    HealthAgent({
        "insider_cluster_fallback_used": True,
        "insider_cluster_fallback_reason": "stale_cache_disallowed",
        "openinsider_cluster_count": 0,
        "openinsider_cluster_rows_considered": 0,
        "openinsider_cluster_rows_eligible": 0,
        "openinsider_cluster_rows_dropped": {
            "stale": 0,
            "undated": 0,
            "future": 0,
            "out_of_window": 0,
        },
    }),
    ResearchHealth(),
)
assert any(
    warning == "Headline provider FMP failed: http_status_403"
    for warning in health["warnings"]
)
assert any(
    warning == "FMP Stock News is unavailable for this account; using the narrower FMP Articles feed."
    for warning in health["warnings"]
)
assert any(
    warning == "Headline provider Official Feeds had 1 partial fetch failure(s)."
    for warning in health["warnings"]
)
assert any(
    warning == "Publisher-diverse headline section underfilled: 4/5."
    for warning in health["warnings"]
)
assert health["sources"]["news"]["selected_count"] == 4
openinsider_health = health["sources"]["openinsider"]
assert openinsider_health["failure_reason"] == "connection_refused"
assert openinsider_health["social_stale_items"] == 1
assert openinsider_health["cluster_count"] == 0
assert openinsider_health["edgar_fallback_used"] is True
assert (
    openinsider_health["edgar_fallback_reason"]
    == "stale_cache_disallowed"
)
assert any(
    warning.startswith("OpenInsider connection_refused:")
    for warning in health["warnings"]
)
assert any(
    "stale cache supplied narrative-only context" in warning
    for warning in health["warnings"]
)

cache_write_health = build_export_health(
    "2026-08-13T12:00:00Z",
    1.25,
    ["[OpenInsider] ROKU: live purchase"],
    {},
    {},
    [{"account": "Direct", "title": "one"}],
    [{"accession_number": "one"}],
    {},
    [{"ticker": "TSLA"}],
    [],
    [],
    NewsHealth(),
    HealthAgent(),
    HealthAgent({
        "status": "OK",
        "live": True,
        "run_origin": "live",
        "cache_used": False,
        "cache_status": "write_error",
        "cache_error": "fixture disk full",
    }),
    HealthAgent(),
    HealthAgent({
        "insider_cluster_fallback_used": False,
        "insider_cluster_fallback_reason": None,
        "openinsider_cluster_count": 0,
        "openinsider_cluster_rows_considered": 1,
        "openinsider_cluster_rows_eligible": 1,
        "openinsider_cluster_rows_dropped": {},
    }),
    ResearchHealth(),
)
assert any(
    warning == (
        "OpenInsider live data was usable, but the last-known-good cache "
        "could not be updated: fixture disk full"
    )
    for warning in cache_write_health["warnings"]
)

print("Headline export provenance and provider-health checks passed")
