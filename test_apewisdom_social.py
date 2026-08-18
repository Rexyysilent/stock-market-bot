"""Regression checks for ApeWisdom social ticker heat integration."""
import os
import sys

sys.path.insert(0, ".")

import agents.social_agent as social_module
from agents.social_agent import SocialAgent


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


calls = []


def fake_get(url, headers=None, timeout=None):
    calls.append(url)
    if url == "https://apewisdom.io/api/v1.0/filter/4chan":
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "rank": "1",
                        "ticker": "MU",
                        "name": "Micron Technology",
                        "mentions": "14",
                        "upvotes": "0",
                        "rank_24h_ago": "1",
                        "mentions_24h_ago": "15",
                    },
                ]
            },
        )
    assert url == "https://apewisdom.io/api/v1.0/filter/all-stocks"
    return FakeResponse(
        200,
        {
            "results": [
                {
                    "rank": "1",
                    "ticker": "TSLA",
                    "name": "Tesla",
                    "mentions": "100",
                    "upvotes": "1,250",
                    "rank_24h_ago": "3",
                    "mentions_24h_ago": "70",
                },
            ]
        },
    )


original_get = social_module.requests.get
original_client_id = os.environ.get("REDDIT_CLIENT_ID")
original_client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

try:
    os.environ.pop("REDDIT_CLIENT_ID", None)
    os.environ.pop("REDDIT_CLIENT_SECRET", None)
    social_module.requests.get = fake_get

    agent = SocialAgent()
    rows = agent._fetch_apewisdom("all-stocks", limit=1)

    # Data layer: structured fields, no formatted strings
    assert rows == [
        {
            "source": "ApeWisdom",
            "filter": "all-stocks",
            "ticker": "TSLA",
            "name": "Tesla",
            "rank": 1,
            "mentions": 100,
            "upvotes": 1250,
            "mentions_24h_ago": 70,
            "rank_24h_ago": 3,
            "mention_velocity_24h": 30,
            "rank_delta_24h": 2,
            # upvotes / max(mentions, 1) = 1250 / 100
            "attention_score": 12.5,
            "is_low_volume": False,
        }
    ]

    # Render layer: same human-readable string as before the split
    assert agent._render_apewisdom_whisper(rows[0]) == (
        "[ApeWisdom/all-stocks] TSLA (Tesla): rank #1, mentions 100, "
        "upvotes 1250 | Mentions 24h: +30 | Rank move: +2"
    )

    # 4chan has no upvote mechanic: attention_score is null, not 0.0
    chan_rows = agent._fetch_apewisdom("4chan", limit=1)
    assert chan_rows[0]["ticker"] == "MU"
    assert chan_rows[0]["upvotes"] == 0
    assert chan_rows[0]["attention_score"] is None
    assert chan_rows[0]["mention_velocity_24h"] == -1
    assert chan_rows[0]["is_low_volume"] is False

    assert calls == [
        "https://apewisdom.io/api/v1.0/filter/all-stocks",
        "https://apewisdom.io/api/v1.0/filter/4chan",
    ]
    assert agent.health["apewisdom_requests"] == 2
    assert agent.health["apewisdom_status_counts"] == {"all-stocks:200": 1, "4chan:200": 1}
    assert agent.health["apewisdom_failures"] == []
    assert agent.health["apewisdom_items"] == 2
finally:
    social_module.requests.get = original_get
    if original_client_id is not None:
        os.environ["REDDIT_CLIENT_ID"] = original_client_id
    if original_client_secret is not None:
        os.environ["REDDIT_CLIENT_SECRET"] = original_client_secret

# ---- Scenario 2: universe-first assembly with pagination -------------------
calls2 = []

PAGE1 = {
    "results": [
        {"rank": "1", "ticker": "TSLA", "name": "Tesla", "mentions": "100",
         "upvotes": "1,250", "rank_24h_ago": "3", "mentions_24h_ago": "70"},
        {"rank": "2", "ticker": "MU", "name": "Micron Technology", "mentions": "90",
         "upvotes": "800", "rank_24h_ago": "2", "mentions_24h_ago": "88"},
    ]
}
PAGE2 = {
    "results": [
        {"rank": "101", "ticker": "RGNX", "name": "Regenxbio", "mentions": "8",
         "upvotes": "2", "rank_24h_ago": "140", "mentions_24h_ago": "3"},
    ]
}
CHAN = {
    "results": [
        {"rank": "1", "ticker": "MU", "name": "Micron Technology", "mentions": "14",
         "upvotes": "0", "rank_24h_ago": "1", "mentions_24h_ago": "15"},
    ]
}


