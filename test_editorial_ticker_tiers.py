"""Offline acceptance checks for the +13 editorial-only coverage tier."""

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, ".")

import agents.news_agent as news_module
import agents.social_agent as social_module
import agents.watcher_agent as watcher_module
from agents.news_agent import NewsAgent
from agents.news_providers import FMPNewsProvider, ProviderResult
from agents.sec_agent import SECAgent
from agents.social_agent import SocialAgent
from agents.watcher_agent import WatcherAgent
from config import (
    ALL_TICKERS,
    APEWISDOM_FILTERS,
    APEWISDOM_UNIVERSE_PAGES,
    EDITORIAL_COVERAGE_TICKERS,
    EDITORIAL_ONLY_TICKERS,
    EDITORIAL_TICKER_ALIASES,
    ISSUER_REGISTRY,
    PIPELINE_VERSION,
    SCHEMA_VERSION,
    SIGNAL_ELIGIBLE_TICKERS,
    WATCHLIST_STOCKS,
    WATCHLIST_VULTURE,
)
from editorial_focus import rank_focus_candidates, select_focus


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
CORE_29 = (
    "TSLA", "ROKU", "PLTR", "STTDF", "CRSP", "ACHR", "SHOP", "SCCO",
    "NTLA", "FCX", "VRT", "COIN", "RIVN", "AMAT", "VNDA", "SI=F",
    "URA", "USO", "GLD", "PSLV", "UUUU", "CCJ", "NXE", "DNN", "LMT",
    "NOC", "ITA", "RGNX", "^VIX",
)
EDITORIAL_13 = (
    "MRNA", "VRTX", "BEAM", "RARE", "CEG", "LEU",
    "BWXT", "GEV", "MP", "NVDA", "RKLB", "HOOD", "MRK",
)
GDELT_QUERIES = (
    '("stock market" OR "federal reserve" OR inflation OR tariff OR earnings OR semiconductor OR uranium OR bitcoin OR oil OR tesla OR roku OR palantir) sourcelang:english',
    '("crispr therapeutics" OR "archer aviation" OR shopify OR "southern copper" OR intellia OR freeport OR vertiv OR coinbase OR rivian OR "applied materials" OR vanda) sourcelang:english',
    '("energy fuels" OR cameco OR nexgen OR denison OR regenxbio OR lockheed OR northrop) sourcelang:english',
)
GDELT_QUERY_HASH = "54214253aea91b20316ad848bea02ebd1bce4036b82c17588bee6563fbae01b9"
SHADOW_RECORD_KEYS = {
    "source_record_id", "tickers", "title", "link", "as_of", "lane",
    "score_components", "disposition", "reason",
}


class StaticProvider:
    name = "Editorial tier fixture"

    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]

    def fetch(self):
        return ProviderResult(
            self.name, "ok" if self.rows else "empty",
            [dict(row) for row in self.rows],
        )


@contextmanager
def coverage_mode(mode):
    previous = news_module.EDITORIAL_COVERAGE_MODE
    news_module.EDITORIAL_COVERAGE_MODE = mode
    try:
        yield
    finally:
        news_module.EDITORIAL_COVERAGE_MODE = previous


def story(
    ticker,
    title,
    source_record_id,
    *,
    raw_tickers=None,
    published="2026-08-20T11:00:00Z",
    source_class="press_release",
):
    slug = source_record_id.replace(":", "-")
    domain = f"{ticker.casefold()}.example"
    return {
        "title": title,
        "link": f"https://{domain}/{slug}",
        "canonical_url": f"https://{domain}/{slug}",
        "published": published,
        "source_time_kind": "published",
        "provider_seen_at": "2026-08-20T11:05:00Z",
        "provider": "Editorial tier fixture",
        "publisher": ISSUER_REGISTRY.get(ticker, {}).get(
            "display_name", "Fixture Publisher"
        ),
        "publisher_domain": domain,
        "source_class": source_class,
        "source_record_id": source_record_id,
        "tickers": list(raw_tickers if raw_tickers is not None else [ticker]),
        "ticker_metadata_kind": "subject",
        "summary": "Provider summary retained only in raw/selected provenance.",
    }


