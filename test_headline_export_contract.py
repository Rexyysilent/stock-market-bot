"""Offline regression for additive headline export and health contracts."""

import sys

sys.path.insert(0, ".")

from config import (
    EDITORIAL_COVERAGE_MODE,
    EDITORIAL_COVERAGE_TICKERS,
    SIGNAL_ELIGIBLE_TICKERS,
)
from editorial_focus import select_focus
from export_for_gemini import (
    RECORD_ID_KEYS,
    HEADLINE_CONTRACT_VERSION,
    build_dropped_headline_export_records,
    normalize_headline_pool_diagnostics,
    add_record_metadata,
    build_export_health,
    build_headline_export_records,
    audit_signal_eligible_tickers,
    filter_signal_eligible_cash_runway_alerts,
)
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)


def set_no_focus(document):
    document["summary"]["editorial_focus"] = {
        "status": "no_focus",
        "ticker": None,
        "selection_mode": "none",
        "eligible_candidate_count": 0,
    }
    universe = document["universe"]
    universe.update({
        "focus_ticker": None,
        "focus_coverage_tier": None,
        "focus_signal_eligible": None,
        "focus_signal_eligibility_reason": "no_focus",
        "configured_focus_ticker": None,
        "focus_selection_mode": "none",
    })
    document["sections"]["deep_dive"] = {
        "status": "no_focus",
        "coverage_tier": None,
        "signal_eligible": None,
        "signal_eligibility_reason": "no_focus",
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
    return document


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
    "ticker_metadata_kind": "subject",
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
    "ticker_metadata_kind",
    "duplicate_providers",
    "duplicate_publishers",
    "tickers",
    "summary",
    "source",
    "lane",
    "universe_tickers",
    "score_components",
    "coverage_tier",
    "signal_eligible",
    "signal_eligibility_reason",
):
    assert provenance_key in record
assert RECORD_ID_KEYS["headlines"] == ("text",)
assert record["source"] == record["provider"] == "GDELT"
assert record["ticker_metadata_kind"] == "subject"
assert record["as_of"] == "2026-08-13T11:00:00Z"
assert record["coverage_tier"] is None
assert record["signal_eligible"] is False
assert record["signal_eligibility_reason"] == "no_mapped_ticker"

selected_without_metadata_kind = dict(selected[0])
selected_without_metadata_kind.pop("ticker_metadata_kind")
assert "ticker_metadata_kind" not in build_headline_export_records(
    [selected_without_metadata_kind]
)[0]

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
rss_document = set_no_focus(load_json(DEFAULT_FIXTURE))
rss_document["sections"]["headlines"] = rss_sections["headlines"]
rss_document["summary"]["total_headlines"] = 1
rss_document["data_quality"]["headline_pool"]["selected_count"] = 1
rss_document["data_quality"]["headline_pool"]["selected_lane_counts"] = {
    "macro": 1}
rss_candidate_count = 1 + len(
    rss_document["data_quality"]["headlines_dropped"])
rss_document["data_quality"]["headline_pool"].update({
    "accounted_candidate_count": rss_candidate_count,
    "fetched_count": rss_candidate_count,
    "fresh_before_relevance": rss_candidate_count,
})
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
    "ticker_metadata_kind": "related",
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
    "coverage_tier",
    "signal_eligible",
    "signal_eligibility_reason",
    "provider",
    "publisher",
    "publisher_domain",
    "source_class",
    "source_record_id",
    "ticker_metadata_kind",
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
assert dropped_record["ticker_metadata_kind"] == "related"
assert dropped_record["tickers"] == []
assert dropped_record["summary"] is None
assert dropped_record["coverage_tier"] is None
assert dropped_record["signal_eligible"] is False
assert dropped_record["signal_eligibility_reason"] == "no_mapped_ticker"

dropped_without_metadata_kind = dict(drop_source)
dropped_without_metadata_kind.pop("ticker_metadata_kind")
assert "ticker_metadata_kind" not in build_dropped_headline_export_records(
    [dropped_without_metadata_kind]
)[0]

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
assert derived_pool["editorial_shadow"] == {
    "mode": EDITORIAL_COVERAGE_MODE, "candidate_count": 0,
    "fresh_candidate_count": 0, "records": [],
}

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
    "editorial_shadow": {
        "mode": EDITORIAL_COVERAGE_MODE,
        "candidate_count": 0,
        "fresh_candidate_count": 0,
        "records": [],
    },
}

