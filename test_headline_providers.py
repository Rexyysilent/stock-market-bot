"""Offline regression checks for normalized headline-source adapters."""

from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, ".")

from config import _positive_int_env
from agents.news_providers import (
    BatchedGDELTNewsProvider,
    AlphaVantageNewsProvider,
    FMPNewsProvider,
    GDELTNewsProvider,
    GoogleNewsProvider,
    OfficialFeedProvider,
    ProviderResult,
    canonicalize_url,
    normalize_domain,
    normalize_publisher,
)


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, *, status=200, content=b"", payload=None):
        self.status_code = status
        self.content = content

        self._payload = payload
        self.text = content.decode("utf-8", errors="replace")

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


os.environ["HEADLINE_TIMEOUT_TEST"] = "not-a-number"
assert _positive_int_env("HEADLINE_TIMEOUT_TEST", 12) == 12
os.environ["HEADLINE_TIMEOUT_TEST"] = "0"
assert _positive_int_env("HEADLINE_TIMEOUT_TEST", 12) == 12
os.environ["HEADLINE_TIMEOUT_TEST"] = "15"
assert _positive_int_env("HEADLINE_TIMEOUT_TEST", 12) == 15
del os.environ["HEADLINE_TIMEOUT_TEST"]

EXPECTED_FIELDS = {
    "title",
    "link",
    "canonical_url",
    "published",
    "source_time_kind",
    "provider_seen_at",
    "provider",
    "publisher",
    "publisher_domain",
    "source_class",
    "source_record_id",
    "tickers",
    "summary",
}


# --- Common result and normalization helpers
result = ProviderResult("Fixture", "ok", [{"title": "one"}], metadata={"page": 1})
assert result.health() == {
    "name": "Fixture",
    "status": "ok",
    "configured": True,
    "record_count": 1,
    "error": None,
    "page": 1,
}

assert normalize_domain("https://finance.yahoo.com/news/x") == "yahoo.com"
assert normalize_domain("NEWS.YAHOO.COM") == "yahoo.com"
assert normalize_domain("https://www.reuters.com/world/") == "reuters.com"
assert normalize_domain("https://news.example.co.uk/a") == "news.example.co.uk"
assert canonicalize_url(
    "HTTPS://Example.com/a/?utm_source=x&b=2&a=1#fragment"
) == "https://example.com/a?a=1&b=2"
assert normalize_publisher("Yahoo Finance", "https://finance.yahoo.com/x") == (
    "Yahoo Finance",
    "yahoo.com",
)
assert normalize_publisher(None, None, "Market rises - Reuters") == (
    "Reuters",
    "reuters.com",
)


# --- Official provider parses both RSS and Atom and isolates a bad feed
RSS = b"""<?xml version='1.0'?><rss><channel><item>
<title>Federal Reserve issues FOMC statement</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/a.htm?utm_source=rss</link>
<pubDate>Thu, 13 Aug 2026 10:00:00 GMT</pubDate>
<guid>fed-1</guid><description><![CDATA[<p>Policy statement.</p>]]></description>
</item><item>
<title>Malformed sibling should not erase the valid release</title>
<link>https://official.test:bad/release</link>
<pubDate>Thu, 13 Aug 2026 10:01:00 GMT</pubDate>
<guid>bad-url</guid>
</item></channel></rss>"""

ATOM = b"""<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom'><entry>
<title>SEC charges Example Corp</title>
<link rel='alternate' href='https://www.sec.gov/newsroom/press-releases/2026-1'/>
<published>2026-08-13T09:30:00-04:00</published>
<id>sec-1</id><summary>Enforcement release.</summary>
</entry></feed>"""


def official_get(url, **kwargs):
    if url.endswith("fed.xml"):
        return Response(content=RSS)
    if url.endswith("sec.atom"):
        return Response(content=ATOM)
    return Response(status=503)


