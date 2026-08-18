"""Deterministic edge regressions for headline classification and diversity."""

from collections import Counter
from datetime import datetime, timezone
import sys
from time import perf_counter

sys.path.insert(0, ".")

from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult
from config import ALL_TICKERS


NOW = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)


def components(title, **extra):
    item = {"title": title, **extra}
    scored = NewsAgent._score_components(item)
    return item, scored


# Listing metadata is stripped only when it is actually a listing. All common
# exchange spellings remain metadata, including the adjective form.
for exchange in ("NASDAQ", "NYSE", "AMEX", "NYSEARCA"):
    for delimiter in (":", " - ", " \u2013 ", " \u2014 "):
        title = (
            f"Outside Corp ({exchange}{delimiter}ZZZZ) "
            "announces quarterly dividend"
        )
        item, scored = components(title)
        assert NewsAgent._listing_symbols(title) == ["ZZZZ"]
        assert scored["macro_relevance"] == 0
        assert NewsAgent._assign_lane(item, scored) is None

listed_title = "Nasdaq-listed ZZZZ announces quarterly dividend"
listed_item, listed = components(listed_title)
assert NewsAgent._listing_symbols(listed_title) == ["ZZZZ"]
assert "nasdaq" not in NewsAgent._strip_exchange_listing_metadata(
    listed_title
).casefold()
assert listed["macro_relevance"] == 0
assert NewsAgent._assign_lane(listed_item, listed) is None

# A genuine exchange subject must not be mistaken for a ticker named Trading.
for title in (
    "Nasdaq: Trading halt after a systems failure",
    "NASDAQ: TRADING HALT AFTER A SYSTEMS FAILURE",
    "NASDAQ - TRADING HALT AFTER A SYSTEMS FAILURE",
    "NYSE: Trading resumes after a market-wide systems halt",
    "Trading resumes on NYSE after a market-wide systems halt",
    "NASDAQ: FED HOLDS RATES STEADY",
    "NASDAQ: S&P 500 RISES AFTER FED DECISION",
    "NYSE: RATE CUT HOPES LIFT STOCKS",
    "Breaking: NASDAQ: Trading halt after a systems failure",
    "NASDAQ: BREAKING: CPI comes in hotter than expected",
    "NASDAQ: UPDATE 1-CPI comes in hotter than expected",
    "NASDAQ: UPDATE 2: CPI comes in cooler than expected",
    "NASDAQ: BREAKING NEWS: CPI comes in hot",
    "NASDAQ: Breaking: Nasdaq futures decline before Fed",
    "Breaking: NASDAQ: UPDATE 4-NASDAQ futures decline before Fed",
):
    item, scored = components(title, source_class="official_exchange")
    assert NewsAgent._listing_symbols(title) == []
    assert scored["macro_relevance"] >= 1
    assert NewsAgent._assign_lane(item, scored) == "macro"

for title, symbol in (
    ("NASDAQ: INFLATION Data Corp reports CPI data", "INFLATION"),
    ("NASDAQ: PAYROLLS Inc reports CPI data", "PAYROLLS"),
):
    item, scored = components(title)
    assert NewsAgent._listing_symbols(title) == [symbol]
    assert scored["macro_relevance"] == 0, (title, scored)
    assert NewsAgent._assign_lane(item, scored) is None, (title, scored)

# Ambiguous bare tokens in company stories do not create macro eligibility.
for title in (
    "Dow Inc. announces quarterly dividend",
    "EYPT expands its clinical trial in China",
    "Acme increases dividend yield after quarterly results",
    "Airbnb says inflation pressures travel demand after earnings",
    "Acme expands payroll by 500 jobs after quarterly earnings",
    "Acme payroll rises as hiring expands",
    "Outside Corp warns tariff costs may pressure guidance",
    "Wall Street analysts upgrade Acme after earnings",
    "Acme expands manufacturing operations in China",
    "Acme oil production rises after new project starts",
    "Stocks including Dow Inc. rise after earnings",
    "Airbnb says inflation remains sticky after earnings",
):
    item, scored = components(title)
    assert scored["macro_relevance"] == 0, (title, scored)
    assert NewsAgent._assign_lane(item, scored) is None, (title, scored)

