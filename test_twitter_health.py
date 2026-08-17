"""Offline regression: partial Nitter account coverage must be a WARN."""
from datetime import datetime, timezone

import agents.twitter_agent as twitter_module
from agents.twitter_agent import TwitterAgent


FEED_TEMPLATE = """<?xml version="1.0"?><rss><channel>
<item><title>{account} market post</title>
<link>https://fixture/{account}/1</link>
<pubDate>Wed, 29 Jul 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""


class _Resp:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


accounts = [
    "DeItaone",
    "unusual_whales",
    "zabormetrics",
    "WallStJesus",
    "mcaborern",
]
deitaone_calls = 0


def fake_get(url, **_kwargs):
    global deitaone_calls
    account = url.rstrip("/").split("/")[-2]
    if account == "DeItaone":
        deitaone_calls += 1
        # Instance probe succeeds, but the actual account fetch rate-limits.
        if deitaone_calls == 1:
            return _Resp(
                200,
                FEED_TEMPLATE.format(account=account).encode(),
            )
        return _Resp(429)
    if account in {"unusual_whales", "WallStJesus"}:
        return _Resp(
            200,
            FEED_TEMPLATE.format(account=account).encode(),
        )
    return _Resp(404)


real_get = twitter_module.requests.get
real_instances = twitter_module.NITTER_INSTANCES
real_accounts = twitter_module.TWITTER_ACCOUNTS
twitter_module.requests.get = fake_get
twitter_module.NITTER_INSTANCES = ["https://fixture"]
twitter_module.TWITTER_ACCOUNTS = accounts
try:
    agent = TwitterAgent(
        now=datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
    )
    rows = agent.get_twitter_intel(max_queries=5, prefer_nitter=True)
finally:
    twitter_module.requests.get = real_get
    twitter_module.NITTER_INSTANCES = real_instances
    twitter_module.TWITTER_ACCOUNTS = real_accounts

health = agent.health
assert len(rows) == 2
assert health["status"] == "WARN"
assert health["fallback_used"] is False
assert health["nitter_accounts_expected"] == 5
assert health["nitter_accounts_http_ok"] == 2
assert health["nitter_accounts_with_items"] == 2
assert {
    (row["account"], row["reason"])
    for row in health["nitter_account_failures"]
} == {
    ("DeItaone", "http_429"),
    ("zabormetrics", "http_404"),
    ("mcaborern", "http_404"),
}

print("Twitter partial-account health checks passed")