def fake_get_paged(url, headers=None, timeout=None):
    calls2.append(url)
    payloads = {
        "https://apewisdom.io/api/v1.0/filter/all-stocks": PAGE1,
        "https://apewisdom.io/api/v1.0/filter/all-stocks/page/2": PAGE2,
        # empty page: pagination stops here, pages 4-5 never requested
        "https://apewisdom.io/api/v1.0/filter/all-stocks/page/3": {"results": []},
        "https://apewisdom.io/api/v1.0/filter/4chan": CHAN,
    }
    assert url in payloads, f"unexpected URL {url}"
    return FakeResponse(200, payloads[url])


original_client_id = os.environ.get("REDDIT_CLIENT_ID")
original_client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
try:
    os.environ.pop("REDDIT_CLIENT_ID", None)
    os.environ.pop("REDDIT_CLIENT_SECRET", None)
    social_module.requests.get = fake_get_paged

    agent = SocialAgent()
    attention, render = agent._build_social_attention()

    # One row per universe ticker (futures/indices excluded from SOCIAL_UNIVERSE)
    universe_rows = [r for r in attention if r["universe_member"]]
    assert len(universe_rows) == len(SocialAgent.SOCIAL_UNIVERSE)
    by = {r["ticker"]: r for r in universe_rows}
    # Found on page 1 (top of leaderboard)
    assert by["TSLA"]["in_leaderboard"] is True and by["TSLA"]["mentions"] == 100
    # Found deep on page 2 — the whole point of universe-first pagination
    assert by["RGNX"]["in_leaderboard"] is True and by["RGNX"]["mentions"] == 8
    assert by["RGNX"]["rank"] == 101
    # Not found anywhere in the scanned depth: mentions=0 by spec, fields null
    assert by["ROKU"]["in_leaderboard"] is False
    assert by["ROKU"]["mentions"] == 0
    assert by["ROKU"]["rank"] is None

    # Market-color rows kept but flagged non-universe (MU via all-stocks + 4chan)
    mu_rows = [r for r in attention if r["ticker"] == "MU"]
    assert len(mu_rows) == 2
    assert all(r["universe_member"] is False for r in mu_rows)
    assert {r["filter"] for r in mu_rows} == {"all-stocks", "4chan"}

    # Render layer unchanged: top-N market color only, never one line per
    # universe ticker. (The canned leaderboard is only 3 rows, so all of them
    # fall inside the top-15 slice; live pages are ~100 rows.)
    assert [(r["filter"], r["ticker"]) for r in render] == [
        ("all-stocks", "TSLA"), ("all-stocks", "MU"), ("all-stocks", "RGNX"),
        ("4chan", "MU"),
    ]

    # top-200 membership stored for leaderboard-entrance detection
    top200 = agent.get_apewisdom_top200()
    assert [
        {key: row[key] for key in ("ticker", "rank", "mentions")}
        for row in top200
    ] == [
        {"ticker": "TSLA", "rank": 1, "mentions": 100},
        {"ticker": "MU", "rank": 2, "mentions": 90},
        {"ticker": "RGNX", "rank": 101, "mentions": 8},
    ]
    assert all(row["observed_at"].endswith("Z") for row in top200)
    assert len({row["observed_at"] for row in top200}) == 1

    assert calls2 == [
        "https://apewisdom.io/api/v1.0/filter/all-stocks",
        "https://apewisdom.io/api/v1.0/filter/all-stocks/page/2",
        "https://apewisdom.io/api/v1.0/filter/all-stocks/page/3",
        "https://apewisdom.io/api/v1.0/filter/4chan",
    ]
finally:
    social_module.requests.get = original_get
    if original_client_id is not None:
        os.environ["REDDIT_CLIENT_ID"] = original_client_id
    if original_client_secret is not None:
        os.environ["REDDIT_CLIENT_SECRET"] = original_client_secret

print("ApeWisdom social integration regression checks passed")
