"""Offline integration checks for shared OpenInsider consumers.

These checks deliberately feed hostile temporal rows through the SEC consumer.
The canonical source deliberately preserves rows for downstream accounting, so
signal construction must remain fail-closed for every ineligible constituent.
"""
from copy import deepcopy
from datetime import datetime, timezone
import sys

sys.path.insert(0, ".")

import agents.sec_agent as sec_module
from agents.sec_agent import SECAgent
from agents.social_agent import SocialAgent
from ledger.ingest import _iter_signals
import signals


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
NOW_Z = "2026-08-18T12:00:00Z"


class FakeOpenInsider:
    def __init__(
        self,
        rows=None,
        *,
        stale=False,
        insecure_http=False,
        acquisition_status="ok",
        failure_reason=None,
        error=None,
        exception=None,
    ):
        self.rows = list(rows or [])
        self.run_uses_stale_cache = stale
        self.run_uses_insecure_http = insecure_http
        self.has_live_run_data = (
            not stale and not insecure_http and acquisition_status == "ok"
        )
        self.exception = exception
        self.run_calls = []
        self.format_calls = []
        self.health = {
            "acquisition_status": acquisition_status,
            "cache_used": stale,
            "cache_stale": stale,
            "run_origin": (
                "stale_cache" if stale
                else "insecure_http" if insecure_http
                else "live"
            ),
            "transport_secure": False if insecure_http else True,
            "http_fallback_used": insecure_http,
            "failure_reason": failure_reason,
            "error": error,
        }

    def get_health(self):
        return deepcopy(self.health)

    def get_run_trades(self, days_back=30, limit=500, allow_stale=True):
        self.run_calls.append((days_back, limit, allow_stale))
        if self.exception is not None:
            raise self.exception
        if (
            self.run_uses_stale_cache or self.run_uses_insecure_http
        ) and not allow_stale:
            return []
        return deepcopy(self.rows[:limit])

    def format_for_whispers(
        self,
        days_back=7,
        limit=5,
        max_stale_days=3,
        allow_stale=True,
    ):
        self.format_calls.append(
            (days_back, limit, max_stale_days, allow_stale)
        )
        rows = self.get_run_trades(days_back, limit, allow_stale)
        prefix = (
            "OpenInsider/STALE" if self.run_uses_stale_cache
            else "OpenInsider/HTTP-INSECURE"
            if self.run_uses_insecure_http
            else "OpenInsider"
        )
        return [
            f"[{prefix}] {row.get('ticker', '?')}: "
            f"{row.get('trade_type', 'Trade')}"
            for row in rows
        ]


def trade(ticker, insider, filing_date, trade_type="P - Purchase"):
    return {
        "ticker": ticker,
        "trade_type": trade_type,
        "filing_date": filing_date,
        "insider_name": insider,
    }


# Both top-level consumers retain the exact injected run-scoped source.
shared = FakeOpenInsider(
    [trade("ROKU", "Director A", NOW_Z)],
    stale=True,
)
social = SocialAgent(now=NOW, openinsider=shared)
sec = SECAgent(now=NOW, openinsider=shared)
assert social.openinsider is shared
assert sec.openinsider is shared

# Cache-backed rows are permitted only as visibly stale narrative.
stale_whispers = social._fetch_rss("https://openinsider.com/rss", limit=5)
assert stale_whispers == ["[OpenInsider/STALE] ROKU: P - Purchase"]
assert shared.format_calls == [(7, 5, 3, True)]
assert shared.run_calls == [(7, 5, True)]


# The SEC consumer revalidates every row. Boundary timestamps are inclusive;
# undated, future, out-of-window, off-watchlist, and malformed rows are dropped.
temporal_rows = [
    trade("ROKU", "Boundary Buyer", "2026-07-19T12:00:00Z"),
    trade("ROKU", "Current Buyer", NOW_Z),
    trade("COIN", "Valid Buyer", "2026-08-17T12:00:00Z"),
    trade("COIN", "Undated Buyer", None),
    trade("PLTR", "Valid Buyer", "2026-08-17T12:00:00Z"),
    trade("PLTR", "Future Buyer", "2026-08-18T12:00:01Z"),
    trade("FCX", "Valid Buyer", "2026-08-17T12:00:00Z"),
    trade("FCX", "Old Buyer", "2026-07-19T11:59:59Z"),
    trade("ZZZZ", "Off Watchlist", NOW_Z),
    "not a mapping",
]
fresh_source = FakeOpenInsider(temporal_rows)
fresh_agent = SECAgent(now=NOW, openinsider=fresh_source)
clusters = fresh_agent._detect_openinsider_clusters(days_back=30)
assert fresh_source.run_calls == [(30, 500, False)]
assert len(clusters) == 1
roku = clusters[0]
assert roku["ticker"] == "ROKU"
assert roku["filing_count"] == 2
assert roku["insider_count"] == 2
assert roku["cluster_direction"] == "buy"
assert roku["source"] == "openinsider"
assert roku["as_of"] == NOW_Z
assert "signal_eligible" not in roku