fail_soft_document = set_no_focus(load_json(DEFAULT_FIXTURE))
fail_soft_document["sections"]["headlines"] = []
fail_soft_document["data_quality"]["headlines_dropped"] = []
fail_soft_document["data_quality"]["headline_pool"] = fail_soft_pool
fail_soft_document["summary"]["total_headlines"] = 0
assert (
    fail_soft_document["data_quality"]["headline_contract_version"]
    == HEADLINE_CONTRACT_VERSION
)
assert validation_errors(fail_soft_document, load_json(DEFAULT_SCHEMA)) == []



filtered_cash_alerts = filter_signal_eligible_cash_runway_alerts([
    {"ticker": "tsla", "risk_level": "RED", "message": "core"},
    {"ticker": "MRNA", "risk_level": "RED", "message": "editorial"},
    {"ticker": "OUTSIDE", "risk_level": "RED", "message": "outside"},
    {"ticker": "REPL", "risk_level": "RED", "message": "research context"},
    "malformed",
])
assert filtered_cash_alerts == [{
    "ticker": "TSLA",
    "risk_level": "RED",
    "message": "core",
}]


editorial_row = dict(selected[0])
editorial_row["universe_tickers"] = ["MRNA"]
editorial_export = build_headline_export_records([editorial_row])[0]
assert editorial_export["coverage_tier"] == "editorial_only"
assert editorial_export["signal_eligible"] is False
assert editorial_export["signal_eligibility_reason"] == "editorial_only_coverage"

mixed_row = dict(selected[0])
mixed_row["universe_tickers"] = ["NXE", "MRNA"]
mixed_export = build_headline_export_records([mixed_row])[0]
assert mixed_export["coverage_tier"] == "mixed"
assert mixed_export["signal_eligible"] is False
assert mixed_export["signal_eligibility_reason"] == (
    "mixed_coverage_requires_ticker_filter")

active_mrna_headline = {
    "title": "Moderna reports Phase 3 topline results",
    "text": "Moderna reports Phase 3 topline results",
    "link": "https://investors.modernatx.com/phase-3-results",
    "canonical_url": "https://investors.modernatx.com/phase-3-results",
    "published": "2026-08-17T15:20:00Z",
    "as_of": "2026-08-17T15:20:00Z",
    "observed_at": "2026-08-17T15:25:00Z",
    "source_time_kind": "published",
    "lane": "universe",
    "universe_tickers": ["MRNA"],
    "score_components": {
        "issuer_relevance": 2, "macro_relevance": 0,
        "vertical_relevance": 1, "authority": 3,
        "novelty": 1, "impact": 3,
    },
    "provider": "Official Feeds",
    "publisher": "Moderna",
    "publisher_domain": "investors.modernatx.com",
    "source_class": "official",
    "source_record_id": "fixture:mrna:focus",
    "tickers": ["MRNA"],
    "summary": None,
    "duplicate_providers": [],
    "duplicate_publishers": [],
}
active_mrna_focus = select_focus(
    [active_mrna_headline], EDITORIAL_COVERAGE_TICKERS,
    "2026-08-17T15:34:26Z", "MRNA",
    coverage_tickers=EDITORIAL_COVERAGE_TICKERS,
    coverage_mode="active",
)
assert active_mrna_focus["status"] == "selected"
assert active_mrna_focus["ticker"] == "MRNA"
assert active_mrna_focus["coverage_tier"] == "editorial_only"
assert active_mrna_focus["signal_eligible"] is False
assert active_mrna_focus["signal_eligibility_reason"] == (
    "editorial_only_coverage")

shadow_mrna_focus = select_focus(
    [active_mrna_headline], SIGNAL_ELIGIBLE_TICKERS,
    "2026-08-17T15:34:26Z", "MRNA",
    coverage_tickers=EDITORIAL_COVERAGE_TICKERS,
    coverage_mode="shadow",
)
assert shadow_mrna_focus["status"] == "no_focus"
assert shadow_mrna_focus["pin_rejection_reason"] == (
    "editorial_coverage_not_active")

legacy_replay = load_json(
    DEFAULT_FIXTURE.with_name("daily_brief.current-2.6.json"))