official = OfficialFeedProvider(
    [
        {
            "url": "https://official.test/fed.xml",
            "publisher": "Federal Reserve",
            "publisher_url": "https://www.federalreserve.gov/",
        },
        {
            "url": "https://official.test/sec.atom",
            "publisher": "SEC",
            "publisher_url": "https://www.sec.gov/",
        },
        {"url": "https://official.test/broken.xml", "publisher": "FDA"},
    ],
    now=NOW,
    request_get=official_get,
).fetch()

assert official.status == "ok"
assert len(official.records) == 2
assert official.metadata["partial_failures"] == 1
assert all(set(row) == EXPECTED_FIELDS for row in official.records)
assert official.records[0]["publisher"] == "Federal Reserve"
assert official.records[0]["publisher_domain"] == "federalreserve.gov"
assert official.records[0]["canonical_url"].endswith("pressreleases/a.htm")
assert official.records[0]["summary"] == "Policy statement."
assert official.records[1]["publisher"] == "SEC"
assert official.records[1]["published"] == "2026-08-13T09:30:00-04:00"


# --- Nasdaq custom tags become a readable official record
NASDAQ = b"""<?xml version='1.0'?><rss xmlns:n='http://www.nasdaqtrader.com/'>
<channel><item><title>TSLA</title>
<link>https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts</link>
<pubDate>Thu, 13 Aug 2026 14:31:00 GMT</pubDate>
<n:IssueSymbol>TSLA</n:IssueSymbol><n:IssueName>Tesla Inc.</n:IssueName>
<n:HaltDate>08/13/2026</n:HaltDate><n:HaltTime>10:31:00.000</n:HaltTime>
<n:ReasonCode>T1</n:ReasonCode>
</item><item><title>ROKU</title>
<link>https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts</link>
<pubDate>Thu, 13 Aug 2026 14:32:00 GMT</pubDate>
<n:IssueSymbol>ROKU</n:IssueSymbol><n:IssueName>Roku Inc.</n:IssueName>
<n:HaltDate>08/13/2026</n:HaltDate><n:HaltTime>10:32:00.250</n:HaltTime>
<n:ReasonCode>T2</n:ReasonCode>
</item></channel></rss>"""

nasdaq = OfficialFeedProvider(
    [{
        "url": "https://nasdaq.test/halts.xml",
        "publisher": "Nasdaq Trader",
        "publisher_url": "https://www.nasdaqtrader.com/",
        "parser": "nasdaq_halts",
    }],
    now=NOW,
    request_get=lambda *a, **k: Response(content=NASDAQ),
).fetch()

assert nasdaq.status == "ok" and len(nasdaq.records) == 2
nasdaq_row = nasdaq.records[0]
assert nasdaq_row["title"] == "TSLA (Tesla Inc.) trading halt - T1"
assert nasdaq_row["tickers"] == ["TSLA"]
assert nasdaq_row["published"] == "2026-08-13T10:31:00-04:00"
assert nasdaq_row["source_time_kind"] == "event_time"
assert nasdaq_row["publisher_domain"] == "nasdaqtrader.com"
assert "halt=08/13/2026 10:31:00" in nasdaq_row["summary"]


assert {row["canonical_url"] for row in nasdaq.records} == {None}
assert len({row["source_record_id"] for row in nasdaq.records}) == 2
assert nasdaq.records[1]["published"] == "2026-08-13T10:32:00.250000-04:00"

# --- FMP: missing key skips without a call; valid rows normalize publisher
network_calls = []
skipped_fmp = FMPNewsProvider(
    None,
    request_get=lambda *a, **k: network_calls.append((a, k)),
).fetch()
assert skipped_fmp.status == "skipped" and skipped_fmp.configured is False
assert network_calls == []

fmp_payload = [{
    "id": "fmp-1",
    "symbol": "TSLA",
    "publishedDate": "2026-08-13 10:20:00",
    "title": "Tesla shares rise after update",
    "url": "https://finance.yahoo.com/news/tesla-update?utm_medium=feed",
    "site": "Yahoo Finance",
    "text": "Company update.",
}, {
    "id": "fmp-bad",
    "symbol": "ROKU",
    "publishedDate": "2026-08-13 10:21:00",
    "title": "Roku earnings update",
    "url": "https://example.com:bad/roku",
}]
fmp_call = {}


