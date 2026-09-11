"""Offline regression: diversified headline selection and legacy contracts."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import sys

sys.path.insert(0, ".")

from agents.news_agent import NewsAgent
from agents.news_providers import GoogleNewsProvider, ProviderResult
from config import GDELT_TICKER_ALIASES
from timeutil import to_utc_z


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, content, status=200):
        self.status_code = status
        self.content = content


gdelt_queries = NewsAgent._build_gdelt_queries()
gdelt_query = " ".join(gdelt_queries).casefold()
assert all(
    aliases[0].casefold() in gdelt_query
    for aliases in GDELT_TICKER_ALIASES.values()
    if aliases
)
assert all("sourcelang:english" in query for query in gdelt_queries)
assert all(50 <= len(query) <= 190 for query in gdelt_queries)
assert len(gdelt_queries) > 1


def google_only(feed_sequence, *, now=NOW):
    responses = iter(feed_sequence)
    provider = GoogleNewsProvider(
        primary_query=NewsAgent.PRIMARY_QUERY,
        fallback_queries=NewsAgent.FALLBACK_QUERIES,
        now=now,
        request_get=lambda *a, **k: Response(next(responses)),
    )
    return NewsAgent(now=now, providers=[], google_provider=provider)


# Legacy structured scoring contract remains usable without provider metadata.
recs = NewsAgent._select_relevant([
    {
        "title": "Tesla sinks 8% as Fed hike bets rise - Yahoo",
        "link": "http://x/1",
        "published": "Mon, 20 Jul 2026 13:05:00 GMT",
    },
    {
        "title": "Is the stock market closed today? - AP",
        "link": "http://x/2",
        "published": "Mon, 20 Jul 2026 12:00:00 GMT",
    },
    {
        "title": "Uranium spot rips higher - WSJ",
        "link": "http://x/3",
        "published": None,
    },
], top_n=5)
assert [row["relevance"] for row in recs] == [3, 1], recs
assert recs[0]["text"].endswith("(http://x/1)")
for row in recs:
    assert {"text", "title", "link", "published", "relevance"} <= set(row)
    assert row["lane"] in {"universe", "macro", "discovery"}
    assert set(row["score_components"]) == {
        "issuer_relevance", "macro_relevance", "vertical_relevance",
        "authority", "novelty", "impact",
    }
assert to_utc_z(recs[0]["published"]) == "2026-07-20T13:05:00Z"
assert recs[1]["published"] is None
assert not any("closed today" in row["text"] for row in recs)

recs2 = NewsAgent._select_relevant([
    {"title": "Fed holds rates steady"}
], top_n=5)
assert recs2[0]["link"] is None
assert recs2[0]["text"] == "Fed holds rates steady"

# Strong broad-market anchors survive; utility filler does not.
broad = NewsAgent._select_relevant([
    {
        "title": (
            "Stock market today: Dow, S&P 500, Nasdaq futures rise "
            "as oil tumbles"
        ),
        "link": "http://x/market",
        "published": "Mon, 20 Jul 2026 10:30:00 GMT",
    },
    {
        "title": "Is the stock market open today?",
        "link": "http://x/utility",
        "published": "Mon, 20 Jul 2026 10:31:00 GMT",
    },
], top_n=None)
assert [row["link"] for row in broad] == ["http://x/market"]
assert broad[0]["relevance"] == 4

# Bare corporate-treasury usage is not a macro hit; market/government Treasury is.
assert NewsAgent._score_title(
    "FIS Treasury Tools Use AI for Cash Forecasting and Liquidity"
) == 0
assert NewsAgent._score_title("Corporate treasury software gets an upgrade") == 0
assert NewsAgent._score_title("10-year Treasury yield rises after auction") >= 2
assert NewsAgent._score_title("U.S. Treasury announces sanctions policy") >= 1
assert NewsAgent._score_title("Treasury Department updates debt guidance") >= 1

# Google parser keeps full pubDate plus underlying publisher metadata.
FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>Fed cuts rates</title><link>http://x/9</link>
<pubDate>Mon, 20 Jul 2026 09:30:00 -0400</pubDate>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>Untimed wire copy</title><link>http://x/10</link></item>
</channel></rss>"""
agent = google_only([FEED])
records, error = agent._fetch_rss_records("anything", limit=10)
assert error is None
assert records[0]["published"] == "Mon, 20 Jul 2026 09:30:00 -0400"
assert records[0]["publisher"] == "Reuters"
assert records[0]["publisher_domain"] == "reuters.com"
assert records[1]["published"] is None