def run_news(rows, mode):
    with coverage_mode(mode):
        agent = NewsAgent(now=NOW, providers=[StaticProvider(rows)])
        agent._google_enabled = False
        agent.get_global_headlines()
        return (
            agent.get_scored_headlines(),
            agent.get_dropped_headlines(),
            agent.get_pool_diagnostics(),
        )


def subprocess_check(code, mode):
    env = dict(os.environ)
    env["EDITORIAL_COVERAGE_MODE"] = mode
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Purpose-specific universes are exact, ordered, disjoint, and immutable. The
# deprecated ALL_TICKERS projection remains the original ordered 29.
assert PIPELINE_VERSION == "2.6.4"
assert SCHEMA_VERSION == "2.8"
assert SIGNAL_ELIGIBLE_TICKERS == CORE_29
assert EDITORIAL_ONLY_TICKERS == EDITORIAL_13
assert EDITORIAL_COVERAGE_TICKERS == CORE_29 + EDITORIAL_13
assert tuple(ALL_TICKERS) == CORE_29
assert len(set(CORE_29)) == 29
assert len(set(EDITORIAL_13)) == 13
assert set(CORE_29).isdisjoint(EDITORIAL_13)


# Every editorial-only issuer profile is complete and carries the SEC CIK used
# for deterministic issuer resolution. The registry key and row ticker agree.
EXPECTED_CIKS = {
    "MRNA": "0001682852", "VRTX": "0000875320", "BEAM": "0001745999",
    "RARE": "0001515673", "CEG": "0001868275", "LEU": "0001065059",
    "BWXT": "0001486957", "GEV": "0001996810", "MP": "0001801368",
    "NVDA": "0001045810", "RKLB": "0001819994", "HOOD": "0001783879", "MRK": "0000310158",
}
EXPECTED_EXCHANGES = {
    "MRNA": "NASDAQ", "VRTX": "NASDAQ", "BEAM": "NASDAQ",
    "RARE": "NASDAQ", "CEG": "NASDAQ", "LEU": "NYSE", "BWXT": "NYSE",
    "GEV": "NYSE", "MP": "NYSE", "NVDA": "NASDAQ", "RKLB": "NASDAQ",
    "HOOD": "NASDAQ", "MRK": "NYSE",
}
assert tuple(ISSUER_REGISTRY) == EDITORIAL_13
for ticker in EDITORIAL_13:
    profile = ISSUER_REGISTRY[ticker]
    assert set(profile) == {
        "ticker", "cik", "legal_name", "display_name", "aliases", "exchange",
        "security_type", "coverage_tier",
    }
    assert profile["ticker"] == ticker
    assert profile["cik"] == EXPECTED_CIKS[ticker]
    assert profile["exchange"] == EXPECTED_EXCHANGES[ticker]
    assert profile["security_type"] == "common_stock"
    assert profile["coverage_tier"] == "editorial_only"
    assert profile["legal_name"] and profile["display_name"]
    assert profile["aliases"] == EDITORIAL_TICKER_ALIASES[ticker]


# GDELT remains byte-for-byte frozen at the pre-expansion three-query batch.
actual_queries = NewsAgent._build_gdelt_queries()
assert actual_queries == GDELT_QUERIES
actual_query_hash = sha256(
    json.dumps(actual_queries, separators=(",", ":")).encode("utf-8")
).hexdigest()
assert actual_query_hash == GDELT_QUERY_HASH


# Shadow preserves the legacy FMP acquisition corpus. The conditional
# entitlement fallback is separately counted in the policy tests.
with coverage_mode("shadow"):
    default_agent = NewsAgent(now=NOW)
    fmp_providers = [
        provider for provider in default_agent._providers
        if isinstance(provider, FMPNewsProvider)
    ]
expected_fmp_tickers = tuple(
    ticker for ticker in CORE_29
    if not ticker.startswith("^") and not ticker.endswith("=F")
)
assert len(fmp_providers) == 1
assert tuple(fmp_providers[0].tickers) == expected_fmp_tickers


class EmptyJSONResponse:
    status_code = 200

    @staticmethod
    def json():
        return []


fmp_calls = []


def fmp_get(url, **kwargs):
    fmp_calls.append({"url": url, **kwargs})
    return EmptyJSONResponse()