corporate_macro_context_titles = (
    "Acme says CPI report hurt quarterly margins",
    "Acme earnings fall after CPI data pressured demand",
    "Acme uses inflation data in new forecasting product",
    "Acme payrolls rise after acquisition closes",
    "Acme says tariffs on imports hit its guidance",
    "Acme bond yields rise after financing repricing",
    "Acme China manufacturing PMI software launches",
    "Acme oil prices tool receives update",
    "Acme bitcoin market product reports earnings",
    "Acme sees Wall Street gains boost advisory fees",
    "Acme says recession fears hurt quarterly margins",
    "Acme uses Federal Reserve data in a forecasting product",
    "Acme says interest rates hit guidance",
    "Acme says OPEC action hurt sales",
    "Acme sees S&P 500 decline pressure advisory fees",
    "Acme shares rise after CPI data falls",
    "Analysts upgrade Acme after CPI data",
    "Investors buy Acme as inflation cools",
    "Acme navigates high interest rates",
    "Acme confronts recession risks",
    "Acme responds to OPEC production cuts",
    "Acme tracks the S&P 500 benchmark",
    "Acme hedges against rising oil prices",
    "Acme explores the crypto market",
    "Acme pivots amid Federal Reserve tightening",
    "Acme shares rally as inflation cools",
    "Acme stock tumbles on recession fears",
    "Globex calibrates its model to CPI data",
    "Northstar Labs studies China manufacturing PMI",
    "Initech benchmarks portfolios against Wall Street gains",
    "Umbrella Holdings rebalances around OPEC cuts",
    "Wayne Enterprises recalibrates for Federal Reserve policy",
    "Stark Industries models recession scenarios",
    "Dow Inc. says CPI data hit quarterly margins",
    "Wall Street Journal says CPI data hit subscriptions",
    "Nasdaq Inc says CPI report affects revenue",
    "Markets Inc. reports CPI data",
    "Stocks & Company says inflation data changes forecasts",
    "Oil States International says CPI data hit sales",
    "Treasury Wine Estates says CPI data hit revenue",
    "Powell Industries says inflation data hit margins",
    "CPI Card Group reports inflation data",
    "Market Research says oil prices rose after CPI data",
    "Market Research covers bitcoin after CPI report",
    "Dow Jones & Company says CPI data hit revenue",
    "Inflation Data Corp reports quarterly revenue",
    "Payrolls Inc reports CPI data",
    "Federal Reserve Bank Corp says inflation hit margins",
    "OPEC Systems reports inflation data",
    "Recession Coffee reports CPI data",
    "Bond Yields Inc reports inflation data",
    "Interest Rates LLC reports CPI data",
    "NASDAQ: INFLATION Data Corp reports CPI data",
    "NASDAQ: PAYROLLS Inc reports CPI data",
    "Federal Reserve Data Services reports quarterly revenue",
    "Fed Policy Advisors reports quarterly revenue",
    "Central Bank Data Solutions reports quarterly revenue",
    "White House Data Systems reports quarterly revenue",
    "Government Policy Research reports quarterly revenue",
    "OPEC Policy Advisors reports quarterly revenue",
    "Treasury Department Services reports quarterly revenue",
    "CPI Report Services reports quarterly revenue",
    "Oil Outlook Research reports quarterly revenue",
    "Bitcoin Outlook Research reports quarterly revenue",
    "Wall Street Markets Research reports quarterly revenue",
    "NASDAQ: CPI Report Services reports quarterly revenue",
    "NASDAQ: FED Policy Advisors reports quarterly revenue",
    "NASDAQ: OPEC Policy Advisors reports quarterly revenue",
)
for title in corporate_macro_context_titles:
    item, scored = components(title)
    assert scored["macro_relevance"] == 0, (title, scored)
    assert NewsAgent._assign_lane(item, scored) is None, (title, scored)

    mapped_item, mapped = components(title, tickers=["AMAT"])
    assert mapped["macro_relevance"] == 0, (title, mapped)
    assert mapped["issuer_relevance"] == 2, (title, mapped)
    assert NewsAgent._assign_lane(
        mapped_item, mapped
    ) == "universe", (title, mapped)