# Recency boundary and transparent GDELT-style provider-seen fallback.
undated_official = {
    "title": "Fed official statement",
    "published": None,
    "provider_seen_at": "2026-07-20T13:59:00Z",
    "source_time_kind": "published",
    "relevance": 1,
}
assert NewsAgent._drop_stale([undated_official], now=NOW)[1][0]["drop_reason"] == "undated"

fresh, dropped = NewsAgent._drop_stale([
    {
        "title": "fresh",
        "published": "Mon, 20 Jul 2026 09:30:00 GMT",
        "relevance": 1,
    },
    {
        "title": "boundary",
        "published": "Fri, 17 Jul 2026 14:00:00 GMT",
        "relevance": 1,
    },
    {
        "title": "provider seen",
        "published": None,
        "provider_seen_at": "2026-07-20T12:00:00Z",
        "source_time_kind": "provider_seen",
        "relevance": 1,
    },
    {
        "title": "stale",
        "published": "Tue, 07 Jul 2026 09:30:00 GMT",
        "relevance": 3,
    },
    {"title": "undated", "published": None, "relevance": 3},
], now=NOW)
assert [row["title"] for row in fresh] == [
    "fresh", "boundary", "provider seen"
]
assert [(row["title"], row["drop_reason"]) for row in dropped] == [
    ("stale", "stale"), ("undated", "undated")
]

# End to end: stale high-score copy is dropped; fresh relevant copy survives.
STALE_FEED = ("""<?xml version="1.0"?><rss><channel>
<item><title>Tesla and the Fed and tariffs</title><link>http://x/stale</link>
<pubDate>Tue, 23 Jun 2026 09:30:00 GMT</pubDate>
<source url="https://stale.example">Stale Wire</source></item>
<item><title>Fed holds rates</title><link>http://x/fresh</link>
<pubDate>%s</pubDate><source url="https://fresh.example">Fresh Wire</source></item>
</channel></rss>""" % format_datetime(
    NOW - timedelta(hours=2)
)).encode()
agent2 = google_only([STALE_FEED])
texts = agent2.get_global_headlines()
assert texts == ["Fed holds rates (http://x/fresh)"], texts
assert [row["title"] for row in agent2.get_scored_headlines()] == [
    "Fed holds rates"
]
gate_drops = [
    row for row in agent2.get_dropped_headlines()
    if row.get("drop_reason") in ("stale", "undated")
]
assert [(row["title"], row["drop_reason"]) for row in gate_drops] == [
    ("Tesla and the Fed and tariffs", "stale")
]