fmp = FMPNewsProvider(
    "configured-test-key",
    tickers=expected_fmp_tickers,
    now=NOW,
    request_get=fmp_get,
)
assert fmp.fetch().status == "empty"
assert len(fmp_calls) == 1
assert fmp_calls[0]["params"]["symbols"] == ",".join(expected_fmp_tickers)


# All non-news acquisition targets stay on the original instrumented universe.
# Spies replace every network edge and record the actual loop inputs.
watcher = WatcherAgent()
assert tuple(dict.fromkeys(watcher.watchlist)) == tuple(
    ticker for ticker in CORE_29 if ticker != "^VIX"
)
assert tuple(watcher.gauges) == ("^VIX",)
assert set(watcher.watchlist + watcher.gauges) == set(CORE_29)
assert set(watcher.watchlist + watcher.gauges).isdisjoint(EDITORIAL_13)

price_calls = []
watcher.get_price_data = lambda ticker: price_calls.append(ticker) or None
watcher.get_full_report_data()
assert Counter(price_calls) == Counter(watcher.watchlist + watcher.gauges)
assert set(price_calls).isdisjoint(EDITORIAL_13)

expected_equity_targets = tuple(dict.fromkeys(
    WATCHLIST_STOCKS + WATCHLIST_VULTURE
))
option_calls = []
watcher.get_options_flow = lambda ticker: option_calls.append(ticker) or {}
watcher.get_all_options_flow()
assert Counter(option_calls) == Counter(WATCHLIST_STOCKS + WATCHLIST_VULTURE)
assert tuple(dict.fromkeys(option_calls)) == expected_equity_targets
assert set(option_calls).issubset(CORE_29)
assert set(option_calls).isdisjoint(EDITORIAL_13)

earnings_calls = []
original_yf_ticker = watcher_module.yf.Ticker
watcher_module.yf.Ticker = lambda ticker: earnings_calls.append(ticker) or object()
watcher._get_earnings_from_dates_api = lambda ticker, stock: None
watcher._get_earnings_from_calendar = lambda ticker, stock: None
try:
    assert watcher.get_earnings_calendar() == []
finally:
    watcher_module.yf.Ticker = original_yf_ticker
assert tuple(earnings_calls) == expected_equity_targets
assert set(earnings_calls).issubset(CORE_29)
assert set(earnings_calls).isdisjoint(EDITORIAL_13)

sec_calls = []
sec = SECAgent(now=NOW, openinsider=object())


def fake_recent_filings(ticker, **kwargs):
    sec_calls.append((ticker, kwargs))
    return []


sec.get_recent_filings = fake_recent_filings
assert sec.scan_all_watchlist(days_back=7) == []
assert tuple(ticker for ticker, _kwargs in sec_calls) == CORE_29
assert all(
    kwargs == {"form_types": ["8-K", "4"], "days_back": 7, "limit": 3}
    for _ticker, kwargs in sec_calls
)
assert set(ticker for ticker, _kwargs in sec_calls).isdisjoint(EDITORIAL_13)

previous_praw_available = social_module.PRAW_AVAILABLE
social_module.PRAW_AVAILABLE = False
try:
    social = SocialAgent(now=NOW, openinsider=object())
finally:
    social_module.PRAW_AVAILABLE = previous_praw_available
social_calls = []


def fake_apewisdom(filter_name="all-stocks", limit=15, pages=1):
    social_calls.append((filter_name, limit, pages))
    return []


social._fetch_apewisdom = fake_apewisdom
attention, render_records = social._build_social_attention()
assert APEWISDOM_UNIVERSE_PAGES == 5
assert APEWISDOM_FILTERS == [("all-stocks", 15), ("4chan", 5)]
assert social_calls == [("all-stocks", None, 5), ("4chan", 5, 1)]
assert SocialAgent.SOCIAL_UNIVERSE == tuple(
    ticker for ticker in CORE_29 if "^" not in ticker and "=" not in ticker
)
assert len(attention) == len(SocialAgent.SOCIAL_UNIVERSE)
assert render_records == []
assert set(SocialAgent.SOCIAL_UNIVERSE).isdisjoint(EDITORIAL_13)