for title in (
    "US inflation cools more than expected in July",
    "July payrolls miss economist forecasts",
    "US payroll rises in July",
    "US imposes new tariffs on steel imports",
    "CPI hotter than expected",
    "Inflation remains sticky despite rate pressures",
    "China manufacturing PMI signals slower activity",
    "China industrial activity expands in July",
    "US oil production rises to a record",
    "OPEC oil production falls after the meeting",
    "Dow index rises after the Fed decision",
    "Dow market update points to broad gains",
):
    item, scored = components(title)
    assert scored["macro_relevance"] >= 1, (title, scored)
    assert NewsAgent._assign_lane(item, scored) == "macro", (title, scored)

subject_led_macro_controls = (
    "CPI report hurts corporate earnings outlook",
    "Inflation data reshapes business forecasting products",
    "Payrolls rise as acquisition activity accelerates",
    "Tariffs on imports pressure company guidance",
    "Bond yields rise after corporate financing boom",
    "China manufacturing PMI software index signals slowdown",
    "Oil prices fall as producer margins narrow",
    "Bitcoin market rally lifts exchange earnings",
    "Wall Street gains boost advisory fees",
    "Markets fall after CPI data pressures demand",
    "Economists expect CPI report to stay hot",
    "White House says tariffs on imports take effect",
    "Recession fears rise after weak data",
    "Federal Reserve data informs new economic forecasts",
    "Interest rates hit a new cycle high",
    "OPEC action cuts global oil supply",
    "S&P 500 decline pressures Wall Street",
    "Markets rise after CPI falls",
    "Investors brace for CPI",
    "Interest rates remain high as growth slows",
    "Recession risks rise after weak data",
    "OPEC production cuts tighten global oil supply",
    "S&P 500 benchmark declines after the session",
    "Oil prices rise as inventories fall",
    "Crypto market expands after regulatory clarity",
    "Federal Reserve tightening continues into year-end",
    "Inflation cools while consumer demand holds",
    "Markets rally as inflation cools",
    "EU imposes tariffs on imports",
    "European markets fall after CPI data",
    "Breaking: CPI comes in hotter than expected",
    "UPDATE 1-CPI comes in hotter than expected",
    "UPDATE 2: CPI comes in cooler than expected",
    "BREAKING NEWS: CPI comes in hot",
    "European markets decline after CPI",
    "The U.S. Treasury announces sanctions policy",
    "The Treasury Department updates debt guidance",
    "Core CPI comes in hotter than expected",
    "July CPI comes in cooler than expected",
    "Global oil prices fall after inventory data",
    "Global crypto market rallies after regulatory clarity",
    "US 10-year Treasury yield jumps after CPI",
    "CPI comes in hot",
    "Inflation unexpectedly accelerates",
    "Rate cuts look less likely after CPI",
    "Markets rally after CPI",
    "Stocks rally after CPI",
    "Jerome Powell says inflation remains elevated",
    "European shares decline after CPI",
    "Bitcoin hits record high",
    "Crypto rallies after SEC approval",
    "Dow Jones falls after CPI",
    "Fed leaves rates unchanged",
    "Nasdaq futures decline before Fed",
    "Jobs report shows hiring slowed",
    "OPEC agrees production cut",
    "European stock markets decline after CPI",
    "Consumer price index comes in hot",
    "Consumer prices rise faster than expected",
    "Unemployment rises after jobs report",
    "Fed Chair Powell says rates may stay high",
    "Federal Reserve policy remains restrictive",
    "White House policy shifts tariff outlook",
    "Government policy raises import costs",
    "OPEC policy shifts oil supply outlook",
    "Bitcoin outlook brightens after approval",
    "Analysts await the payrolls report",
)
for title in subject_led_macro_controls:
    item, scored = components(title)
    assert scored["macro_relevance"] >= 1, (title, scored)
    assert NewsAgent._assign_lane(item, scored) == "macro", (title, scored)