def fmp_get(url, **kwargs):
    fmp_call.update({"url": url, **kwargs})
    return Response(payload=fmp_payload)


fmp = FMPNewsProvider(
    "fmp-secret",
    tickers=["TSLA", "ROKU"],
    now=NOW,
    request_get=fmp_get,
).fetch()
assert fmp.status == "ok" and len(fmp.records) == 1
assert "apikey" not in fmp_call["params"]
assert fmp_call["headers"]["apikey"] == "fmp-secret"
assert "fmp-secret" not in fmp_call["url"]
assert fmp.records[0]["publisher"] == "Yahoo Finance"
assert fmp.records[0]["publisher_domain"] == "yahoo.com"
assert fmp.records[0]["source_class"] == "api_news_discovery"
assert fmp.records[0]["tickers"] == ["TSLA"]
assert fmp.metadata["malformed_record_count"] == 1
assert fmp.records[0]["published"] == "2026-08-13 10:20:00"


# Basic FMP accounts receive 402 for Stock News; use the narrower Articles feed.
fmp_articles_payload = [{
    "title": "Roku earnings analysis",
    "date": "2026-08-13 11:15:03",
    "content": "<p>Roku quarterly earnings analysis.</p>",
    "tickers": "NASDAQ:ROKU",
    "link": "https://financialmodelingprep.com/market-news/roku-analysis",
    "author": "FMP Staff",
    "site": "Financial Modeling Prep",
}]
fmp_fallback_calls = []


def fmp_fallback_get(url, **kwargs):
    fmp_fallback_calls.append((url, kwargs))
    if url.endswith("/news/stock"):
        return Response(status=402)
    return Response(payload=fmp_articles_payload)


fmp_fallback = FMPNewsProvider(
    "fmp-basic-key",
    tickers=["ROKU"],
    now=NOW,
    request_get=fmp_fallback_get,
).fetch()
assert fmp_fallback.status == "ok"
assert len(fmp_fallback.records) == 1
assert [call[0].rsplit("/", 1)[-1] for call in fmp_fallback_calls] == [
    "stock",
    "fmp-articles",
]
assert "symbols" in fmp_fallback_calls[0][1]["params"]
assert "symbols" not in fmp_fallback_calls[1][1]["params"]
assert fmp_fallback.metadata["fallback_used"] is True
assert fmp_fallback.metadata["coverage"] == "fmp_articles"
assert fmp_fallback.metadata["primary_status_code"] == 402
assert fmp_fallback.records[0]["published"] == "2026-08-13 11:15:03"
assert fmp_fallback.records[0]["tickers"] == ["ROKU"]
assert fmp_fallback.records[0]["publisher"] == "Financial Modeling Prep"
assert fmp_fallback.records[0]["summary"] == "Roku quarterly earnings analysis."
assert all(
    call[1]["headers"]["apikey"] == "fmp-basic-key"
    for call in fmp_fallback_calls
)
fmp_bad = FMPNewsProvider(
    "do-not-leak",
    request_get=lambda *a, **k: Response(payload={"Error Message": "bad do-not-leak"}),
).fetch()
assert fmp_bad.status == "error"
assert "do-not-leak" not in (fmp_bad.error or "")