# Every +13 issuer resolves from its conservative issuer alias, an explicit
# ticker/listing context, and subject metadata. Related-provider tags alone do
# not establish identity.
with coverage_mode("shadow"):
    for ticker in EDITORIAL_13:
        alias = ISSUER_REGISTRY[ticker]["aliases"][0]
        assert NewsAgent._matched_universe_tickers({
            "title": f"{alias} reports quarterly results", "tickers": [],
        }) == [ticker]
        assert NewsAgent._matched_universe_tickers({
            "title": f"{ticker} stock reports quarterly results", "tickers": [],
        }) == [ticker]
        assert NewsAgent._matched_universe_tickers({
            "title": "Company reports quarterly results",
            "tickers": [ticker],
            "ticker_metadata_kind": "subject",
        }) == [ticker]
        assert NewsAgent._matched_universe_tickers({
            "title": "Unrelated issuer reports quarterly results",
            "tickers": [ticker],
            "ticker_metadata_kind": "related",
        }) == []


# Broad scientific/category/ordinary-language references never manufacture an
# issuer mapping. These cover every new name, including the five high-risk
# ambiguous tokens mRNA, beam, rare, MP, and hood.
NEGATIVE_TITLES = {
    "MRNA": "Researchers describe an mRNA cancer vaccine",
    "VRTX": "Students calculate the vertex of a parabola",
    "BEAM": "Engineers reinforce a bridge beam",
    "RARE": "A rare weather pattern crosses the coast",
    "CEG": "Astronomers chart a constellation in the night sky",
    "LEU": "Scientists test a uranium enrichment process",
    "BWXT": "A technology supplier expands factory capacity",
    "GEV": "A turbine manufacturer expands grid equipment",
    "MP": "A member of parliament debates the bill",
    "NVDA": "Graphics processor demand grows this quarter",
    "RKLB": "A university opens a launch research center",
    "HOOD": "Drivers inspect a damaged engine hood",
    "MRK": "Merck KGaA reports quarterly results",
}
with coverage_mode("shadow"):
    for ticker, title in NEGATIVE_TITLES.items():
        assert ticker not in NewsAgent._matched_universe_tickers({
            "title": title, "tickers": [],
        })


# Off mode is verified in a clean interpreter: none of the 13 may map through
# an alias, symbol/listing context, or explicit subject metadata.
subprocess_check(
    r'''
from agents.news_agent import NewsAgent
from config import EDITORIAL_COVERAGE_MODE, EDITORIAL_ONLY_TICKERS, ISSUER_REGISTRY
assert EDITORIAL_COVERAGE_MODE == "off"
for ticker in EDITORIAL_ONLY_TICKERS:
    alias = ISSUER_REGISTRY[ticker]["aliases"][0]
    cases = (
        {"title": f"{alias} reports quarterly results", "tickers": []},
        {"title": f"${ticker} stock rises", "tickers": []},
        {
            "title": "Company reports quarterly results",
            "tickers": [ticker],
            "ticker_metadata_kind": "subject",
        },
    )
    for case in cases:
        assert NewsAgent._matched_universe_tickers(case) == [], (ticker, case)
''',
    "off",
)

# Invalid configuration values fail closed to the default shadow mode.
subprocess_check(
    "from config import EDITORIAL_COVERAGE_MODE; "
    "assert EDITORIAL_COVERAGE_MODE == 'shadow'",
    "invalid-mode",
)


# In shadow mode, all thirteen fresh pure-editorial source rows are suppressed,
# counted once each, and audited with a bounded, schema-safe provenance shape.
shadow_rows = [
    story(
        ticker,
        f"{ISSUER_REGISTRY[ticker]['aliases'][0]} reports quarterly results",
        f"shadow:{ticker.casefold()}",
    )
    for ticker in EDITORIAL_13
]
shadow_selected, shadow_dropped, shadow_diagnostics = run_news(
    shadow_rows, "shadow"
)
shadow = shadow_diagnostics["editorial_shadow"]
assert shadow_selected == []
assert shadow["mode"] == "shadow"
assert shadow["candidate_count"] == 13
assert shadow["fresh_candidate_count"] == 13
assert len(shadow["records"]) == NewsAgent.SHADOW_AUDIT_LIMIT == 10
assert all(set(record) == SHADOW_RECORD_KEYS for record in shadow["records"])
assert all(record["tickers"] for record in shadow["records"])
assert all(record["disposition"] == "suppressed" for record in shadow["records"])
assert all(
    record["reason"] == "editorial_coverage_shadow_mode"
    for record in shadow["records"]
)
assert all("summary" not in record and "provider" not in record for record in shadow["records"])
assert {
    row["source_record_id"] for row in shadow_dropped
    if row.get("drop_reason") == "editorial_shadow_suppressed"
} == {row["source_record_id"] for row in shadow_rows}