mapped_macro_item, mapped_macro = components(
    "CPI report hurts corporate earnings outlook",
    tickers=["AMAT"],
)
assert mapped_macro["issuer_relevance"] == 2
assert mapped_macro["macro_relevance"] >= 1
assert NewsAgent._assign_lane(
    mapped_macro_item, mapped_macro
) == "universe"

secondary_macro_item, secondary_macro = components(
    "Tesla sinks 8% as Fed hike bets rise"
)
assert secondary_macro["issuer_relevance"] == 2
assert secondary_macro["macro_relevance"] >= 1
assert NewsAgent._assign_lane(
    secondary_macro_item, secondary_macro
) == "universe"

wall_item, wall_street = components("Wall Street falls after CPI report")
assert wall_street["macro_relevance"] == 2
assert NewsAgent._assign_lane(wall_item, wall_street) == "macro"


china_item, china = components(
    "China markets fall after central bank stimulus"
)
assert china["macro_relevance"] >= 1
assert NewsAgent._assign_lane(china_item, china) == "macro"

tagged_corporate_item, tagged_corporate = components(
    "Acme expands manufacturing operations in China",
    tickers=["EYPT"],
)
assert tagged_corporate["macro_relevance"] == 0
assert tagged_corporate["issuer_relevance"] == 0
assert NewsAgent._assign_lane(
    tagged_corporate_item, tagged_corporate
) is None

# Every configured instrument can be linked by its explicit symbol, including
# symbols absent from TICKER_ALIASES and punctuation-bearing instruments.
for ticker in ALL_TICKERS:
    title = f"{ticker} closes higher after the session"
    matched = NewsAgent._matched_universe_tickers({"title": title})
    assert ticker in matched, (ticker, matched)
    scored = NewsAgent._score_components({"title": title})
    assert scored["issuer_relevance"] >= 2, (ticker, scored)
    assert NewsAgent._assign_lane({"title": title}, scored) == "universe"

assert NewsAgent._matched_universe_tickers(
    {"title": "VIX closes higher after the session"}
) == ["^VIX"]
assert NewsAgent._matched_universe_tickers(
    {"title": "Volatility update", "tickers": ["VIX"]}
) == ["^VIX"]

for title in (
    "US MINT UNVEILS NEW COIN DESIGNS",
    "SMALL BUSINESSES URGE CONSUMERS TO SHOP LOCAL",
    "ITA AIRWAYS ADDS NEW LOCAL ROUTES",
):
    assert NewsAgent._matched_universe_tickers({"title": title}) == [], title

for ticker in NewsAgent.AMBIGUOUS_TITLE_TICKERS:
    assert ticker in NewsAgent._matched_universe_tickers({
        "title": "Quarterly issuer update",
        "tickers": [ticker],
    })
assert NewsAgent._matched_universe_tickers(
    {"title": "$COIN rises after the market opens"}
) == ["COIN"]
assert NewsAgent._matched_universe_tickers(
    {"title": "Shopify shares rise after earnings"}
) == ["SHOP"]
assert NewsAgent._matched_universe_tickers(
    {"title": "Defense investors buy the ITA ETF"}
) == ["ITA"]


def story_row(
    *,
    source_record_id,
    provider,
    publisher,
    source_class,
    tickers,
):
    return {
        "title": "Next-generation project wins financing approval",
        "link": "https://example.test/shared-story",
        "canonical_url": "https://example.test/shared-story",
        "published": "2026-08-18T14:00:00Z",
        "provider_seen_at": "2026-08-18T14:05:00Z",
        "source_time_kind": "published",
        "provider": provider,
        "publisher": publisher,
        "publisher_domain": f"{provider.casefold().replace(' ', '-')}.test",
        "source_class": source_class,
        "source_record_id": source_record_id,
        "tickers": tickers,
    }