# Raw-pool telemetry measures the source pool before relevance scoring.
DIAGNOSTIC_FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>Stock market today: Dow, S&amp;P 500, Nasdaq futures rise as oil tumbles</title>
<link>http://x/market</link><pubDate>Mon, 20 Jul 2026 10:30:00 GMT</pubDate>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>Is the stock market open today?</title>
<link>http://x/utility</link><pubDate>Mon, 20 Jul 2026 10:31:00 GMT</pubDate>
<source url="https://www.apnews.com">Associated Press</source></item>
</channel></rss>"""
agent3 = google_only([DIAGNOSTIC_FEED])
diagnostic_texts = agent3.get_global_headlines()
assert len(diagnostic_texts) == 1 and "Dow" in diagnostic_texts[0]
pool = agent3.get_pool_diagnostics()
assert pool["fetched_count"] == 2
assert pool["fresh_before_relevance"] == 2
assert pool["newest_as_of"] == "2026-07-20T10:31:00Z"
assert pool["fresh_relevant_count"] == 1
assert pool["fresh_relevance_zero_count"] == 1
assert pool["google_fallback_used"] is True
assert pool["selected_publisher_counts"] == {"reuters.com": 1}

# Empty Google broad query tries targeted market fallback.
EMPTY = b"<?xml version='1.0'?><rss><channel></channel></rss>"
agent4 = google_only([EMPTY, DIAGNOSTIC_FEED])
fallback_texts = agent4.get_global_headlines()
fallback_pool = agent4.get_pool_diagnostics()
assert len(fallback_texts) == 1 and "Dow" in fallback_texts[0]
assert fallback_pool["fallback_used"] is True
assert fallback_pool["google_fallback_used"] is True
assert [row["record_count"] for row in fallback_pool["attempts"]] == [0, 2]

INELIGIBLE = b"""<rss><channel><item><title>Local arts festival schedule</title>
<link>http://x/arts</link><pubDate>Mon, 20 Jul 2026 10:32:00 GMT</pubDate>
<source url="https://local.example">Local News</source></item></channel></rss>"""
agent4b = google_only([INELIGIBLE, DIAGNOSTIC_FEED])
eligible_fallback_texts = agent4b.get_global_headlines()
eligible_fallback_pool = agent4b.get_pool_diagnostics()
assert len(eligible_fallback_texts) == 1 and "Dow" in eligible_fallback_texts[0]
assert eligible_fallback_pool["fallback_used"] is True
assert eligible_fallback_pool["providers"][-1]["eligibility_fallback_used"] is True
assert [row["record_count"] for row in eligible_fallback_pool["attempts"]] == [1, 2]


class StaticProvider:
    def __init__(self, name, rows, status="ok", error=None):
        self.name = name
        self.rows = rows
        self.status = status
        self.error = error

    def fetch(self):
        return ProviderResult(
            self.name, self.status, [dict(row) for row in self.rows],
            error=self.error
        )


def row(title, publisher, domain, provider, hour, link):
    return {
        "title": title,
        "link": link,
        "canonical_url": link,
        "published": f"2026-07-20T{hour:02d}:00:00Z",
        "source_time_kind": "published",
        "provider_seen_at": "2026-07-20T14:00:00Z",
        "provider": provider,
        "publisher": publisher,
        "publisher_domain": domain,
        "source_class": "licensed_market_news",
        "source_record_id": f"{provider}:{hour}:{domain}",
        "tickers": [],
        "summary": None,
    }


# Five distinct primary publishers suppress Google entirely.
primary_rows = [
    row("Fed market update one", "Reuters", "reuters.com", "FMP", 13, "https://reuters.com/1"),
    row("Nasdaq market update two", "AP", "apnews.com", "FMP", 12, "https://apnews.com/2"),
    row("Oil market update three", "Bloomberg", "bloomberg.com", "AV", 11, "https://bloomberg.com/3"),
    row("Uranium prices surge on supply disruption", "CNBC", "cnbc.com", "GDELT", 10, "https://cnbc.com/4"),
    row("Bitcoin market update five", "FT", "ft.com", "GDELT", 9, "https://ft.com/5"),
]


class MustNotFetch:
    name = "Google News"

    def fetch(self):
        raise AssertionError("Google should be lazy")


agent5 = NewsAgent(
    now=NOW,
    providers=[StaticProvider("primary", primary_rows)],
    google_provider=MustNotFetch(),
)
assert len(agent5.get_global_headlines()) == 5
assert agent5.get_pool_diagnostics()["google_fallback_used"] is False
sixth = row("China market update six", "Nikkei", "nikkei.com", "GDELT", 8, "https://nikkei.com/6")
reconcile = NewsAgent(now=NOW, providers=[StaticProvider("primary", primary_rows + [sixth])], google_provider=MustNotFetch())
assert len(reconcile.get_global_headlines()) == 5
reconcile_diag = reconcile.get_pool_diagnostics()
assert reconcile_diag["eligible_before_caps"] == reconcile_diag["selected_count"] + reconcile_diag["cap_dropped_count"]
assert any(item["drop_reason"] == "selection_limit" for item in reconcile.get_dropped_headlines())


# Cross-provider syndication and publisher cap: one Reuters story, one Yahoo.
duplicates = [
    row(
        "Fed decision moves markets - Reuters",
        "Reuters", "reuters.com", "FMP", 13,
        "https://reuters.com/story?utm_source=fmp",
    ),
    row(
        "Fed decision moves markets - Reuters",
        "Reuters", "reuters.com", "Alpha Vantage News", 13,
        "https://reuters.com/story",
    ),
    row(
        "Tesla market update - Yahoo Finance",
        "Yahoo Finance", "yahoo.com", "FMP", 12,
        "https://finance.yahoo.com/tesla",
    ),
    row(
        "Palantir market update - Yahoo Finance",
        "Yahoo Finance", "yahoo.com", "GDELT", 11,
        "https://finance.yahoo.com/pltr",
    ),
]
agent6 = NewsAgent(
    now=NOW,
    providers=[StaticProvider("primary", duplicates)],
    google_provider=GoogleNewsProvider(
        now=NOW,
        request_get=lambda *a, **k: Response(EMPTY),
    ),
)
agent6.get_global_headlines()
diag6 = agent6.get_pool_diagnostics()
assert diag6["dedupe_dropped_count"] == 1
assert diag6["cap_dropped_count"] == 1
assert diag6["selected_publisher_counts"] == {
    "reuters.com": 1,
    "yahoo.com": 1,
}
assert diag6["selected_publisher_max_share"] == 0.5
semantic_dash_rows = [
    row("Tesla recalls vehicles - battery fault", "Reuters", "reuters.com", "FMP", 13, "https://reuters.com/battery"),
    row("Tesla recalls vehicles - software update", "Reuters", "reuters.com", "FMP", 12, "https://reuters.com/software"),
]
semantic_scored = NewsAgent._select_relevant(semantic_dash_rows, top_n=None)
semantic_fresh, semantic_dropped = NewsAgent._deduplicate(semantic_scored)
assert len(semantic_fresh) == 2
assert semantic_dropped == []
publisher_suffix = dict(semantic_dash_rows[0])
publisher_suffix["title"] = "Tesla recalls vehicles - Reuters"
publisher_suffix_copy = dict(publisher_suffix)
publisher_suffix_copy["link"] = "https://other.example/reuters-copy"
assert len(NewsAgent._deduplicate(NewsAgent._select_relevant([publisher_suffix, publisher_suffix_copy], top_n=None))[0]) == 1

# One failed provider does not poison usable rows; failures stay explicit.
agent7 = NewsAgent(
    now=NOW,
    providers=[
        StaticProvider("broken", [], status="error", error="malformed JSON"),
        StaticProvider("working", primary_rows),
    ],
    google_provider=MustNotFetch(),
)
assert len(agent7.get_global_headlines()) == 5
diag7 = agent7.get_pool_diagnostics()
assert diag7["fetch_error"] is None
assert diag7["provider_failures"] == ["broken"]


class ExplodingProvider:
    name = "Exploding API"
    api_key = "super-secret-key"

    def fetch(self):
        raise RuntimeError("apikey=super-secret-key token=second-secret")


agent8 = NewsAgent(
    now=NOW,
    providers=[ExplodingProvider()],
    google_provider=GoogleNewsProvider(
        now=NOW, request_get=lambda *a, **k: Response(EMPTY)
    ),
)
agent8.get_global_headlines()
serialized_error = str(agent8.get_pool_diagnostics()["providers"])
print("Headline relevance, diversity, dedupe, and fail-soft checks passed")
assert "super-secret-key" not in serialized_error
assert "second-secret" not in serialized_error
assert "[redacted]" in serialized_error