legacy_replay["sections"]["social_alerts"] = [{"ticker": "MRNA"}]
assert audit_signal_eligible_tickers(legacy_replay) is True

raw_context_document = {
    "schema_version": "2.8",
    "pipeline_version": "2.6.3",
    "sections": {
        "social_attention": [{"ticker": "MRNA", "universe_member": False}],
        "clinical_catalysts": [{"ticker": "VRTX", "fragile_alert": None}],
        "fda_catalysts": [{"ticker": "BEAM", "alert": None}],
        "fda_advisory_meetings": [{"ticker": "RARE"}],
        "cash_runway": [{"ticker": "MRNA"}],
        "ceo_ca_signals": [
            {"ticker": "SECTOR", "signal_eligible": False},
            {"ticker": "MRNA", "signal_eligible": False},
        ],
    },
}
assert audit_signal_eligible_tickers(raw_context_document) is True
for section_name, row in (
    ("social_alerts", {"ticker": "MRNA"}),
    ("social_attention", {"ticker": "MRNA", "universe_member": True}),
    ("clinical_catalysts", {"ticker": "VRTX", "fragile_alert": "FRAGILE_CATALYST"}),
    ("fda_catalysts", {"ticker": "BEAM", "alert": "FDA_CATALYST_NEAR"}),
    ("ceo_ca_signals", {"ticker": "MRNA", "signal_eligible": True}),
    ("cash_runway_alerts", {"ticker": "MRNA"}),
    ("cash_runway_alerts", {"ticker": "OUTSIDE"}),
):
    leaked_document = {
        "schema_version": "2.8", "pipeline_version": "2.6.3",
        "sections": {section_name: [row]},
    }
    try:
        audit_signal_eligible_tickers(leaked_document)
    except ValueError as exc:
        assert "outside-core ticker" in str(exc)
    else:
        raise AssertionError(f"{section_name} outside-core leak was accepted")


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
assert openinsider_health["social_insecure_items"] == 0
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

# OMNI-01: a pre-existing discovery story survives shadow unchanged, with a
# typed legacy_projection diagnostic and no new issuer/focus eligibility.
from datetime import datetime, timezone
from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult

class FrozenEditorialProvider:
    name = "Frozen editorial fixture"

    def fetch(self):
        return ProviderResult(self.name, "ok", [{
            "title": "Moderna Phase 3 trial met primary endpoint",
            "link": "https://moderna.example/frozen",
            "canonical_url": "https://moderna.example/frozen",
            "source_record_id": "omni:legacy-discovery",
            "provider": self.name, "publisher": "Moderna",
            "publisher_domain": "moderna.example", "source_class": "official",
            "source_time_kind": "published", "published": "2026-08-17T15:00:00Z",
            "provider_seen_at": "2026-08-17T15:10:00Z",
            "tickers": ["MRNA"], "ticker_metadata_kind": "subject",
        }])

frozen_agent = NewsAgent(now=datetime(2026, 8, 17, 15, 34, 26, tzinfo=timezone.utc),
                         providers=[FrozenEditorialProvider()])
frozen_agent._google_enabled = False
frozen_agent.get_global_headlines()
frozen_doc = set_no_focus(load_json(DEFAULT_FIXTURE))
frozen_doc["sections"]["headlines"] = build_headline_export_records(frozen_agent.get_scored_headlines())
add_record_metadata(frozen_doc["sections"])
frozen_doc["data_quality"]["headlines_dropped"] = build_dropped_headline_export_records(frozen_agent.get_dropped_headlines())
frozen_doc["data_quality"]["headline_pool"] = normalize_headline_pool_diagnostics(
    frozen_agent.get_pool_diagnostics(), frozen_agent.get_scored_headlines(),
    frozen_agent.get_dropped_headlines(),
)
frozen_doc["summary"]["total_headlines"] = 1
assert frozen_doc["sections"]["headlines"][0]["lane"] == "discovery"
assert frozen_doc["sections"]["headlines"][0]["universe_tickers"] == []
assert frozen_doc["sections"]["headlines"][0]["signal_eligible"] is False
assert frozen_doc["data_quality"]["headline_pool"]["editorial_shadow"]["records"][0]["disposition"] == "legacy_projection"
errors = validation_errors(frozen_doc, load_json(DEFAULT_SCHEMA))
assert not errors, [e.message for e in errors]
print("OMNI-01 legacy discovery shadow export is schema-valid")