# An authoritative representative must inherit universe mappings and the
# strongest component evidence from provider duplicates.
duplicate_rows = NewsAgent._select_relevant(
    [
        story_row(
            source_record_id="official-copy",
            provider="Official Feeds",
            publisher="Official Agency",
            source_class="official_regulator",
            tickers=[],
        ),
        story_row(
            source_record_id="tagged-copy",
            provider="Market API",
            publisher="Market Wire",
            source_class="aggregator",
            tickers=["AMAT"],
        ),
    ],
    top_n=None,
)
assert {row["lane"] for row in duplicate_rows} == {"universe", "discovery"}
assert {
    row["score_components"]["novelty"] for row in duplicate_rows
} == {0}
deduped, duplicate_drops = NewsAgent._deduplicate(duplicate_rows)
assert len(deduped) == 1
merged = deduped[0]
assert merged["source_record_id"] == "official-copy"

singleton = NewsAgent._select_relevant(
    [{"title": "Fed holds rates steady", "source_record_id": "singleton"}],
    top_n=None,
)
assert singleton[0]["score_components"]["novelty"] == 1

stable_items = [
    {"title": "Fed holds rates steady", "source_record_id": "stable-b"},
    {"title": "Inflation cools after the report", "source_record_id": "stable-a"},
]
forward = NewsAgent._select_relevant(stable_items, top_n=None)
reverse = NewsAgent._select_relevant(
    list(reversed(stable_items)), top_n=None
)
assert [row["source_record_id"] for row in forward] == [
    row["source_record_id"] for row in reverse
]
assert all(row["score_components"]["novelty"] == 1 for row in forward)

transitive_titles = (
    "Fed policy officials signal markets rates outlook economy investors bonds "
    "stocks global decision meeting statement guidance future path western alpha",
    "Fed policy officials signal markets rates outlook economy investors bonds "
    "stocks global decision meeting statement guidance future path western eastern",
    "Fed policy officials signal markets rates outlook economy investors bonds "
    "stocks global decision meeting statement guidance future path eastern gamma",
)
transitive_rows = [
    {
        "title": title,
        "canonical_url": f"https://story-{index}.test/article",
        "source_record_id": f"transitive-{index}",
        "provider": "Transitive Wire",
        "publisher": f"Transitive Publisher {index}",
        "source_class": "aggregator",
    }
    for index, title in enumerate(transitive_titles)
]
assert NewsAgent._same_story(transitive_rows[0], transitive_rows[1])
assert NewsAgent._same_story(transitive_rows[1], transitive_rows[2])
assert not NewsAgent._same_story(transitive_rows[0], transitive_rows[2])
assert len(set(NewsAgent._story_group_ids(transitive_rows))) == 1
transitive_components = NewsAgent._score_pool_components(transitive_rows)
assert {
    row["novelty"] for row in transitive_components
} == {0}
transitive_scored = NewsAgent._select_relevant(
    transitive_rows, top_n=None
)
transitive_kept, transitive_dropped = NewsAgent._deduplicate(
    transitive_scored
)
reverse_kept, reverse_dropped = NewsAgent._deduplicate(
    NewsAgent._select_relevant(
        list(reversed(transitive_rows)), top_n=None
    )
)
assert len(transitive_kept) == 1
assert len(transitive_dropped) == 2
assert [row["source_record_id"] for row in transitive_kept] == [
    row["source_record_id"] for row in reverse_kept
]
assert [row["source_record_id"] for row in transitive_dropped] == [
    row["source_record_id"] for row in reverse_dropped
]

assert merged["lane"] == "universe"
assert merged["universe_tickers"] == ["AMAT"]
assert merged["score_components"]["issuer_relevance"] == 2
assert merged["score_components"]["authority"] == 3
assert merged["score_components"]["impact"] == 3
assert merged["duplicate_providers"] == ["Market API", "Official Feeds"]
assert len(duplicate_drops) == 1
assert duplicate_drops[0]["drop_reason"] == "duplicate"