fresh_health = fresh_agent.get_health()
assert fresh_health["openinsider_cluster_rows_considered"] == 10
assert fresh_health["openinsider_cluster_rows_eligible"] == 5
assert fresh_health["openinsider_cluster_rows_dropped"] == {
    "not_mapping": 1,
    "stale": 0,
    "insecure_transport": 0,
    "off_watchlist": 1,
    "undated": 1,
    "future": 1,
    "out_of_window": 1,
}
assert fresh_health["insider_cluster_fallback_reason"] is None

# A fresh live cluster retains the old record shape and bypasses EDGAR.
fresh_agent.get_recent_filings = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("EDGAR must not run when fresh OpenInsider clusters exist")
)
live_clusters = fresh_agent.detect_insider_clusters(days_back=30)
assert live_clusters and live_clusters[0]["source"] == "openinsider"
live_health = fresh_agent.get_health()
assert live_health["insider_cluster_source"] == "openinsider"
assert live_health["insider_cluster_fallback_used"] is False
assert live_health["insider_cluster_fallback_reason"] is None


def assert_edgar_fallback(source, expected_reason):
    original_tickers = sec_module.ALL_TICKERS
    sec_module.ALL_TICKERS = ["ROKU"]
    try:
        agent = SECAgent(now=NOW, openinsider=source)
        edgar_calls = []

        def no_edgar_rows(ticker, form_types, days_back, limit):
            edgar_calls.append((ticker, tuple(form_types), days_back, limit))
            return []

        agent.get_recent_filings = no_edgar_rows
        assert agent.detect_insider_clusters(days_back=30) == []
        health = agent.get_health()
        assert edgar_calls == [("ROKU", ("4",), 30, 10)]
        assert health["insider_cluster_source"] == "edgar_fallback"
        assert health["insider_cluster_fallback_used"] is True
        assert health["insider_cluster_fallback_reason"] == expected_reason
        return health
    finally:
        sec_module.ALL_TICKERS = original_tickers


class LeakyStaleOpenInsider(FakeOpenInsider):
    """Hostile adapter that ignores the live-only view contract."""

    def get_run_trades(self, days_back=30, limit=500, allow_stale=True):
        self.run_calls.append((days_back, limit, allow_stale))
        return deepcopy(self.rows[:limit])


# Stale cache is narrative-only, a failed live acquisition is unavailable,
# and fresh rows without two qualifying filers preserve the existing fallback.
stale_health = assert_edgar_fallback(
    FakeOpenInsider(
        [
            trade("ROKU", "Cached Buyer A", "2026-08-18T10:00:00Z"),
            trade("ROKU", "Cached Buyer B", "2026-08-18T11:00:00Z"),
        ],
        stale=True,
        acquisition_status="error",
        failure_reason="connection_refused",
    ),
    "stale_cache_disallowed",
)
assert stale_health["openinsider_cluster_rows_considered"] == 0

http_source = FakeOpenInsider(
    [
        trade("ROKU", "HTTP Buyer A", "2026-08-18T10:00:00Z"),
        trade("ROKU", "HTTP Buyer B", "2026-08-18T11:00:00Z"),
    ],
    insecure_http=True,
    acquisition_status="warn",
    failure_reason="connection_refused",
)
http_social = SocialAgent(now=NOW, openinsider=http_source)
http_whispers = http_social._fetch_rss(
    "https://openinsider.com/rss", limit=5
)
assert all(
    item.startswith("[OpenInsider/HTTP-INSECURE]")
    for item in http_whispers
)
http_health = assert_edgar_fallback(
    http_source, "insecure_http_disallowed"
)
assert http_health["openinsider_cluster_rows_considered"] == 0

# Defense in depth rejects a source that leaks cache rows despite
# allow_stale=False, and independently rejects stale provenance on individual
# rows from an otherwise fresh adapter.
leaked_health = assert_edgar_fallback(
    LeakyStaleOpenInsider(
        [
            trade("ROKU", "Cached Buyer A", "2026-08-18T10:00:00Z"),
            trade("ROKU", "Cached Buyer B", "2026-08-18T11:00:00Z"),
        ],
        stale=True,
        acquisition_status="error",
        failure_reason="connection_refused",
    ),
    "stale_cache_disallowed",
)
assert leaked_health["openinsider_cluster_rows_dropped"]["stale"] == 2

