"""Offline regression: a Nitter instance must not choose the collector's next request.

Both the availability probe and the per-account RSS fetch used to follow
redirects, so an instance could send the collector to any host it can reach
(including private addresses), and the probe credited the final 200 to the
configured instance. Redirects are now refused and count as unavailability.
"""
from datetime import datetime, timezone

import agents.twitter_agent as twitter_module
from agents.twitter_agent import TwitterAgent


FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>market post</title><link>https://good.invalid/a/1</link>
<pubDate>Wed, 29 Jul 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""
INTERNAL = "http://169.254.169.254/latest/meta-data/"


class _Resp:
    def __init__(self, status_code, content=b"", location=None):
        self.status_code = status_code
        self.content = content
        self.headers = {"Location": location} if location else {}


def run(routes, instances, accounts):
    """routes: url prefix -> response. Emulates Requests' redirect following."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        for prefix, response in routes.items():
            if url.startswith(prefix):
                break
        else:
            response = _Resp(404)
        if 300 <= response.status_code < 400 and kwargs.get("allow_redirects", True):
            return fake_get(response.headers["Location"], **kwargs)
        return response

    saved = (twitter_module.requests.get, twitter_module.NITTER_INSTANCES,
             twitter_module.TWITTER_ACCOUNTS)
    twitter_module.requests.get = fake_get
    twitter_module.NITTER_INSTANCES = instances
    twitter_module.TWITTER_ACCOUNTS = accounts
    try:
        agent = TwitterAgent(now=datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc))
        tweets = agent.get_twitter_via_nitter(max_accounts=len(accounts))
    finally:
        (twitter_module.requests.get, twitter_module.NITTER_INSTANCES,
         twitter_module.TWITTER_ACCOUNTS) = saved
    return agent, tweets, calls


# 1. Probe: a redirecting instance is not "available", and its target is never
#    contacted; the next configured instance is used instead.
agent, tweets, calls = run(
    {
        "https://evil.invalid/": _Resp(302, location=INTERNAL),
        INTERNAL: _Resp(200, FEED),
        "https://good.invalid/": _Resp(200, FEED),
    },
    ["https://evil.invalid", "https://good.invalid"],
    ["alpha"],
)
assert all(kwargs.get("allow_redirects") is False for _url, kwargs in calls), calls
assert not any(url.startswith(INTERNAL) for url, _kwargs in calls), calls
assert agent.health["nitter_instance"] == "https://good.invalid"
assert agent.health["status_counts"]["nitter_probe:302"] == 1
assert len(tweets) == 1

# 2. A redirect that would land on a 200 elsewhere is not credited to the
#    configured instance: no instance is available, the fallback path applies.
agent, tweets, calls = run(
    {"https://evil.invalid/": _Resp(301, location=INTERNAL), INTERNAL: _Resp(200, FEED)},
    ["https://evil.invalid"],
    ["alpha"],
)
assert not any(url.startswith(INTERNAL) for url, _kwargs in calls), calls
assert agent.health["nitter_available"] is False
assert agent.health.get("nitter_instance") in (None, "")
assert tweets == []

# 3. Feed fetch: after a good probe, an account feed that redirects is an
#    account failure, not a request to the redirect target.
routes = {
    "https://good.invalid/alpha/rss": _Resp(200, FEED),
    "https://good.invalid/beta/rss": _Resp(307, location=INTERNAL),
    INTERNAL: _Resp(200, FEED),
}
agent, tweets, calls = run(routes, ["https://good.invalid"], ["alpha", "beta"])
assert all(kwargs.get("allow_redirects") is False for _url, kwargs in calls), calls
assert not any(url.startswith(INTERNAL) for url, _kwargs in calls), calls
assert {(row["account"], row["reason"]) for row in agent.health["nitter_account_failures"]} == {
    ("beta", "http_307"),
}
assert [t["account"] for t in tweets] == ["@alpha"]

print("Nitter redirect checks passed")