def card(index, provider, lane):
    return {
        "title": f"Card {index}",
        "provider": provider,
        "publisher": f"Publisher {index}",
        "publisher_domain": f"publisher-{index}.test",
        "source_class": "aggregator",
        "source_record_id": f"card-{index}",
        "lane": lane,
        "score_components": {
            "issuer_relevance": 2 if lane == "universe" else 0,
            "macro_relevance": 1 if lane == "macro" else 0,
            "vertical_relevance": 1 if lane == "discovery" else 0,
            "authority": 2,
            "novelty": 0,
            "impact": 2 if lane == "discovery" else 0,
        },
    }

publisher_rows = [
    card(31, "Wire A", "universe"),
    card(32, "Wire B", "macro"),
    card(33, "Wire C", "macro"),
    card(34, "Wire D", "macro"),
    card(35, "Wire E", "macro"),
    card(36, "Wire F", "macro"),
]
for row in publisher_rows[:3]:
    row["publisher"] = "Reuters"
    row["publisher_domain"] = "reuters.com"
publisher_selected, publisher_dropped = NewsAgent._select_diverse(
    publisher_rows
)
assert len(publisher_selected) == 5
assert sum(
    row["publisher_domain"] == "reuters.com"
    for row in publisher_selected
) == 2
assert {
    row["lane"]
    for row in publisher_selected
    if row["publisher_domain"] == "reuters.com"
} == {"universe", "macro"}
assert [row["drop_reason"] for row in publisher_dropped] == [
    "publisher_cap"
]



global_selected, global_dropped = NewsAgent._select_diverse([
    card(1, "Solo API", "universe"),
    card(2, "Solo API", "macro"),
    card(3, "Solo API", "discovery"),
    card(4, "Solo API", "universe"),
    card(5, "Other A", "macro"),
    card(6, "Other B", "macro"),
])
assert len(global_selected) == 5
assert sum(row["provider"] == "Solo API" for row in global_selected) == 3
assert [row["drop_reason"] for row in global_dropped] == ["provider_cap"]

lane_selected, lane_dropped = NewsAgent._select_diverse([
    card(11, "Lane API", "macro"),
    card(12, "Lane API", "macro"),
    card(13, "Lane API", "macro"),
    card(14, "Other C", "macro"),
    card(15, "Other D", "macro"),
    card(16, "Other E", "macro"),
])
assert len(lane_selected) == 5
assert sum(row["provider"] == "Lane API" for row in lane_selected) == 2
assert [row["drop_reason"] for row in lane_dropped] == [
    "provider_lane_cap"
]


def raw_row(index, provider, title):
    return {
        "title": title,
        "link": f"https://publisher-{index}.test/story",
        "canonical_url": f"https://publisher-{index}.test/story",
        "published": "2026-08-18T14:00:00Z",
        "provider_seen_at": "2026-08-18T14:05:00Z",
        "source_time_kind": "published",
        "provider": provider,
        "publisher": f"Publisher {index}",
        "publisher_domain": f"publisher-{index}.test",
        "source_class": "aggregator",
        "source_record_id": f"diagnostic-{index}",
        "tickers": [],
    }


class StaticProvider:
    name = "Static fixture"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return ProviderResult(
            self.name, "ok", [dict(row) for row in self.rows]
        )


class MustNotFetch:
    name = "Google News"

    def fetch(self):
        raise AssertionError("diverse primary rows already fill the section")


class CountingNewsAgent(NewsAgent):
    normalized_title_calls = 0

    def __init__(self, *args, **kwargs):
        self.process_pool_calls = 0
        super().__init__(*args, **kwargs)

    @classmethod
    def _normalized_title(cls, row):
        cls.normalized_title_calls += 1
        return super()._normalized_title(row)

    def _process_pool(self, raw):
        self.process_pool_calls += 1
        return super()._process_pool(raw)


