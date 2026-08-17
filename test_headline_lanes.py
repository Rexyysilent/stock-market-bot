"""Offline regression for the August 17 editorial headline-lane defect."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, ".")

from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult


NOW = datetime(2026, 8, 17, 15, 32, 36, tzinfo=timezone.utc)
FIXTURE_PATH = Path("fixtures/headlines/2026-08-17.json")
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
ROWS = FIXTURE["records"]


class StaticProvider:
    name = "August 17 fixture"

    def __init__(self, rows=ROWS):
        self.rows = rows

    def fetch(self):
        return ProviderResult(self.name, "ok", [dict(row) for row in self.rows])


class MustNotFetch:
    name = "Google News"

    def fetch(self):
        raise AssertionError("five eligible primary rows should avoid Google")


by_id = {row["source_record_id"]: row for row in ROWS}

# Exchange listing metadata is removed before macro scoring. A genuine Nasdaq
# market-structure subject remains eligible.
duot = NewsAgent._score_components(by_id["aug17:duot"])
assert duot["issuer_relevance"] == 0
assert duot["macro_relevance"] == 0
assert NewsAgent._assign_lane(by_id["aug17:duot"], duot) is None

nasdaq = NewsAgent._score_components(by_id["aug17:nasdaq-market-structure"])
assert nasdaq["macro_relevance"] >= 1
assert NewsAgent._assign_lane(
    by_id["aug17:nasdaq-market-structure"], nasdaq
) == "macro"

nxe = NewsAgent._score_components(by_id["aug17:nxe"])
assert nxe["issuer_relevance"] >= 2
assert NewsAgent._assign_lane(by_id["aug17:nxe"], nxe) == "universe"

for source_id in ("aug17:duot", "aug17:trv", "aug17:eypt", "aug17:abnb"):
    components = NewsAgent._score_components(by_id[source_id])
    assert NewsAgent._assign_lane(by_id[source_id], components) is None, source_id

agent = NewsAgent(
    now=NOW,
    providers=[StaticProvider()],
    google_provider=MustNotFetch(),
)
rendered = agent.get_global_headlines()
selected = agent.get_scored_headlines()
dropped = agent.get_dropped_headlines()
diagnostics = agent.get_pool_diagnostics()

assert len(rendered) == len(selected) == 5
assert {row["source_record_id"] for row in selected} == {
    "aug17:tsla",
    "aug17:nxe",
    "aug17:nasdaq-market-structure",
    "aug17:fed",
    "aug17:roku",
}
assert Counter(row["lane"] for row in selected) == {
    "universe": 3,
    "macro": 2,
}
assert diagnostics["selected_lane_counts"] == {
    "universe": 3,
    "macro": 2,
}
assert diagnostics["eligible_lane_counts"] == {
    "universe": 3,
    "macro": 2,
}
assert diagnostics["relevant_lane_counts"] == {
    "universe": 4,
    "macro": 2,
}
assert diagnostics["no_approved_lane_count"] == 4
assert diagnostics["accounted_candidate_count"] == diagnostics["fetched_count"]
assert all(
    set(row["score_components"]) == {
        "issuer_relevance",
        "macro_relevance",
        "vertical_relevance",
        "authority",
        "novelty",
        "impact",
    }
    for row in selected
)

drop_reasons = {
    row["source_record_id"]: row["drop_reason"] for row in dropped
}
for source_id in ("aug17:duot", "aug17:trv", "aug17:eypt", "aug17:abnb"):
    assert drop_reasons[source_id] == "no_approved_lane"
assert drop_reasons["aug17:stale-amat"] == "stale"

# Selection and ordering must not depend on upstream delivery order.
reversed_agent = NewsAgent(
    now=NOW,
    providers=[StaticProvider(list(reversed(ROWS)))],
    google_provider=MustNotFetch(),
)
reversed_agent.get_global_headlines()
assert [row["source_record_id"] for row in reversed_agent.get_scored_headlines()] == [
    row["source_record_id"] for row in selected
]

print("August 17 headline lane and exchange-prefix regression checks passed")
