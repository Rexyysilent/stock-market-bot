"""Deterministic editorial-focus selection for the daily brief.

This module is deliberately presentation-only.  It consumes the already
selected, freshness-gated headline records and never writes state, changes the
configured universe, or creates signal/confluence/ledger eligibility.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import re
from urllib.parse import urlsplit

from config import (
    EDITORIAL_COVERAGE_MODE,
    EDITORIAL_ONLY_TICKER_SET,
    SIGNAL_ELIGIBLE_TICKER_SET,
)
from timeutil import to_utc_z


FOCUS_CONTRACT_VERSION = "2.7-editorial-focus-1"
SCORE_COMPONENT_KEYS = (
    "impact",
    "confidence",
    "novelty",
    "source_authority",
    "audience_relevance",
    "timeliness",
    "independent_corroboration",
    "unresolved_contradiction_penalty",
)

_CONFIDENCE_BY_SOURCE_CLASS = {
    "official": 3,
    "official_central_bank": 3,
    "official_exchange": 3,
    "official_regulator": 3,
    "aggregator": 2,
    "press_release": 2,
    "api_news_discovery": 1,
    "global_discovery": 1,
}

_STRICT_WEB_URL = re.compile(
    r"[Hh][Tt][Tt][Pp][Ss]?://"
    r"([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(\.([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*"
    r"(:[0-9]{1,5})?"
    r"(/([A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-Fa-f]{2}|/)*)?"
    r"([?]([A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-Fa-f]{2}|[/?])*)?"
    r"(\#([A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-Fa-f]{2}|[/?])*)?"
)
_SUPPORTED_SOURCE_TIME_KINDS = frozenset({
    "published", "event_time", "provider_seen",
})
_GENERIC_PUBLISHER_TOKENS = frozenset({
    "co", "company", "corp", "corporation", "group", "holdings", "inc",
    "limited", "llc", "ltd", "media", "network", "networks", "news",
    "official", "plc", "press", "the", "wire",
})


def _coverage_mode(value=None):
    mode = str(value or EDITORIAL_COVERAGE_MODE).strip().casefold()
    return mode if mode in {"off", "shadow", "active"} else "shadow"


def _focus_eligibility(ticker):
    if ticker in SIGNAL_ELIGIBLE_TICKER_SET:
        return {
            "coverage_tier": "instrumented",
            "signal_eligible": True,
            "signal_eligibility_reason": "instrumented_universe",
        }
    if ticker in EDITORIAL_ONLY_TICKER_SET:
        return {
            "coverage_tier": "editorial_only",
            "signal_eligible": False,
            "signal_eligibility_reason": "editorial_only_coverage",
        }
    return {
        "coverage_tier": None,
        "signal_eligible": None,
        "signal_eligibility_reason": "no_focus",
    }


def _bounded_int(value, minimum=0, maximum=3):
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return minimum
    return max(minimum, min(maximum, parsed))


def _string_list(value):
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    })


def _valid_web_url(value):
    if not isinstance(value, str) or not value:
        return False
    if _STRICT_WEB_URL.fullmatch(value) is None:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError):
        return False
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 0 <= port <= 65535)
    ):
        return False
    # The marked public schema accepts DNS hostnames, not address literals.
    # Reject IPv4-looking strings even when the stdlib rejects their octets.
    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        return re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", hostname) is None


def _required_clean_string(value):
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned and cleaned == value else None


def _publisher_aliases(value):
    """Return conservative comparison aliases for one publisher identity."""
    if not isinstance(value, str):
        return set()
    normalized = value.strip().casefold()
    if not normalized:
        return set()

    aliases = {re.sub(r"[^a-z0-9]+", "", normalized)}
    domain = normalized
    if "://" in domain:
        try:
            domain = urlsplit(domain).hostname or ""
        except (TypeError, ValueError):
            domain = ""
    domain = domain.rstrip(".")
    if re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z0-9-]+", domain):
        if domain.startswith("www."):
            domain = domain[4:]
        labels = domain.split(".")
        aliases.add(domain)
        aliases.add(re.sub(r"[^a-z0-9]+", "", labels[0]))
        if len(labels) > 1:
            aliases.add(re.sub(r"[^a-z0-9]+", "", labels[-2]))
    else:
        tokens = re.findall(r"[a-z0-9]+", normalized)
        semantic = "".join(
            token for token in tokens if token not in _GENERIC_PUBLISHER_TOKENS
        )
        if semantic:
            aliases.add(semantic)
    aliases.discard("")
    return aliases


def _independent_publisher_count(primary_publisher, publisher_domain, duplicates):
    primary_aliases = (
        _publisher_aliases(primary_publisher)
        | _publisher_aliases(publisher_domain)
    )
    if not primary_aliases:
        return 0

    independent = 0
    seen_aliases = set(primary_aliases)
    for duplicate in duplicates:
        aliases = _publisher_aliases(duplicate)
        if not aliases or aliases & seen_aliases:
            continue
        independent += 1

        seen_aliases.update(aliases)
    return min(3, independent)

def _parse_utc(value):
    normalized = to_utc_z(value)
    if normalized is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, None
    return normalized, parsed.astimezone(timezone.utc)


def _timeliness(as_of, generated_at):
    _, event_dt = _parse_utc(as_of)
    _, run_dt = _parse_utc(generated_at)
    if event_dt is None or run_dt is None:
        return 0
    age_seconds = (run_dt - event_dt).total_seconds()
    if age_seconds < -3600:
        return 0
    age_days = max(0.0, age_seconds / 86400.0)
    if age_days <= 1:
        return 3
    if age_days <= 2:
        return 2
    if age_days <= 3:
        return 1
    return 0


def _headline_tickers(row, universe):
    # Current headline-contract rows carry the deterministic entity mapping in
    # universe_tickers.  The fallback supports pre-projection unit fixtures;
    # it never performs title/substring inference.
    source = (
        row.get("universe_tickers")
        if "universe_tickers" in row
        else row.get("tickers")
    )
    return sorted({
        str(ticker).strip().upper()
        for ticker in (source or [])
        if str(ticker).strip().upper() in universe
    })



def _normalize_evidence(row, ticker, generated_at):
    source_record_id = _required_clean_string(row.get("source_record_id"))
    provider = _required_clean_string(row.get("provider"))
    source_class = _required_clean_string(row.get("source_class"))
    source_time_kind = row.get("source_time_kind")
    if (
        source_record_id is None
        or provider is None
        or source_class is None
        or source_time_kind not in _SUPPORTED_SOURCE_TIME_KINDS
    ):
        return None

    title = str(row.get("title") or row.get("text") or "").strip()
    link = row.get("link")
    provider_seen_at = row.get("provider_seen_at")
    provider_seen_time = source_time_kind == "provider_seen"
    as_of, as_of_dt = _parse_utc(
        row.get("as_of") or (
            provider_seen_at if provider_seen_time else row.get("published")
        )
    )
    if not title or not _valid_web_url(link) or as_of_dt is None:
        return None

    canonical_url = row.get("canonical_url")
    if canonical_url is not None and not _valid_web_url(canonical_url):
        return None
    observed_at, _ = _parse_utc(None if provider_seen_time else provider_seen_at)
    raw_components = row.get("score_components")
    components = dict(raw_components) if isinstance(raw_components, dict) else {}
    source_authority = _bounded_int(components.get("authority"))
    confidence = _CONFIDENCE_BY_SOURCE_CLASS.get(source_class, 1)
    timeliness = _timeliness(as_of, generated_at)
    contradictions = row.get("contradictions") or []
    contradiction_count = len(contradictions) if isinstance(contradictions, list) else 0
    if row.get("contradiction"):
        contradiction_count += 1
    primary_publisher = (
        row.get("publisher") if isinstance(row.get("publisher"), str) else None
    )
    publisher_domain = (
        row.get("publisher_domain")
        if isinstance(row.get("publisher_domain"), str)
        else None
    )
    duplicate_publishers = _string_list(row.get("duplicate_publishers"))
    independent_publisher_count = _independent_publisher_count(
        primary_publisher, publisher_domain, duplicate_publishers
    )

    return {
        "evidence_id": source_record_id,
        "source_record_id": source_record_id,
        "ticker": ticker,
        "title": title,
        "link": link,
        "canonical_url": canonical_url,
        "provider": provider,
        "publisher": primary_publisher,
        "publisher_domain": publisher_domain,
        "source_class": source_class,
        "source_time_kind": source_time_kind,
        "duplicate_providers": _string_list(row.get("duplicate_providers")),
        "duplicate_publishers": duplicate_publishers,
        "as_of": as_of,
        "observed_at": observed_at,
        "impact": _bounded_int(components.get("impact")),
        "confidence": confidence,
        "novelty": _bounded_int(components.get("novelty")),
        "source_authority": source_authority,
        "audience_relevance": _bounded_int(components.get("issuer_relevance")),
        "timeliness": timeliness,
        "independent_corroboration": independent_publisher_count,
        "contradiction_count": contradiction_count,
    }


def _candidate_sort_key(row):
    components = row["score_components"]
    _, as_of_dt = _parse_utc(row["evidence"][0].get("as_of"))
    timestamp = as_of_dt.timestamp() if as_of_dt is not None else 0.0
    return (
        -components["impact"],
        -components["confidence"],
        -components["source_authority"],
        -components["timeliness"],
        -components["independent_corroboration"],
        -components["audience_relevance"],
        -components["novelty"],
        components["unresolved_contradiction_penalty"],
        -timestamp,
        row["ticker"],
        row["primary_evidence_id"],
        json.dumps(
            row["evidence"][0],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def rank_focus_candidates(headlines, universe_tickers, generated_at):
    """Return deterministic eligible candidates from fresh selected headlines.

    The caller must pass the NewsAgent's selected/scored records, never the raw
    or dropped pool.  This function still fail-closes on lane, mapped ticker,
    URL, source time, and materiality so a configured focus cannot create an
    empty ticker shell.
    """
    universe = {
        str(ticker).strip().upper()
        for ticker in (universe_tickers or [])
        if str(ticker).strip()
    }
    candidates_by_evidence = {}
    for raw in headlines or []:
        if not isinstance(raw, dict) or raw.get("lane") != "universe":
            continue
        for ticker in _headline_tickers(raw, universe):
            evidence = _normalize_evidence(raw, ticker, generated_at)
            if evidence is None:
                continue
            components = {
                "impact": evidence["impact"],
                "confidence": evidence["confidence"],
                "novelty": evidence["novelty"],
                "source_authority": evidence["source_authority"],
                "audience_relevance": evidence["audience_relevance"],
                "timeliness": evidence["timeliness"],
                "independent_corroboration": evidence["independent_corroboration"],
                "unresolved_contradiction_penalty": min(
                    3, evidence["contradiction_count"]
                ),
            }
            # A focus is one supported development, not an issuer-level blend
            # of unrelated stories. This prevents component maxima from being
            # borrowed across separate headlines for the same ticker.
            if (
                components["impact"] < 1
                or components["audience_relevance"] < 1
                or components["timeliness"] < 1
            ):
                continue
            candidate = {
                "ticker": ticker,
                "score_components": components,
                "primary_evidence_id": evidence["evidence_id"],
                "evidence": [evidence],
            }
            identity = (ticker, evidence["evidence_id"])
            current = candidates_by_evidence.get(identity)
            if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
                candidates_by_evidence[identity] = candidate

    return sorted(candidates_by_evidence.values(), key=_candidate_sort_key)


def _evidence_grade(candidate):
    components = candidate["score_components"]
    if components["independent_corroboration"] and components["source_authority"] >= 2:
        return "multiple_selected_sources"
    if components["source_authority"] >= 3:
        return "official_source"
    if components["source_authority"] >= 2:
        return "single_source_secondary"
    return "limited_source"


def _no_focus(requested_ticker, pin_rejection_reason, candidate_count):
    return {
        "status": "no_focus",
        **_focus_eligibility(None),
        "selection_mode": "none",
        "ticker": None,
        "entity_ids": [],
        "requested_ticker": requested_ticker,
        "pin_rejection_reason": pin_rejection_reason,
        "reason": "insufficient_supported_evidence",
        "eligible_candidate_count": candidate_count,
        "score_components": None,
        "headline": None,
        "what_changed": None,
        "why_it_matters": None,
        "evidence_grade": None,
        "market_reaction": None,
        "next_checkpoint": None,
        "contradictions_assessed": False,
        "contradictions": [],
        "as_of": None,
        "observed_at": None,
        "evidence": [],
        # Compatibility projection for the pre-PR3 deep_dive object.
        "note": "No editorial focus met the mapped fresh-evidence threshold.",
        "sec_filing_accessions": [],
        "has_insider_cluster": False,
        "social_mentions": [],
        "news_headlines": [],
        "twitter_signals": [],
    }


def select_focus(
    headlines,
    focus_eligible_tickers,
    generated_at,
    configured_ticker=None,
    *,
    coverage_tickers=None,
    coverage_mode=None,
):
    """Select configured focus when supported, otherwise deterministic dynamic.

    A configured ticker is an editorial override only.  It must be inside the
    configured universe and independently pass the same usable-evidence gate.
    """
    focus_universe = {
        str(ticker).strip().upper()
        for ticker in (focus_eligible_tickers or [])
        if str(ticker).strip()
    }
    configured_universe = {
        str(ticker).strip().upper()
        for ticker in (
            coverage_tickers
            if coverage_tickers is not None
            else focus_eligible_tickers
        )
        if str(ticker).strip()
    }
    mode = _coverage_mode(coverage_mode)
    universe = set(focus_universe)
    if mode != "active":
        universe &= SIGNAL_ELIGIBLE_TICKER_SET
    requested = str(configured_ticker or "").strip().upper() or None
    ranked = rank_focus_candidates(headlines, universe, generated_at)
    by_ticker = {}
    for candidate in ranked:
        by_ticker.setdefault(candidate["ticker"], candidate)
    selected = None
    selection_mode = "none"
    pin_rejection_reason = None

    if requested is not None:
        if (
            requested in configured_universe
            and requested in EDITORIAL_ONLY_TICKER_SET
            and mode != "active"
        ):
            pin_rejection_reason = "editorial_coverage_not_active"
        elif requested not in configured_universe:
            pin_rejection_reason = "unmapped_or_outside_universe"
        elif requested not in by_ticker:
            pin_rejection_reason = "no_usable_fresh_evidence"
        else:
            selected = by_ticker[requested]
            selection_mode = "configured"

    if selected is None and ranked:
        selected = ranked[0]
        selection_mode = "dynamic"

    if selected is None:
        return _no_focus(requested, pin_rejection_reason, len(ranked))

    evidence = deepcopy(selected["evidence"])
    primary = next(
        row for row in evidence
        if row["evidence_id"] == selected["primary_evidence_id"]
    )
    evidence_ids = [row["evidence_id"] for row in evidence]
    rationale = (
        f"{selected['ticker']} is the configured editorial focus and has fresh "
        "mapped headline evidence; this is not an investment recommendation."
        if selection_mode == "configured"
        else
        f"{selected['ticker']} is the highest-ranked mapped issuer with fresh "
        "headline evidence under the deterministic editorial materiality "
        "rubric; this is not an investment recommendation."
    )
    return {
        "status": "selected",
        **_focus_eligibility(selected["ticker"]),
        "selection_mode": selection_mode,
        "ticker": selected["ticker"],
        "entity_ids": [f"ticker:{selected['ticker']}"],
        "requested_ticker": requested,
        "pin_rejection_reason": pin_rejection_reason,
        "reason": (
            "configured_focus_supported"
            if selection_mode == "configured"
            else "highest_ranked_supported_candidate"
        ),
        "eligible_candidate_count": len(ranked),
        "score_components": deepcopy(selected["score_components"]),
        "headline": primary["title"],
        "what_changed": {
            "text": primary["title"],
            "evidence_ids": [primary["evidence_id"]],
        },
        "why_it_matters": {
            "text": rationale,
            "evidence_ids": evidence_ids,
        },
        "evidence_grade": _evidence_grade(selected),
        # PR6 timestamp/window semantics and PR8 next-checkpoint provenance are
        # not complete.  Do not manufacture these fields from nearby data.
        "market_reaction": None,
        "next_checkpoint": None,
        "contradictions_assessed": False,
        "contradictions": [],
        "as_of": primary["as_of"],
        "observed_at": primary["observed_at"],
        "evidence": evidence,
        # Compatibility projection for consumers of sections.deep_dive.
        "note": (
            "Dynamic editorial focus selected from fresh mapped headline "
            "evidence; numeric records remain in their canonical sections."
        ),
        "sec_filing_accessions": [],
        "has_insider_cluster": False,
        "social_mentions": [],
        "news_headlines": [row["title"] for row in evidence],
        "twitter_signals": [],
    }


def _mentions_ticker(text, ticker):
    return bool(re.search(
        rf"(?<![A-Za-z0-9])\$?{re.escape(ticker)}(?![A-Za-z0-9])",
        str(text or ""),
        flags=re.IGNORECASE,
    ))


def attach_legacy_context(focus, sec_filings=None, insider_clusters=None,
                          linked_whispers=None, twitter_signals=None):
    """Add dynamic compatibility fields without affecting selection/scoring."""
    result = deepcopy(focus)
    ticker = result.get("ticker")
    if result.get("status") != "selected" or not ticker:
        return result
    result["sec_filing_accessions"] = sorted({
        str(row.get("accession_number"))
        for row in (sec_filings or [])
        if str(row.get("ticker") or "").upper() == ticker
        and row.get("accession_number")
    })
    result["has_insider_cluster"] = any(
        str(row.get("ticker") or "").upper() == ticker
        for row in (insider_clusters or [])
    )
    result["social_mentions"] = sorted({
        str(row.get("text"))
        for row in (linked_whispers or [])
        if ticker in {
            str(value).upper() for value in (row.get("tickers") or [])
        }
        and row.get("text")
    })
    result["twitter_signals"] = sorted(
        [
            deepcopy(row)
            for row in (twitter_signals or [])
            if _mentions_ticker(row.get("query"), ticker)
            or _mentions_ticker(row.get("title"), ticker)
        ],
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("link") or ""),
            str(row.get("title") or ""),
        ),
    )
    return result


def render_focus_text(focus):
    """Render a compact evidence-linked card; no-focus renders no section."""
    if not focus or focus.get("status") != "selected":
        return ""
    ticker = focus["ticker"]
    evidence_by_id = {
        row["evidence_id"]: row for row in focus.get("evidence", [])
    }

    def claim_lines(label, claim):
        if not isinstance(claim, dict) or not str(claim.get("text") or "").strip():
            return None
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            return None
        links = []
        for evidence_id in evidence_ids:
            row = evidence_by_id.get(evidence_id)
            if row is None or not _valid_web_url(row.get("link")):
                return None
            if row["link"] not in links:
                links.append(row["link"])
        lines = [f"- {label}: {claim['text']}"]
        lines.extend(f"  Evidence: {link}" for link in links)
        return lines

    what_changed_lines = claim_lines("What changed", focus.get("what_changed"))
    why_it_matters_lines = claim_lines("Why it matters", focus.get("why_it_matters"))
    if what_changed_lines is None or why_it_matters_lines is None:
        return ""
    components = focus["score_components"]
    lines = [
        "=" * 70,
        f"## EDITORIAL FOCUS — {ticker}",
        "=" * 70,
        "",
    ]
    lines.extend(what_changed_lines)
    lines.extend(why_it_matters_lines)
    lines.extend([
        f"- Evidence grade: {focus['evidence_grade']}",
        "- Editorial score components: " + ", ".join(
            f"{key}={components[key]}" for key in SCORE_COMPONENT_KEYS
        ),
    ])
    if focus.get("market_reaction") is not None:
        lines.append(f"- Market reaction: {focus['market_reaction']}")
    if focus.get("next_checkpoint") is not None:
        lines.append(f"- Next checkpoint: {focus['next_checkpoint']}")
    lines.append("")
    return "\n".join(lines)