# A mixed PLTR+NVDA story is audited for NVDA while the selectable clone is
# projected to PLTR. Raw provider tickers remain untouched as provenance.
mixed_row = story(
    "PLTR",
    "Palantir and NVIDIA announce AI partnership",
    "shadow:pltr-nvda",
    raw_tickers=["PLTR", "NVDA"],
)
mixed_selected, mixed_dropped, mixed_diagnostics = run_news(
    [mixed_row], "shadow"
)
assert mixed_dropped == []
assert len(mixed_selected) == 1
assert mixed_selected[0]["tickers"] == ["PLTR", "NVDA"]
assert mixed_selected[0]["universe_tickers"] == ["PLTR"]
assert mixed_selected[0]["lane"] == "universe"
assert mixed_selected[0]["coverage_tier"] == "instrumented"
assert mixed_selected[0]["signal_eligible"] is True
assert mixed_selected[0]["signal_eligibility_reason"] == "instrumented_universe"
mixed_shadow = mixed_diagnostics["editorial_shadow"]
assert mixed_shadow["candidate_count"] == 1
assert mixed_shadow["fresh_candidate_count"] == 1
assert len(mixed_shadow["records"]) == 1
assert mixed_shadow["records"][0]["tickers"] == ["NVDA"]
assert mixed_shadow["records"][0]["disposition"] == "core_projection"
assert mixed_shadow["records"][0]["reason"] == "editorial_mapping_shadowed"


# Stale/undated editorial rows keep ordinary freshness drop reasons; they are
# never mislabeled as fresh shadow suppression.
stale = story(
    "MRNA", "Moderna reports Phase 3 trial results", "shadow:stale",
    published="2026-08-15T11:00:00Z",
)
undated = story(
    "MRNA", "Moderna reports Phase 3 trial results", "shadow:undated",
    published=None,
)
stale_selected, stale_dropped, stale_diagnostics = run_news(
    [stale, undated], "shadow"
)
assert stale_selected == []
assert stale_diagnostics["editorial_shadow"]["candidate_count"] == 2
assert stale_diagnostics["editorial_shadow"]["fresh_candidate_count"] == 0
assert stale_diagnostics["editorial_shadow"]["records"] == []
stale_reasons = {
    row["source_record_id"]: row["drop_reason"] for row in stale_dropped
}
assert stale_reasons == {"shadow:stale": "stale", "shadow:undated": "undated"}


# Clinical materiality recognizes only explicit occurred endpoint outcomes.
# Forward-looking readouts, quoted history, and competitor results stay at zero.
CLINICAL_IMPACT_CASES = (
    ("Moderna Phase 3 trial met primary endpoint", 2),
    ("Moderna Phase 3 trial met primary and key secondary endpoints", 2),
    ("Moderna Phase 3 trial met endpoints of RFS and DMFS", 2),
    ("Moderna Phase 3 trial did not meet primary endpoint", 2),
    ("Moderna Phase 3 trial failed to meet primary endpoint", 2),
    ("Moderna reports positive Phase 3 topline results", 2),
    ("Moderna Phase 3 trial met primary endpoint and will present data", 2),
    ("Moderna Phase 3 trial met primary endpoint, beating rival", 2),
    ("Moderna expects Phase 3 results next quarter", 0),
    ("Moderna expects Phase 3 trial to meet primary endpoint", 0),
    ("Moderna comments after rival Phase 3 trial met primary endpoint", 0),
    ("Moderna recalls Phase 3 trial met primary endpoint last year", 0),
    ("Moderna Phase 3 trial met primary endpoint last year", 0),
)
with coverage_mode("active"):
    for index, (title, expected_impact) in enumerate(CLINICAL_IMPACT_CASES):
        candidate = story(
            "MRNA", title, f"clinical-impact:{index}", source_class="official"
        )
        components = NewsAgent._score_components(candidate)
        assert components["impact"] == expected_impact, (title, components)


