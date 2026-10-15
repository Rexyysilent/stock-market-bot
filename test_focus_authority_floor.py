"""Offline acceptance checks for the editorial-focus source-authority floor.

A focus candidate must carry ``source_authority >= 1``. The floor is applied
inside ``rank_focus_candidates`` so that neither dynamic selection nor a
configured pin can rest on a row whose source class has no authority score
(for example the Google aggregated-news fallback) or whose score components
are missing. No schema change accompanies this rule: a pin that only has
below-floor evidence reports the existing ``no_usable_fresh_evidence`` reason
and ``focus_contract_version`` stays ``2.7-editorial-focus-1``.

These checks are deterministic and never contact the network.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import sys

sys.path.insert(0, ".")

from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult
from editorial_focus import (
    FOCUS_CONTRACT_VERSION,
    rank_focus_candidates,
    select_focus,
)


NOW = datetime(2026, 8, 17, 15, 32, 36, tzinfo=timezone.utc)
UNIVERSE = ["ROKU", "NXE", "TSLA"]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def row(ticker, source_id, *, authority, impact, source_class="aggregator",
        provider="Fixture Wire", publisher_domain="fixture.example",
        as_of="2026-08-17T14:00:00Z"):
    """One already-selected universe-lane headline row (post-projection shape)."""
    return {
        "title": f"{ticker} reports a development with impact {impact}",
        "link": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "canonical_url": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "as_of": as_of,
        "observed_at": "2026-08-17T15:00:00Z",
        "provider": provider,
        "publisher": publisher_domain,
        "publisher_domain": publisher_domain,
        "source_class": source_class,
        "source_record_id": source_id,
        "source_time_kind": "published",
        "lane": "universe",
        "universe_tickers": [ticker],
        "score_components": {
            "issuer_relevance": 2,
            "macro_relevance": 0,
            "vertical_relevance": 0,
            "authority": authority,
            "novelty": 1,
            "impact": impact,
        },
    }


assert FOCUS_CONTRACT_VERSION == "2.7-editorial-focus-1", (
    "the floor is a behaviour change under the existing contract string"
)

# --- Case 1: the highest-scoring authority-zero candidate loses to a
# supported candidate, even though impact and confidence would otherwise rank
# it first under the deterministic sort.
google_zero = row(
    "NXE", "google:nxe-zero", authority=0, impact=3,
    source_class="aggregated_news_fallback", provider="Google News",
    publisher_domain="news.google.com",
)
supported = row("ROKU", "wire:roku-two", authority=2, impact=2)

ranked = rank_focus_candidates([google_zero, supported], UNIVERSE, NOW)
assert [c["primary_evidence_id"] for c in ranked] == ["wire:roku-two"], ranked
assert ranked[0]["score_components"]["source_authority"] == 2

dynamic = select_focus([google_zero, supported], UNIVERSE, NOW)
assert dynamic["status"] == "selected"
assert dynamic["selection_mode"] == "dynamic"
assert dynamic["ticker"] == "ROKU"
assert dynamic["eligible_candidate_count"] == 1, (
    "a floored-out row is not an eligible candidate"
)
assert dynamic["score_components"]["source_authority"] == 2
assert all(
    evidence["source_record_id"] != "google:nxe-zero"
    for evidence in dynamic["evidence"]
)

# --- Case 2: only authority-zero candidates yield the truthful no-focus
# path, with eligible_candidate_count 0 so the no_focus schema shape holds.
only_zero = select_focus([google_zero], UNIVERSE, NOW)
assert only_zero["status"] == "no_focus"
assert only_zero["selection_mode"] == "none"
assert only_zero["ticker"] is None
assert only_zero["reason"] == "insufficient_supported_evidence"
assert only_zero["eligible_candidate_count"] == 0
assert only_zero["pin_rejection_reason"] is None
assert only_zero["evidence"] == []
assert only_zero["score_components"] is None

# --- Case 3: a configured pin cannot bypass the floor. The pinned ticker's
# only evidence is authority-zero, so the pin falls back to the supported
# dynamic candidate with the existing no_usable_fresh_evidence reason.
pinned_zero = select_focus([google_zero, supported], UNIVERSE, NOW, "NXE")
assert pinned_zero["status"] == "selected"
assert pinned_zero["selection_mode"] == "dynamic"
assert pinned_zero["ticker"] == "ROKU"
assert pinned_zero["requested_ticker"] == "NXE"
assert pinned_zero["pin_rejection_reason"] == "no_usable_fresh_evidence"
assert pinned_zero["reason"] == "highest_ranked_supported_candidate"

# --- Case 4: a pin with no evidence at all produces the identical output
# shape; the floor introduces no new rejection reason.
pinned_absent = select_focus([supported], UNIVERSE, NOW, "NXE")
assert pinned_absent["pin_rejection_reason"] == "no_usable_fresh_evidence"
assert canonical(pinned_absent) == canonical(
    select_focus([google_zero, supported], UNIVERSE, NOW, "NXE")
)

# A pin whose only evidence is authority-zero and with no other candidate at
# all is a no-focus with the same existing reason.
pinned_zero_alone = select_focus([google_zero], UNIVERSE, NOW, "NXE")
assert pinned_zero_alone["status"] == "no_focus"
assert pinned_zero_alone["requested_ticker"] == "NXE"
assert pinned_zero_alone["pin_rejection_reason"] == "no_usable_fresh_evidence"
assert pinned_zero_alone["eligible_candidate_count"] == 0

# A supported pin still works exactly as before.
pinned_supported = select_focus([google_zero, supported], UNIVERSE, NOW, "ROKU")
assert pinned_supported["selection_mode"] == "configured"
assert pinned_supported["ticker"] == "ROKU"
assert pinned_supported["pin_rejection_reason"] is None

# --- Case 5: a missing, null, or non-numeric authority component is 0 and is
# floored; it never becomes a candidate by accident.
missing_components = row("TSLA", "wire:tsla-missing", authority=1, impact=2)
del missing_components["score_components"]
null_authority = row("TSLA", "wire:tsla-null", authority=None, impact=2)
text_authority = row("TSLA", "wire:tsla-text", authority="official", impact=2)
negative_authority = row("TSLA", "wire:tsla-negative", authority=-2, impact=2)
for malformed in (
    missing_components, null_authority, text_authority, negative_authority,
):
    assert rank_focus_candidates([malformed], UNIVERSE, NOW) == [], malformed
    assert select_focus([malformed], UNIVERSE, NOW)["status"] == "no_focus"

# The boundary value 1 is inside the floor.
boundary = row("TSLA", "wire:tsla-one", authority=1, impact=2)
assert rank_focus_candidates([boundary], UNIVERSE, NOW)[0][
    "score_components"
]["source_authority"] == 1

# --- Case 6: determinism under input reversal with floored rows present.
mixed = [google_zero, supported, boundary, null_authority]
forward = rank_focus_candidates(mixed, UNIVERSE, NOW)
reverse = rank_focus_candidates(list(reversed(mixed)), UNIVERSE, NOW)
assert canonical(forward) == canonical(reverse)
assert [c["primary_evidence_id"] for c in forward] == [
    "wire:roku-two", "wire:tsla-one",
]
assert canonical(select_focus(mixed, UNIVERSE, NOW)) == canonical(
    select_focus(list(reversed(mixed)), UNIVERSE, NOW)
)

# --- Case 7: the authority-zero source is real, not a fixture artefact. A
# Google aggregated-news fallback row scored by NewsAgent carries
# authority 0, so the floor excludes it end to end.


class StaticProvider:
    name = "Floor fixture"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return ProviderResult(self.name, "ok", [dict(r) for r in self.rows])


class EmptyGoogleProvider:
    name = "Google News"

    def fetch(self):
        return ProviderResult(self.name, "empty", [])

    def fetch_query(self, query, limit=None):
        return [], None


fallback_raw = {
    "title": "Roku agrees to an acquisition of a streaming rival",
    "link": "https://news.google.com/articles/roku-acquisition",
    "canonical_url": "https://news.google.com/articles/roku-acquisition",
    "published": "2026-08-17T14:00:00Z",
    "source_time_kind": "published",
    "provider_seen_at": "2026-08-17T15:00:00Z",
    "provider": "Google News",
    "publisher": "Google News",
    "publisher_domain": "news.google.com",
    "source_class": "aggregated_news_fallback",
    "source_record_id": "google-news:roku-acquisition",
    "tickers": ["ROKU"],
    "ticker_metadata_kind": "subject",
    "summary": None,
}
agent = NewsAgent(
    now=NOW,
    providers=[StaticProvider([fallback_raw])],
    google_provider=EmptyGoogleProvider(),
)
agent.get_global_headlines()
scored = agent.get_scored_headlines()
assert len(scored) == 1 and scored[0]["lane"] == "universe", scored
assert scored[0]["score_components"]["authority"] == 0, scored[0]
assert scored[0]["score_components"]["impact"] >= 1
assert select_focus(scored, UNIVERSE, NOW)["status"] == "no_focus"
assert select_focus(scored, UNIVERSE, NOW, "ROKU")["pin_rejection_reason"] == (
    "no_usable_fresh_evidence"
)

# The input rows are never mutated by ranking or selection.
untouched = deepcopy(mixed)
rank_focus_candidates(mixed, UNIVERSE, NOW)
select_focus(mixed, UNIVERSE, NOW, "NXE")
assert canonical(mixed) == canonical(untouched)

print("Editorial-focus source-authority floor checks passed")