leaked_http_health = assert_edgar_fallback(
    LeakyStaleOpenInsider(
        [
            trade("ROKU", "HTTP Buyer A", "2026-08-18T10:00:00Z"),
            trade("ROKU", "HTTP Buyer B", "2026-08-18T11:00:00Z"),
        ],
        insecure_http=True,
        acquisition_status="warn",
        failure_reason="connection_refused",
    ),
    "insecure_http_disallowed",
)
assert (
    leaked_http_health["openinsider_cluster_rows_dropped"]
    ["insecure_transport"]
    == 2
)

row_stale_a = trade("ROKU", "Cached Buyer A", "2026-08-18T10:00:00Z")
row_stale_b = trade("ROKU", "Cached Buyer B", "2026-08-18T11:00:00Z")
row_stale_a["source_stale"] = True
row_stale_b["source_stale"] = "true"
row_stale_health = assert_edgar_fallback(
    FakeOpenInsider([row_stale_a, row_stale_b]),
    "stale_cache_disallowed",
)
assert row_stale_health["openinsider_cluster_rows_dropped"]["stale"] == 2

row_http_a = trade("ROKU", "HTTP Buyer A", "2026-08-18T10:00:00Z")
row_http_b = trade("ROKU", "HTTP Buyer B", "2026-08-18T11:00:00Z")
for row in (row_http_a, row_http_b):
    row["source_transport_scheme"] = "http"
    row["source_transport_secure"] = False
    row["signal_eligible"] = False
row_http_health = assert_edgar_fallback(
    FakeOpenInsider([row_http_a, row_http_b]),
    "insecure_http_disallowed",
)
assert (
    row_http_health["openinsider_cluster_rows_dropped"]
    ["insecure_transport"]
    == 2
)

assert_edgar_fallback(
    FakeOpenInsider(
        acquisition_status="error",
        failure_reason="connection_refused",
    ),
    "source_unavailable",
)
assert_edgar_fallback(
    FakeOpenInsider(
        [trade("ROKU", "Only Buyer", "2026-08-18T10:00:00Z")]
    ),
    "no_qualifying_clusters",
)
assert_edgar_fallback(
    FakeOpenInsider([trade("ROKU", "Undated Buyer", None)]),
    "no_temporally_eligible_rows",
)
mixed_drop_health = assert_edgar_fallback(
    FakeOpenInsider([
        trade("ZZZZ", "Off Watchlist", NOW_Z),
        trade(None, "Missing Ticker", NOW_Z),
        trade("ROKU", "Undated Watchlist Buyer", None),
    ]),
    "no_watchlist_rows",
)
assert mixed_drop_health["openinsider_cluster_rows_dropped"] == {
    "not_mapping": 0, "stale": 0, "insecure_transport": 0,
    "off_watchlist": 2, "undated": 1, "future": 0,
    "out_of_window": 0,
}
assert_edgar_fallback(
    FakeOpenInsider(exception=RuntimeError("fixture acquisition failure")),
    "source_unavailable",
)


# Defense in depth: explicitly ineligible context is excluded from both
# confluence population and Tier-1 ledger ingestion. Absence of the marker
# retains the existing live-cluster behavior and identity.
stale_cluster = {
    "ticker": "ROKU",
    "buyers": 2,
    "sellers": 0,
    "period_days": 30,
    "alert_level": "MEDIUM",
    "cluster_direction": "buy",
    "as_of": NOW_Z,
    "signal_eligible": False,
}
live_cluster = {
    "ticker": "RGNX",
    "buyers": 2,
    "sellers": 0,
    "period_days": 30,
    "alert_level": "MEDIUM",
    "cluster_direction": "buy",
    "as_of": NOW_Z,
}
events = signals.collect_alert_events(
    [], [], {}, [stale_cluster, live_cluster], {}, [], [], []
)
assert [(event["ticker"], event["family"]) for event in events] == [
    ("RGNX", "insider")
]

ledger_rows = list(_iter_signals(
    {"sections": {"insider_clusters": [stale_cluster, live_cluster]}},
    run_id="fixture-run",
    pipeline_version="2.6.2",
))
assert [(row["ticker"], row["family"]) for row in ledger_rows] == [
    ("RGNX", "insider_cluster")
]

print("OpenInsider shared-consumer integration checks passed")
