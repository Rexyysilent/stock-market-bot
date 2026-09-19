"""Versioned purpose boundaries for OMNI-01; no collectors or state writers.

Configured collection is an engineering scope, NOT a source-license attestation.
Unknown capabilities/rights stay unknown. This module does not validate claims
and cannot promote securities into the production signal cohort.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from config import (
    CORE_TICKER_ALIASES, EDITORIAL_COVERAGE_TICKERS,
    EDITORIAL_ONLY_TICKERS, ETF_TICKERS, ISSUER_REGISTRY,
    LEGACY_EDITORIAL_ONLY_TICKERS, SIGNAL_ELIGIBLE_TICKERS,
    WATCHLIST_STOCKS, WATCHLIST_VULTURE,
)

COHORT_VERSION = "editorial-42-v2"
REGISTRY_VERSION = "issuer-capabilities-1"
SOURCE_POLICY_VERSION = "source-purpose-1"
MAPPING_VERSION = "issuer-mapping-2"
LEGACY_COVERAGE_TICKERS = SIGNAL_ELIGIBLE_TICKERS + LEGACY_EDITORIAL_ONLY_TICKERS
OPTIONS_EARNINGS_TICKERS = tuple(dict.fromkeys(WATCHLIST_STOCKS + WATCHLIST_VULTURE))
SOCIAL_ATTENTION_TICKERS = tuple(
    t for t in SIGNAL_ELIGIBLE_TICKERS if not t.startswith("^") and not t.endswith("=F")
)

def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value

def _plain(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value

def normalize_mode(mode):
    value = str(mode or "").strip().casefold()
    return value if value in ("off", "shadow", "active") else "shadow"

def fmp_acquisition_tickers(mode):
    """Shadow must observe the same corpus, not just the same request count."""
    cohort = (EDITORIAL_COVERAGE_TICKERS if normalize_mode(mode) == "active"
              else SIGNAL_ELIGIBLE_TICKERS)
    return tuple(t for t in cohort if not t.startswith("^") and not t.endswith("=F"))

def _security(ticker):
    profile = dict(ISSUER_REGISTRY.get(ticker, {}))
    if ticker.endswith("=F"):
        kind = "future"
    elif ticker.startswith("^"):
        kind = "index"
    elif ticker in ETF_TICKERS:
        kind = "exchange_traded_product"
    else:
        kind = profile.get("security_type", "unknown")
    core = ticker in SIGNAL_ELIGIBLE_TICKERS
    non_issuer = kind in ("future", "index")
    return {
        "ticker": ticker,
        "coverage_tier": "instrumented" if core else "editorial_only",
        "security_type": kind,
        "issuer": {
            "legal_name": profile.get("legal_name"),
            "display_name": profile.get("display_name"),
            "cik": profile.get("cik"),
            "exchange": profile.get("exchange"),
            "aliases": profile.get("aliases", CORE_TICKER_ALIASES.get(ticker, ())),
            "identity_status": ("primary_verified" if ticker == "MRK"
                                else "legacy_profile" if profile else "unverified"),
        },
        # Actual routed targets, not promises that data/products exist.
        "acquisition_targets": {
            "prices_technicals": core,
            "sec_cik_lookup": core,
            "options": ticker in OPTIONS_EARNINGS_TICKERS,
            "earnings": ticker in OPTIONS_EARNINGS_TICKERS,
            "social_attention": ticker in SOCIAL_ATTENTION_TICKERS,
        },
        "availability": {
            "prices": "unknown", "options": "unknown", "earnings": "unknown",
            "sec_registrant": ("not_applicable" if non_issuer else
                              "verified" if ticker == "MRK" else "unknown"),
            "sec_form_capabilities": "not_applicable" if non_issuer else "unknown",
        },
        "production_signal_cohort": core,
    }

SECURITY_CAPABILITIES = _freeze({t: _security(t) for t in EDITORIAL_COVERAGE_TICKERS})
IDENTITY_EVIDENCE = _freeze({
    "MRK": {
        "checked_on": "2026-09-06",
        "filing": "https://www.sec.gov/Archives/edgar/data/310158/000031015826000063/mrk-20251231.htm",
        "issuer": "https://www.merck.com/investor-relations/",
        "filing_route": "https://www.sec.gov/edgar/browse/?CIK=310158",
        "newsroom": "https://www.merck.com/news/",
        "scope": "Legal issuer, CIK, NYSE common-stock symbol; not clinical-event verification.",
        "access_display_rights": "unknown",
    }
})
SOURCE_IDS = (
    "official_feeds", "fmp", "alpha_vantage", "gdelt", "google_news",
    "sec_edgar", "openinsider", "apewisdom", "reddit", "ceo_ca",
    "clinicaltrials", "yfinance", "rss", "twitter",
)
SOURCE_POLICIES = _freeze({
    source: {
        "rights_status": "unknown",
        "existing_local_metadata": "configured_legacy_scope",
        "raw_storage": "unknown",
        "model_processing": "unknown",
        "commercial_redistribution": "unknown",
        "excerpt_redistribution": "unknown",
    }
    for source in SOURCE_IDS
})

def source_use_decision(source_id, purpose):
    """Fail closed for unreviewed new uses; never infer a license from credibility."""
    policy = SOURCE_POLICIES.get(source_id)
    allowed = bool(policy and purpose == "existing_local_metadata"
                   and policy[purpose] == "configured_legacy_scope")
    return {
        "allowed": allowed,
        "reason": ("legacy_scope_not_license_attestation" if allowed
                   else "unknown_source" if policy is None else "purpose_not_approved"),
        "rights_status": "unknown",
        "policy_version": SOURCE_POLICY_VERSION,
    }

def require_source_use(source_id, purpose):
    decision = source_use_decision(source_id, purpose)
    if not decision["allowed"]:
        raise PermissionError(decision["reason"])
    return decision

def fmp_coverage_contract(coverage):
    known = coverage in ("ticker_stock_news", "fmp_articles")
    return {
        "policy_version": SOURCE_POLICY_VERSION,
        "source_scope": coverage if known else "unknown",
        "narrower_than_requested": coverage == "fmp_articles" or not known,
        "complete_market_coverage": False,
        "model_processing_allowed": source_use_decision("fmp", "model_processing")["allowed"],
        "commercial_redistribution_allowed": source_use_decision("fmp", "commercial_redistribution")["allowed"],
        "rights_status": "unknown",
    }

def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(_plain(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def coverage_policy_manifest(mode):
    """Fresh JSON-safe snapshot; callers cannot mutate the policy registry."""
    from config import UNIVERSE_PROFILE
    if UNIVERSE_PROFILE is not None:
        from universe_profile import profile_policy_manifest
        return profile_policy_manifest(UNIVERSE_PROFILE)
    mode = normalize_mode(mode)
    manifest = {
        "cohort_version": COHORT_VERSION,
        "registry_version": REGISTRY_VERSION,
        "source_policy_version": SOURCE_POLICY_VERSION,
        "mapping_version": MAPPING_VERSION,
        "mode": mode,
        "activation": "manual",
        "cloud_model_budget_usd": 0,
        "license_attestation": False,
        "registry_fingerprint": _fingerprint({
            "capabilities": SECURITY_CAPABILITIES, "identity_evidence": IDENTITY_EVIDENCE,
        }),
        "source_policy_fingerprint": _fingerprint(SOURCE_POLICIES),
        "unapproved_uses": [
            "raw_storage", "model_processing", "commercial_redistribution",
            "excerpt_redistribution",
        ],
        "collector_targets": {
            "prices_technicals": list(SIGNAL_ELIGIBLE_TICKERS),
            "sec_cik_lookup": list(SIGNAL_ELIGIBLE_TICKERS),
            "options": list(OPTIONS_EARNINGS_TICKERS),
            "earnings": list(OPTIONS_EARNINGS_TICKERS),
            "social_attention": list(SOCIAL_ATTENTION_TICKERS),
            "fmp_symbols": list(fmp_acquisition_tickers(mode)),
            "apewisdom_all_stocks_pages": 5,
            "apewisdom_4chan_pages": 1,
            "fmp_primary_requests": 1,
            "fmp_max_requests_with_entitlement_fallback": 2,
            "gdelt_query_policy": "legacy-three-batches",
        },
    }
    manifest["fingerprint"] = _fingerprint(manifest)
    return manifest