# --- Alpha Vantage: parse ticker sentiment and detect HTTP-200 quota messages
av_payload = {
    "items": "1",
    "feed": [{
        "title": "Reuters: Chip stocks gain",
        "url": "https://www.reuters.com/technology/chips/?utm_campaign=x",
        "time_published": "20260813T104500",
        "source": "Reuters",
        "source_domain": "www.reuters.com",
        "summary": "Semiconductor shares advanced.",
        "ticker_sentiment": [{"ticker": "AMAT"}, {"ticker": "VRT"}],
    }, {
        "title": "Malformed sibling",
        "url": "https://example.com:bad/news",
        "time_published": "20260813T104600",
        "source": "Example",
        "source_domain": "example.com",
        "summary": "Bad URL must not erase Reuters.",
        "ticker_sentiment": [{"ticker": "ROKU"}],
    }],
}
av = AlphaVantageNewsProvider(
    "av-secret",
    tickers=["AMAT", "VRT"],
    topics=["financial_markets"],
    now=NOW,
    request_get=lambda *a, **k: Response(payload=av_payload),
).fetch()
assert av.status == "ok" and len(av.records) == 1
assert av.metadata["malformed_record_count"] == 1
assert av.records[0]["publisher"] == "Reuters"
assert av.records[0]["publisher_domain"] == "reuters.com"
assert av.records[0]["tickers"] == ["AMAT", "VRT"]
assert av.records[0]["published"] == "2026-08-13T10:45:00Z"
assert av.records[0]["source_class"] == "api_news_discovery"

av_limited = AlphaVantageNewsProvider(
    "av-secret",
    request_get=lambda *a, **k: Response(
        payload={"Note": "Thank you for using Alpha Vantage; rate limit reached."}
    ),
).fetch()
assert av_limited.status == "error"
assert "api_error" in (av_limited.error or "")


# --- GDELT: seendate is observation provenance, never publication time
gdelt_payload = {
    "articles": [{
        "title": "Copper rises as supply tightens",
        "url": "https://www.reuters.com/markets/copper?utm_source=gdelt",
        "seendate": "20260813T110000Z",
        "domain": "www.reuters.com",
    }, {
        "title": "Malformed sibling",
        "url": "https://example.com:bad/news",
        "seendate": "20260813T110100Z",
        "domain": "example.com",
    }]
}
gdelt = GDELTNewsProvider(
    "copper OR uranium",
    now=NOW,
    request_get=lambda *a, **k: Response(payload=gdelt_payload),
).fetch()
assert gdelt.status == "ok" and len(gdelt.records) == 1
assert gdelt.metadata["malformed_record_count"] == 1
gdelt_row = gdelt.records[0]
assert gdelt_row["published"] is None
assert gdelt_row["source_time_kind"] == "provider_seen"
assert gdelt_row["provider_seen_at"] == "2026-08-13T11:00:00Z"
assert gdelt.metadata["timestamp_policy"] == "seendate_is_provider_seen_not_published"


class RetryResponse(Response):
    def __init__(self, *, status=200, payload=None, retry_after=None):
        super().__init__(status=status, payload=payload)
        self.headers = {}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after


retry_responses = iter([
    RetryResponse(status=429, retry_after="0"),
    RetryResponse(payload=gdelt_payload),
])
retry_sleeps = []
gdelt_recovered = GDELTNewsProvider(
    "markets",
    now=NOW,
    request_get=lambda *a, **k: next(retry_responses),
    sleep_fn=retry_sleeps.append,
).fetch()
assert gdelt_recovered.status == "ok"
assert gdelt_recovered.metadata["attempt_count"] == 2
assert gdelt_recovered.metadata["transient_retries"] == 1
assert retry_sleeps == [0.0]
default_retry_responses = iter([
    RetryResponse(status=429),
    RetryResponse(payload=gdelt_payload),
])
default_retry_sleeps = []
gdelt_default_recovered = GDELTNewsProvider(
    "markets",
    now=NOW,
    request_get=lambda *a, **k: next(default_retry_responses),
    sleep_fn=default_retry_sleeps.append,
).fetch()
assert gdelt_default_recovered.status == "ok"
assert default_retry_sleeps == [5.0]

gdelt_missing_time = GDELTNewsProvider(
    "markets",
    now=NOW,
    request_get=lambda *a, **k: Response(payload={
        "articles": [{
            "title": "Fed market update",
            "url": "https://example.com/fed",
            "domain": "example.com",
        }]
    }),
).fetch()
assert gdelt_missing_time.records[0]["provider_seen_at"] is None



