"""Regression checks for Reddit RSS fallback when public JSON is blocked."""
import os
import sys

sys.path.insert(0, ".")

import agents.social_agent as social_module
from agents.social_agent import SocialAgent


class FakeResponse:
    def __init__(self, status_code, content=b"", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload or {}

    def json(self):
        return self._payload


atom = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Daily Discussion Thread</title>
  </entry>
  <entry>
    <title>Small caps rotate higher as yields cool</title>
  </entry>
</feed>
"""

calls = []


def fake_get(url, headers=None, timeout=None):
    calls.append(url)
    if "/hot.rss" in url:
        return FakeResponse(200, atom)
    if "/hot/.rss" in url:
        return FakeResponse(200, atom)
    raise AssertionError(f"unexpected URL: {url}")


original_get = social_module.requests.get
original_client_id = os.environ.get("REDDIT_CLIENT_ID")
original_client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

try:
    os.environ.pop("REDDIT_CLIENT_ID", None)
    os.environ.pop("REDDIT_CLIENT_SECRET", None)
    social_module.requests.get = fake_get

    agent = SocialAgent()
    rows = agent._fetch_reddit("stocks", "hot", limit=3)

    assert rows == [
        "[r/stocks RSS] Small caps rotate higher as yields cool (via Reddit RSS)"
    ]
    assert "/hot.rss" in calls[0]
    assert agent.health["reddit_mode"] == "rss"
    assert agent.health["reddit_requests"] == 0
    assert agent.health["reddit_rss_requests"] == 1
    assert agent.health["reddit_status_counts"] == {"r/stocks/hot/rss:200": 1}
    assert agent.health["reddit_failures"] == []
finally:
    social_module.requests.get = original_get
    if original_client_id is not None:
        os.environ["REDDIT_CLIENT_ID"] = original_client_id
    if original_client_secret is not None:
        os.environ["REDDIT_CLIENT_SECRET"] = original_client_secret

print("Reddit RSS fallback regression checks passed")
