"""Offline acceptance checks for PR3 deterministic editorial focus.

The selector is deliberately downstream of NewsAgent's selected/fresh pool.
These checks use the frozen August 17 fixture and never contact the network.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, ".")

from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult
from config import ALL_TICKERS, PIPELINE_VERSION, SCHEMA_VERSION
from editorial_focus import (
    FOCUS_CONTRACT_VERSION,
    rank_focus_candidates,
    render_focus_text,
    select_focus,
)
from ledger import __version__ as LEDGER_VERSION
from ledger.ingest import _iter_signals


NOW = datetime(2026, 8, 17, 15, 32, 36, tzinfo=timezone.utc)
FIXTURE_PATH = Path("fixtures/headlines/2026-08-17.json")
FIXTURE_ROWS = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["records"]


class StaticProvider:
    name = "August 17 focus fixture"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return ProviderResult(self.name, "ok", [dict(row) for row in self.rows])


class EmptyGoogleProvider:
    name = "Google News"

    def fetch(self):
        return ProviderResult(self.name, "empty", [])

    def fetch_query(self, query, limit=None):
        return [], None


def selected_headlines(rows):
    agent = NewsAgent(
        now=NOW,
        providers=[StaticProvider(rows)],
        google_provider=EmptyGoogleProvider(),
    )
    agent.get_global_headlines()
    return agent.get_scored_headlines()


def without(*source_record_ids):
    removed = set(source_record_ids)
    return [
        row for row in FIXTURE_ROWS
        if row["source_record_id"] not in removed
    ]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


selected = selected_headlines(FIXTURE_ROWS)

# Frozen acceptance case: the material NXE financing development outranks the
# lower-impact ROKU filing even though ROKU has stronger source authority.
dynamic = select_focus(selected, ALL_TICKERS, NOW)
assert dynamic["status"] == "selected"
assert dynamic["selection_mode"] == "dynamic"
assert dynamic["ticker"] == "NXE"
assert dynamic["reason"] == "highest_ranked_supported_candidate"
assert dynamic["requested_ticker"] is None
assert dynamic["pin_rejection_reason"] is None
assert dynamic["score_components"]["impact"] == 2

# Removing NXE promotes the supported ROKU development. Removing both material
# issuer records leaves only impact=0 TSLA, which must not create an empty shell.
without_nxe = selected_headlines(without("aug17:nxe"))
roku_fallback = select_focus(without_nxe, ALL_TICKERS, NOW)
assert roku_fallback["status"] == "selected"
assert roku_fallback["selection_mode"] == "dynamic"
assert roku_fallback["ticker"] == "ROKU"

without_nxe_roku = selected_headlines(without("aug17:nxe", "aug17:roku"))
no_focus = select_focus(without_nxe_roku, ALL_TICKERS, NOW)
assert no_focus["status"] == "no_focus"
assert no_focus["selection_mode"] == "none"
assert no_focus["ticker"] is None
assert no_focus["reason"] == "insufficient_supported_evidence"
assert no_focus["eligible_candidate_count"] == 0
assert no_focus["evidence"] == []
assert render_focus_text(no_focus) == ""

# Broad Alpha Vantage peer tags cannot manufacture a universe-lane focus.
# A genuine Tesla title still maps through the strict company alias and remains
# eligible under the same fresh materiality gate.
peer_tagged_gm = {
    "title": "General Motors raises guidance after quarterly results",
    "link": "https://example.com/general-motors-guidance",
    "canonical_url": "https://example.com/general-motors-guidance",
    "published": "2026-08-17T14:00:00Z",
    "provider_seen_at": "2026-08-17T14:05:00Z",
    "source_time_kind": "published",
    "provider": "Alpha Vantage News",
    "publisher": "Example News",
    "publisher_domain": "example.com",
    "source_class": "api_news_discovery",
    "source_record_id": "alpha:gm-peer-tags",
    "tickers": ["GM", "F", "TSLA"],
    "ticker_metadata_kind": "related",
    "summary": "General Motors guidance coverage.",
}
gm_selected = selected_headlines([peer_tagged_gm])
assert gm_selected == []
assert select_focus(gm_selected, ALL_TICKERS, NOW)["status"] == "no_focus"

genuine_tesla = deepcopy(peer_tagged_gm)
genuine_tesla.update({
    "title": "Tesla raises guidance after quarterly results",
    "link": "https://example.com/tesla-guidance",
    "canonical_url": "https://example.com/tesla-guidance",
    "source_record_id": "alpha:tesla-subject",
})
tesla_selected = selected_headlines([genuine_tesla])
assert len(tesla_selected) == 1
assert tesla_selected[0]["universe_tickers"] == ["TSLA"]
tesla_focus = select_focus(tesla_selected, ALL_TICKERS, NOW)
assert tesla_focus["status"] == "selected"
assert tesla_focus["ticker"] == "TSLA"

# A configured ticker is only an editorial override when it independently
# satisfies the same mapped, fresh, usable-evidence gate.
configured_roku = select_focus(selected, ALL_TICKERS, NOW, "roku")
assert configured_roku["status"] == "selected"
assert configured_roku["selection_mode"] == "configured"
assert configured_roku["ticker"] == "ROKU"
assert configured_roku["requested_ticker"] == "ROKU"
assert configured_roku["pin_rejection_reason"] is None
assert {row["source_record_id"] for row in configured_roku["evidence"]} == {
    "aug17:roku"
}

missing_roku_pin = select_focus(without_nxe, ALL_TICKERS, NOW, "AMAT")
assert missing_roku_pin["status"] == "selected"
assert missing_roku_pin["selection_mode"] == "dynamic"
assert missing_roku_pin["ticker"] == "ROKU"
assert missing_roku_pin["requested_ticker"] == "AMAT"
assert missing_roku_pin["pin_rejection_reason"] == "no_usable_fresh_evidence"

unsupported_roku_pin = select_focus(
    selected_headlines(without("aug17:roku")), ALL_TICKERS, NOW, "ROKU"
)
assert unsupported_roku_pin["status"] == "selected"
assert unsupported_roku_pin["selection_mode"] == "dynamic"
assert unsupported_roku_pin["ticker"] == "NXE"
assert unsupported_roku_pin["pin_rejection_reason"] == "no_usable_fresh_evidence"

outside_pin = select_focus(selected, ALL_TICKERS, NOW, "DUOT")
assert outside_pin["status"] == "selected"
assert outside_pin["selection_mode"] == "dynamic"
assert outside_pin["ticker"] == "NXE"
assert outside_pin["requested_ticker"] == "DUOT"
assert outside_pin["pin_rejection_reason"] == "unmapped_or_outside_universe"

stale_direct = deepcopy(selected[0])
stale_direct["as_of"] = "2026-08-13T15:00:00Z"
stale_direct["published"] = "2026-08-13T15:00:00Z"
assert select_focus([stale_direct], ALL_TICKERS, NOW)["status"] == "no_focus"
undated_direct = deepcopy(selected[0])
undated_direct["as_of"] = undated_direct["published"] = None
assert select_focus([undated_direct], ALL_TICKERS, NOW)["status"] == "no_focus"

# Focus evidence must resolve through the complete public source projection.
# Missing identifiers or unsupported time semantics fail closed.
for required_field in ("source_record_id", "provider", "source_class"):
    missing_metadata = deepcopy(selected[0])
    missing_metadata[required_field] = ""
    assert select_focus([missing_metadata], ALL_TICKERS, NOW)["status"] == "no_focus"
unsupported_time_kind = deepcopy(selected[0])
unsupported_time_kind["source_time_kind"] = "retrieved"
assert select_focus([unsupported_time_kind], ALL_TICKERS, NOW)["status"] == "no_focus"

# Non-finite numeric source fields never escape bounded parsing or qualify a
# candidate by accident.
for nonfinite in (float("inf"), float("-inf")):
    nonfinite_impact = deepcopy(selected[0])
    nonfinite_impact["score_components"]["impact"] = nonfinite
    assert select_focus([nonfinite_impact], ALL_TICKERS, NOW)["status"] == "no_focus"

invalid_urls = (
    "https://valid.example/%ZZ",
    "https://example.com:",
    "https://user@example.com/path",
    "https://example.com./path",
    "https://127.0.0.1/path",
    "https://[2001:db8::1]/path",
    "https://-example.com/path",
    "https://example-.com/path",
    "https://example..com/path",
)
for invalid_link in invalid_urls:
    invalid_url = deepcopy(selected[0])
    invalid_url["link"] = invalid_link
    assert select_focus([invalid_url], ALL_TICKERS, NOW)["status"] == "no_focus"
invalid_canonical_url = deepcopy(selected[0])
invalid_canonical_url["canonical_url"] = "https://example..com/path"
assert select_focus([invalid_canonical_url], ALL_TICKERS, NOW)["status"] == "no_focus"
encoded_url = deepcopy(selected[0])
encoded_url["link"] = "https://valid.example/a%20path?x=a%2Fb#part"
encoded_focus = select_focus([encoded_url], ALL_TICKERS, NOW)
assert encoded_focus["status"] == "selected"
assert encoded_focus["evidence"][0]["link"] == encoded_url["link"]

malformed_components = deepcopy(selected[0])
malformed_components["score_components"] = "not-an-object"
assert select_focus([malformed_components], ALL_TICKERS, NOW)["status"] == "no_focus"

# Every rendered factual claim must resolve to an included evidence record and
# expose the corresponding source link. No orphan claim IDs or placeholders.
evidence_by_id = {
    row["evidence_id"]: row for row in dynamic["evidence"]
}
rendered = render_focus_text(dynamic)
assert "## EDITORIAL FOCUS — NXE" in rendered
assert "No data available" not in rendered
for claim_name in ("what_changed", "why_it_matters"):
    claim = dynamic[claim_name]
    assert claim["text"]
    assert claim["evidence_ids"]
    for evidence_id in claim["evidence_ids"]:
        assert evidence_id in evidence_by_id, (claim_name, evidence_id)
        assert evidence_by_id[evidence_id]["link"] in rendered

orphaned_focus = deepcopy(dynamic)
orphaned_focus["what_changed"]["evidence_ids"] = ["missing:evidence"]
assert render_focus_text(orphaned_focus) == ""
unlinked_focus = deepcopy(dynamic)
unlinked_focus["evidence"][0]["link"] = "relative/path"
assert render_focus_text(unlinked_focus) == ""

# Delivery order cannot alter the selected card or any of its evidence order.
reversed_selected = selected_headlines(list(reversed(FIXTURE_ROWS)))
assert canonical(select_focus(reversed_selected, ALL_TICKERS, NOW)) == canonical(dynamic)
assert canonical(select_focus(list(reversed(selected)), ALL_TICKERS, NOW)) == canonical(dynamic)


def tie_row(ticker, source_id, publisher_domain):
    return {
        "title": f"{ticker} reports a supported material development",
        "link": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "canonical_url": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "as_of": "2026-08-17T14:00:00Z",
        "observed_at": "2026-08-17T15:00:00Z",
        "provider": "Tie Fixture",
        "publisher": publisher_domain,
        "publisher_domain": publisher_domain,
        "source_class": "aggregator",
        "source_record_id": source_id,
        "source_time_kind": "published",
        "lane": "universe",
        "universe_tickers": [ticker],
        "score_components": {
            "issuer_relevance": 2,
            "macro_relevance": 0,
            "vertical_relevance": 0,
            "authority": 2,
            "novelty": 1,
            "impact": 2,
        },
    }


# Fully tied story candidates resolve by ticker, then source record ID. Both
# orders remain stable under reversal.
ties = [
    tie_row("ROKU", "tie:roku-z", "z-roku.example"),
    tie_row("NXE", "tie:nxe-z", "z-nxe.example"),
    tie_row("ROKU", "tie:roku-a", "a-roku.example"),
    tie_row("NXE", "tie:nxe-a", "a-nxe.example"),
]
ranked_ties = rank_focus_candidates(ties, ["ROKU", "NXE"], NOW)
ranked_ties_reversed = rank_focus_candidates(
    list(reversed(ties)), ["ROKU", "NXE"], NOW
)
assert canonical(ranked_ties_reversed) == canonical(ranked_ties)
assert [candidate["ticker"] for candidate in ranked_ties] == [
    "NXE", "NXE", "ROKU", "ROKU"
]
assert ranked_ties[0]["primary_evidence_id"] == "tie:nxe-a"

# Even a malformed upstream collision on the declared identity is resolved by
# canonical content, never by delivery order or Python's stable-sort input.
collision_a = tie_row("ROKU", "tie:collision", "collision.example")
collision_a["title"] = "Alpha supported development"
collision_a["link"] = collision_a["canonical_url"] = "https://collision.example/a"
collision_b = tie_row("ROKU", "tie:collision", "collision.example")
collision_b["title"] = "Beta supported development"
collision_b["link"] = collision_b["canonical_url"] = "https://collision.example/b"
collision_forward = rank_focus_candidates(
    [collision_a, collision_b], ["ROKU"], NOW
)
collision_reversed = rank_focus_candidates(
    [collision_b, collision_a], ["ROKU"], NOW
)
assert canonical(collision_forward) == canonical(collision_reversed)
assert canonical(select_focus([collision_a, collision_b], ["ROKU"], NOW)) == canonical(
    select_focus([collision_b, collision_a], ["ROKU"], NOW)
)

older_nxe = tie_row("NXE", "tie:older-nxe", "older.example")
older_nxe["as_of"] = "2026-08-17T12:00:00Z"
newer_roku = tie_row("ROKU", "tie:newer-roku", "newer.example")
newer_roku["as_of"] = "2026-08-17T14:30:00Z"
timestamp_tie = rank_focus_candidates(
    [older_nxe, newer_roku], ["NXE", "ROKU"], NOW
)
assert timestamp_tie[0]["primary_evidence_id"] == "tie:newer-roku"
assert timestamp_tie[0]["ticker"] == "ROKU"

# Separate stories for one issuer never lend each other component maxima or
# count as independent corroboration. Only merged same-story publisher
# provenance may increment the corroboration component.
high_impact = tie_row("ROKU", "story:impact", "impact.example")
high_impact["source_class"] = "global_discovery"
high_impact["score_components"]["authority"] = 1
official_routine = tie_row("ROKU", "story:official", "official.example")
official_routine["source_class"] = "official_regulator"
official_routine["score_components"]["authority"] = 3
official_routine["score_components"]["impact"] = 1
separate_stories = rank_focus_candidates(
    [official_routine, high_impact], ["ROKU"], NOW
)
assert separate_stories[0]["primary_evidence_id"] == "story:impact"
assert separate_stories[0]["score_components"]["impact"] == 2
assert separate_stories[0]["score_components"]["source_authority"] == 1
assert separate_stories[0]["score_components"]["independent_corroboration"] == 0

merged_story = tie_row("ROKU", "story:merged", "primary.example")
merged_story["publisher"] = "Primary News"
merged_story["duplicate_publishers"] = ["Primary News", "Second News"]
merged_story["duplicate_providers"] = ["Official Feeds", "FMP"]
merged_candidate = rank_focus_candidates([merged_story], ["ROKU"], NOW)[0]
assert merged_candidate["score_components"]["independent_corroboration"] == 1
assert merged_candidate["evidence"][0]["duplicate_publishers"] == [
    "Primary News", "Second News"
]

# Case variants and publisher-domain aliases describe one publisher, not
# independent corroboration. Invalid lineage elements are discarded rather
# than stringified (notably, None must never become the publisher "None").
alias_story = tie_row("ROKU", "story:aliases", "primary.example")
alias_story["publisher"] = "Primary News"
alias_story["publisher_domain"] = "primary.example"
alias_story["duplicate_publishers"] = [
    None,
    "PRIMARY NEWS",
    "primary.example",
    "WWW.PRIMARY.EXAMPLE",
    "Second News",
    "second news",
]
alias_candidate = rank_focus_candidates([alias_story], ["ROKU"], NOW)[0]
assert alias_candidate["score_components"]["independent_corroboration"] == 1
assert None not in alias_candidate["evidence"][0]["duplicate_publishers"]
assert "None" not in alias_candidate["evidence"][0]["duplicate_publishers"]

none_lineage = tie_row("ROKU", "story:none-lineage", "primary.example")
none_lineage["duplicate_publishers"] = [None]
none_candidate = rank_focus_candidates([none_lineage], ["ROKU"], NOW)[0]
assert none_candidate["score_components"]["independent_corroboration"] == 0
assert none_candidate["evidence"][0]["duplicate_publishers"] == []

# Selection and ranking are pure projections; callers retain their exact rows.
purity_input = deepcopy(selected)
purity_before = deepcopy(purity_input)
rank_focus_candidates(purity_input, ALL_TICKERS, NOW)
select_focus(purity_input, ALL_TICKERS, NOW, "ROKU")
assert purity_input == purity_before

# Editorial focus is not a Signal Ledger extractor. Adding it to an otherwise
# identical brief cannot add, remove, or mutate any extracted signal.
base_brief = {
    "sections": {
        "confluence": [{
            "ticker": "TSLA",
            "families": ["options", "social"],
            "confluence_score": 2,
            "record_id": "focus-isolation-control",
        }],
    },
}
with_focus = deepcopy(base_brief)
with_focus["sections"]["deep_dive"] = deepcopy(dynamic)
base_signals = list(_iter_signals(base_brief, "focus-run", PIPELINE_VERSION))
focus_signals = list(_iter_signals(with_focus, "focus-run", PIPELINE_VERSION))
assert canonical(focus_signals) == canonical(base_signals)
assert len(focus_signals) == 1
assert focus_signals[0]["family"] == "confluence"

# PR3 is editorial-only: public schema and signal/ledger eras do not advance.
assert SCHEMA_VERSION == "2.8"
assert PIPELINE_VERSION == "2.6.4"
assert LEDGER_VERSION == "2.6.4"
assert FOCUS_CONTRACT_VERSION == "2.7-editorial-focus-1"

print("PR3 deterministic editorial-focus acceptance checks passed")