# --- GDELT batches are sequential, fail soft per batch, and dedupe deterministically
batch_responses = iter([
    RetryResponse(payload=gdelt_payload),
    RetryResponse(status=200, payload={"articles": [gdelt_payload["articles"][0]]}),
])
batch_sleeps = []
gdelt_batched = BatchedGDELTNewsProvider(
    ("copper sourcelang:english", "uranium sourcelang:english"),
    now=NOW,
    request_get=lambda *a, **k: next(batch_responses),
    sleep_fn=batch_sleeps.append,
    batch_delay_seconds=5,
).fetch()
assert gdelt_batched.status == "ok"
assert len(gdelt_batched.records) == 1
assert gdelt_batched.metadata["query_count"] == 2
assert gdelt_batched.metadata["query_lengths"] == [
    len("copper sourcelang:english"),
    len("uranium sourcelang:english"),
]
assert batch_sleeps == [5.0]


class TextResponse(Response):
    def __init__(self, text, status=200):
        super().__init__(status=status, content=text.encode("utf-8"))
        self.headers = {}

    def json(self):
        raise ValueError("not JSON")


partial_batch_responses = iter([
    TextResponse("Your query was too short or too long."),
    RetryResponse(payload=gdelt_payload),
])
partial_sleeps = []
gdelt_partial = BatchedGDELTNewsProvider(
    ("bad sourcelang:english", "copper sourcelang:english"),
    now=NOW,
    request_get=lambda *a, **k: next(partial_batch_responses),
    sleep_fn=partial_sleeps.append,
).fetch()
assert gdelt_partial.status == "ok"
assert gdelt_partial.metadata["partial_failures"] == 1
assert gdelt_partial.metadata["batches"][0]["error"] == (
    "non_json_response: Your query was too short or too long."
)
assert partial_sleeps == [5.0]

all_bad = BatchedGDELTNewsProvider(
    ("bad sourcelang:english",),
    now=NOW,
    request_get=lambda *a, **k: TextResponse("bad body"),
    sleep_fn=lambda *a: None,
).fetch()
assert all_bad.status == "error"
assert "non_json_response: bad body" in (all_bad.error or "")

gdelt_bad = GDELTNewsProvider(
    "markets",
    request_get=lambda *a, **k: Response(content=b"not json"),
).fetch()
assert gdelt_bad.status == "error" and gdelt_bad.records == []


# --- Google: parse underlying publisher and preserve targeted fallback metadata
EMPTY = b"<?xml version='1.0'?><rss><channel></channel></rss>"
GOOGLE = b"""<?xml version='1.0'?><rss><channel><item>
<title>Treasury yields rise after auction - Yahoo Finance</title>
<link>https://news.google.com/rss/articles/opaque?oc=5</link>
<pubDate>Thu, 13 Aug 2026 11:30:00 GMT</pubDate>
<guid>google-1</guid>
<source url='https://finance.yahoo.com'>Yahoo Finance</source>
</item></channel></rss>"""
google_calls = []


def google_get(url, **kwargs):
    google_calls.append(url)
    return Response(content=EMPTY if len(google_calls) == 1 else GOOGLE)


google_provider = GoogleNewsProvider(
    primary_query="broad",
    fallback_queries=("targeted",),
    now=NOW,
    request_get=google_get,
).fetch()
assert google_provider.status == "ok"
assert google_provider.metadata["fallback_used"] is True
assert google_provider.metadata["query"] == "targeted"
assert [row["record_count"] for row in google_provider.metadata["attempts"]] == [0, 1]
google_row = google_provider.records[0]
assert google_row["publisher"] == "Yahoo Finance"
assert google_row["publisher_domain"] == "yahoo.com"
assert google_row["canonical_url"] == "https://news.google.com/rss/articles/opaque"
assert google_row["provider"] == "Google News"

direct_records, direct_error = GoogleNewsProvider(
    now=NOW,
    request_get=lambda *a, **k: Response(content=GOOGLE),
).fetch_query("one query", 1)
assert direct_error is None and len(direct_records) == 1

print("Headline provider adapter checks passed")
