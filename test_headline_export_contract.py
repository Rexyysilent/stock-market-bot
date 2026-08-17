"""Offline regression for additive headline export and health contracts."""

import sys

sys.path.insert(0, ".")

from export_for_gemini import (
    RECORD_ID_KEYS,
    add_record_metadata,
    build_export_health,
    build_headline_export_records,
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
    "published": "2026-08-13 09:00:00",
    "provider_seen_at": "2026-08-13T11:05:00Z",
})
published_export = build_headline_export_records([published_row])[0]
assert published_export["as_of"] == "2026-08-13 09:00:00"
assert published_export["observed_at"] == "2026-08-13T11:05:00Z"
undated_row = dict(published_row)
undated_row["published"] = None
undated_export = build_headline_export_records([undated_row])[0]
assert undated_export["as_of"] is None
assert undated_export["observed_at"] == "2026-08-13T11:05:00Z"
undated_sections = {"headlines": [undated_export]}
assert add_record_metadata(undated_sections) == ["$.sections.headlines[0]"]
assert undated_sections["headlines"][0]["as_of"] is None



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
    [],
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
    HealthAgent(),
    HealthAgent(),
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

print("Headline export provenance and provider-health checks passed")