# Active mode may select a fresh official MRNA development for editorial focus,
# but the row and focus remain explicitly non-signal-eligible.
mrna_story = story(
    "MRNA",
    "Moderna Phase 3 trial met primary endpoint",
    "active:mrna-phase3",
    source_class="official",
)
active_selected, active_dropped, active_diagnostics = run_news(
    [mrna_story], "active"
)
assert active_dropped == []
assert len(active_selected) == 1
assert active_selected[0]["universe_tickers"] == ["MRNA"]
assert active_selected[0]["coverage_tier"] == "editorial_only"
assert active_selected[0]["signal_eligible"] is False
assert active_selected[0]["signal_eligibility_reason"] == "editorial_only_coverage"
assert active_diagnostics["editorial_shadow"] == {
    "mode": "active", "candidate_count": 0,
    "fresh_candidate_count": 0, "records": [],
}
active_focus = select_focus(
    active_selected,
    EDITORIAL_COVERAGE_TICKERS,
    NOW,
    "MRNA",
    coverage_tickers=EDITORIAL_COVERAGE_TICKERS,
    coverage_mode="active",
)
assert active_focus["status"] == "selected"
assert active_focus["selection_mode"] == "configured"
assert active_focus["ticker"] == "MRNA"
assert active_focus["coverage_tier"] == "editorial_only"
assert active_focus["signal_eligible"] is False
assert active_focus["signal_eligibility_reason"] == "editorial_only_coverage"

shadow_pin = select_focus(
    active_selected,
    SIGNAL_ELIGIBLE_TICKERS,
    NOW,
    "MRNA",
    coverage_tickers=EDITORIAL_COVERAGE_TICKERS,
    coverage_mode="shadow",
)
assert shadow_pin["status"] == "no_focus"
assert shadow_pin["ticker"] is None
assert shadow_pin["pin_rejection_reason"] == "editorial_coverage_not_active"


# Separate MRNA stories never lend each other component maxima.
high_impact = deepcopy(active_selected[0])
high_impact.update({
    "source_record_id": "active:mrna-impact",
    "title": "Moderna Phase 3 trial met primary endpoint",
    "link": "https://moderna.example/impact",
    "canonical_url": "https://moderna.example/impact",
    "provider": "Discovery Fixture",
    "publisher": "Discovery Publisher",
    "publisher_domain": "discovery.example",
    "source_class": "global_discovery",
})
high_impact["score_components"] = {
    **high_impact["score_components"], "impact": 3, "authority": 1,
}
official_routine = deepcopy(active_selected[0])
official_routine.update({
    "source_record_id": "active:mrna-official",
    "title": "Moderna provides a routine corporate update",
    "link": "https://moderna.example/official",
    "canonical_url": "https://moderna.example/official",
    "provider": "Official Fixture",
    "publisher": "Moderna",
    "publisher_domain": "moderna.example",
    "source_class": "official",
})
official_routine["score_components"] = {
    **official_routine["score_components"], "impact": 1, "authority": 3,
}
separate = rank_focus_candidates(
    [official_routine, high_impact], EDITORIAL_COVERAGE_TICKERS, NOW
)
assert len(separate) == 2
by_evidence = {row["primary_evidence_id"]: row for row in separate}
assert by_evidence["active:mrna-impact"]["score_components"]["impact"] == 3
assert by_evidence["active:mrna-impact"]["score_components"]["source_authority"] == 1
assert by_evidence["active:mrna-official"]["score_components"]["impact"] == 1
assert by_evidence["active:mrna-official"]["score_components"]["source_authority"] == 3
anti_pollution_focus = select_focus(
    [official_routine, high_impact],
    EDITORIAL_COVERAGE_TICKERS,
    NOW,
    coverage_tickers=EDITORIAL_COVERAGE_TICKERS,
    coverage_mode="active",
)
assert anti_pollution_focus["ticker"] == "MRNA"
assert anti_pollution_focus["score_components"]["impact"] == 3
assert anti_pollution_focus["score_components"]["source_authority"] == 1

print("Editorial ticker-tier acceptance checks passed")