diagnostic_rows = [
    raw_row(21, "Solo API", "Fed signals inflation outlook as prices rise"),
    raw_row(22, "Solo API", "Fed warns rate outlook as wages grow"),
    raw_row(23, "Solo API", "Fed sees policy outlook after growth data"),
    raw_row(24, "Other F", "Fed signals policy path as inflation eases"),
    raw_row(25, "Other G", "Fed warns rates may stay high"),
    raw_row(26, "Other H", "Fed sees growth slowing after hikes"),
    raw_row(27, "Other I", "EYPT expands its clinical trial in China"),
    raw_row(28, "Other J", "Local arts festival schedule"),
]
agent = NewsAgent(
    now=NOW,
    providers=[StaticProvider(diagnostic_rows)],
    google_provider=MustNotFetch(),
)
assert len(agent.get_global_headlines()) == 5
diagnostics = agent.get_pool_diagnostics()
assert diagnostics["cap_drop_reason_counts"] == {"provider_lane_cap": 1}
assert diagnostics["selected_provider_counts"]["Solo API"] == 2
assert diagnostics["selected_provider_lane_counts"]["macro:Solo API"] == 2
assert diagnostics["no_approved_lane_count"] == 2
assert diagnostics["fresh_relevance_zero_count"] == 1
assert diagnostics["accounted_candidate_count"] == len(diagnostic_rows)
diagnostic_drops = {
    row["source_record_id"]: row for row in agent.get_dropped_headlines()
}
assert diagnostic_drops["diagnostic-27"]["relevance"] == 1
assert diagnostic_drops["diagnostic-28"]["relevance"] == 0
assert diagnostic_drops["diagnostic-27"]["score_components"]["novelty"] == 1
assert diagnostic_drops["diagnostic-28"]["score_components"]["novelty"] == 1

stress_rows = [
    raw_row(
        1000 + index,
        f"Stress Provider {index}",
        (
            f"Fed signals scenario {index} uniquealpha{index} "
            f"uniquebeta{index} uniquegamma{index}"
        ),
    )
    for index in range(250)
]
expected_stress_ids = Counter(
    row["source_record_id"] for row in stress_rows
)

CountingNewsAgent.normalized_title_calls = 0
stress_agent = CountingNewsAgent(
    now=NOW,
    providers=[StaticProvider(stress_rows)],
    google_provider=MustNotFetch(),
)
stress_started = perf_counter()
assert len(stress_agent.get_global_headlines()) == 5
stress_elapsed = perf_counter() - stress_started
assert stress_elapsed < 10.0, stress_elapsed
assert stress_agent.process_pool_calls == 1
assert CountingNewsAgent.normalized_title_calls == len(stress_rows)
stress_diagnostics = stress_agent.get_pool_diagnostics()
assert stress_diagnostics["accounted_candidate_count"] == len(stress_rows)
assert stress_diagnostics["dedupe_dropped_count"] == 0
assert stress_diagnostics["cap_drop_reason_counts"] == {
    "selection_limit": len(stress_rows) - NewsAgent.TARGET_COUNT
}
stress_terminal = (
    stress_agent.get_scored_headlines()
    + stress_agent.get_dropped_headlines()
)
assert Counter(
    row["source_record_id"] for row in stress_terminal
) == expected_stress_ids
assert all(
    row["score_components"]["novelty"] == 1
    for row in stress_terminal
)
assert all("_story_group_id" not in row for row in stress_terminal)

CountingNewsAgent.normalized_title_calls = 0
reverse_stress_agent = CountingNewsAgent(
    now=NOW,
    providers=[StaticProvider(list(reversed(stress_rows)))],
    google_provider=MustNotFetch(),
)
reverse_started = perf_counter()
assert len(reverse_stress_agent.get_global_headlines()) == 5
reverse_elapsed = perf_counter() - reverse_started
assert reverse_elapsed < 10.0, reverse_elapsed
assert reverse_stress_agent.process_pool_calls == 1
assert CountingNewsAgent.normalized_title_calls == len(stress_rows)
reverse_terminal = (
    reverse_stress_agent.get_scored_headlines()
    + reverse_stress_agent.get_dropped_headlines()
)
assert Counter(
    row["source_record_id"] for row in reverse_terminal
) == expected_stress_ids
assert {
    row["source_record_id"]: row.get("drop_reason", "selected")
    for row in stress_terminal
} == {
    row["source_record_id"]: row.get("drop_reason", "selected")
    for row in reverse_terminal
}

print("Headline lane edge, mapping, dedupe, and provider diversity checks passed")
