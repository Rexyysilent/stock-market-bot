"""Offline regression for CEO.ca provenance, qualification, and quality gates."""
from datetime import datetime, timezone

import agents.research_agent as research_module
from agents.research_agent import ResearchAgent


NOW = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
FRESH = "2026-07-29T12:00:00Z"


def direct(agent, ticker, text, suffix, date=FRESH):
    return agent._build_ceo_signal(
        ticker,
        f"CEO.ca/{ticker}",
        "ceo.ca_api",
        text,
        f"https://ceo.ca/{ticker.lower()}?{suffix}",
        date,
        text,
        source_record_id=suffix,
    )


agent = ResearchAgent(now=NOW)

# Provenance and content qualification are independent.
qualified = direct(
    agent,
    "NXE",
    "Assays returned 1.5% U3O8 across 8.0 m @ the Arrow deposit",
    "qualified",
)
assert qualified["source_record_verified"] is True
assert qualified["provenance_status"] == "direct_api_record"
assert qualified["threshold_qualified"] is True
assert qualified["verified"] is True  # compatibility alias only
assert qualified["content_class"] == "geology_threshold"

context = direct(
    agent,
    "CCJ",
    "Uranium conversion capacity and nuclear fuel-cycle update",
    "context",
)
assert context["source_record_verified"] is True
assert context["threshold_qualified"] is False
assert context["content_class"] == "context_only"
assert context["context_relevant"] is True

banter = direct(agent, "NXE", "LOL enjoy the day", "banter")
assert banter["source_record_verified"] is True
assert banter["threshold_qualified"] is False
assert banter["content_class"] == "irrelevant_chatter"
assert banter["context_relevant"] is False

proxy = agent._build_ceo_signal(
    "UUUU",
    "CEO.ca/UUUU",
    "google_news_proxy",
    "Uranium permitting update",
    "https://news.example/1",
    FRESH,
    "Uranium permitting update",
)
assert proxy["source_record_verified"] is False
assert proxy["provenance_status"] == "news_proxy_record"
assert proxy["content_class"] == "context_only"


# The direct API record contract requires a valid payload, post ID, and time.
class Response:
    status_code = 200

    @staticmethod
    def json():
        return {
            "spiels": [{
                "spiel": "7.0 m @ 1.2% U3O8",
                "timestamp": 1785326400000,
                "spiel_id": "api-record",
                "name": "@fixture",
                "votes": 3,
            }]
        }


real_get = research_module.requests.get
research_module.requests.get = lambda *_args, **_kwargs: Response()
try:
    api_rows = agent._fetch_ceo_ca_channel("NXE", limit=5)
finally:
    research_module.requests.get = real_get

assert len(api_rows) == 1
assert api_rows[0]["source_record_id"] == "api-record"
assert api_rows[0]["source_record_verified"] is True
assert api_rows[0]["threshold_qualified"] is True


# End-to-end collection: freshness first, then relevance, then the per-source
# context cap. Threshold-qualified rows are never displaced by newer chatter.
def fake_channel(ticker, limit=5):
    if ticker == "UUUU":
        return [
            direct(agent, ticker, f"Uranium production update {i}", f"u{i}")
            for i in range(4)
        ]
    if ticker == "CCJ":
        return [
            direct(agent, ticker, "have a nice day lol", "c-junk"),
            direct(agent, ticker, "Cigar Lake uranium mine update", "c-good"),
        ]
    if ticker == "NXE":
        return [qualified]
    return [
        direct(
            agent,
            ticker,
            "Wheeler River uranium permitting update",
            "d-stale",
            date="2026-07-01T12:00:00Z",
        )
    ]


sector = agent._build_ceo_signal(
    "SECTOR",
    "Uranium/Mining",
    "google_news_proxy",
    "Nuclear fuel-cycle investment rises",
    "https://news.example/sector",
    FRESH,
    "Nuclear fuel-cycle investment rises",
)
agent._fetch_ceo_ca_channel = fake_channel
agent._fetch_uranium_sector_news = lambda: [sector]

rows = agent.get_ceo_ca_signals()
assert len(rows) == 5, rows
assert sum(row["threshold_qualified"] for row in rows) == 1
assert sum(row["source_record_verified"] for row in rows) == 4
assert len([row for row in rows if row["ticker"] == "UUUU"]) == 2
assert not any("nice day" in row["title"] for row in rows)

freshness_drops = agent.get_dropped_ceo_signals()
assert len(freshness_drops) == 1
assert freshness_drops[0]["ticker"] == "DNN"
assert freshness_drops[0]["drop_reason"] == "stale"

quality_drops = agent.get_dropped_ceo_quality()
assert sum(row["drop_reason"] == "irrelevant_chatter" for row in quality_drops) == 1
assert sum(row["drop_reason"] == "context_cap" for row in quality_drops) == 2

health = agent.get_ceo_ca_health()
assert health["expected_channels"] == ["UUUU", "CCJ", "NXE", "DNN"]
assert health["api_channels"] == ["UUUU", "CCJ", "NXE", "DNN"]
assert health["fallback_channels"] == []
assert health["records_raw"] == 9
assert health["records_fresh"] == 8
assert health["records_rendered"] == 5
assert health["direct_api_records_seen"] == 8
assert health["direct_api_records_verified"] == 8
assert health["incomplete_direct_api_records"] == 0
assert health["rendered_direct_api_records_verified"] == 4
assert health["threshold_qualified_records"] == 1
assert health["irrelevant_chatter_dropped"] == 1
assert health["context_cap_dropped"] == 2
assert health["freshness_dropped"] == 1

print("CEO.ca provenance and content-quality checks passed")
