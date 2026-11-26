"""Read-only listing coverage diagnostics over the registry and captured directories.

The denominator is a current captured Nasdaq directory subset. It is not a
survivorship-free historical universe, a liquidity screen, or missed-trade recall.
No registry membership, collector target, or listing state is changed here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re

from instrument_registry import Registry, TIERS
from registry_snapshot import DIRECTORY_PARTS, _instant, _load_parts, directory_records

CONTRACT = "listing-coverage-audit-1"
STALE_AFTER_HOURS = 72
SAMPLE_LIMIT = 100
CATALOGUE_TYPES = {"common_stock", "adr", "exchange_traded_product"}
DIRECTORY_DEFINITIONS = "https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs"


def _counter(values):
    return dict(sorted(Counter(values).items()))


def _age(value, now):
    if not value:
        return {"captured_at": None, "age_hours": None, "freshness": "unknown"}
    try:
        stamp = datetime.fromisoformat(_instant(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {"captured_at": value, "age_hours": None, "freshness": "invalid_time"}
    hours = (now - stamp).total_seconds() / 3600
    return {"captured_at": _instant(stamp), "age_hours": round(hours, 3),
            "freshness": "future" if hours < 0 else "stale" if hours > STALE_AFTER_HOURS else "recent"}


def _security_class(row):
    """Name-derived classes are heuristics; only the ETF flag is explicit."""
    text = row["Security Name"].casefold()
    if row.get("ETF") == "Y":
        return "exchange_traded_product"
    for kind, pattern in (
            ("warrant", r"\bwarrants?\b"), ("right", r"\brights?\b"),
            ("unit", r"\bunits?\b"), ("preferred_stock", r"\bpreferred\b"),
            ("debt_or_note", r"\b(?:notes?|bonds?|debentures?)\b"),
            ("adr", r"\bamerican depositary (?:shares?|receipts?)\b"),
            ("common_stock", r"\b(?:common (?:stock|shares)|ordinary shares|subordinate voting shares)\b")):
        if re.search(pattern, text):
            return kind
    return "unknown"


def _registry_summary(registry, now):
    instruments = list(registry.instruments.values())
    by_symbol = defaultdict(list)
    for listing in registry.listings:
        day = now.date().isoformat()
        if ((listing["valid_from"] is None or listing["valid_from"] <= day)
                and (listing["valid_to"] is None or day < listing["valid_to"])
                and listing["known_from"] <= _instant(now)):
            by_symbol[listing["symbol"].upper()].append(listing)
    ambiguous = {s for s, rows in by_symbol.items()
                 if len({r["instrument_id"] for r in rows}) > 1}
    tier_symbols = {tier: set() for tier in TIERS}
    for symbol, rows in by_symbol.items():
        if symbol not in ambiguous:
            tier_symbols[registry.instruments[rows[0]["instrument_id"]]["tier"]].add(symbol)
    capabilities = list(registry.capabilities.values())
    observed = {c["instrument_id"] for c in capabilities if c.get("status") != "unknown"
                and _age(c.get("checked_at"), now)["freshness"] in ("recent", "stale")}
    snapshots = []
    for part in DIRECTORY_PARTS + ("sec_company_tickers_exchange",):
        records = [s for s in registry.snapshots if s.get("part") == part
                   and s.get("status") == "captured"]
        latest = max(records, key=lambda s: s.get("captured_at") or "", default=None)
        snapshots.append({"part": part, **_age(latest.get("captured_at") if latest else None, now),
                          "file_creation_time": latest.get("file_creation_time") if latest else None})
    return {
        "version": registry.version, "fingerprint": registry.fingerprint(),
        "instruments": len(instruments), "issuers": len(registry.issuers),
        "listing_rows": len(registry.listings),
        "by_tier": _counter(i["tier"] for i in instruments),
        "by_verification": _counter(i["verification"] for i in instruments),
        "by_security_type": _counter(i["type"] for i in instruments),
        "known_active_symbols_by_tier": {k: sorted(v) for k, v in tier_symbols.items()},
        "ambiguous_active_symbols": sorted(ambiguous),
        "unknown_listing_fields": {
            k: sum(r.get(k) is None for r in registry.listings)
            for k in ("valid_from", "venue", "calendar", "currency", "price_scale")},
        "capabilities": {
            "records": len(capabilities), "by_status": _counter(c["status"] for c in capabilities),
            "instruments_without_dated_observation": len(registry.instruments.keys() - observed),
            "provider_bindings": len(registry.bindings),
            "meaning": "An unobserved capability is unknown, not unsupported or available.",
        },
        "latest_captures": snapshots,
        "scope": "Metadata in the supplied registry version; this audit does not reconstruct earlier registry versions.",
    }, tier_symbols


def build_listing_audit(registry, snapshot_folder=None, as_of=None):
    """Build deterministic coverage diagnostics without requests or mutations."""
    if not isinstance(registry, Registry):
        registry = Registry(registry)
    normalized = _instant(as_of)
    now = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    summary, tier_symbols = _registry_summary(registry, now)
    report = {
        "contract": CONTRACT, "as_of": normalized, "registry": summary,
        "freshness_threshold_hours": STALE_AFTER_HOURS,
        "snapshot": {"status": "not_provided", "directory_complete": False,
                     "full_snapshot_complete": False},
        "coverage": {"denominator": None, "instrumented_fraction": None,
                     "reason": "no_captured_directory_snapshot"},
        "historical_market_recall": {"available": False,
                                     "reason": "current_directories_do_not_establish_historical_membership_or_trade_outcomes"},
        "activations": [], "inferred_delistings": [],
        "limitations": [
            "A configured symbol is a collection target, not evidence of usable prices or provider capability.",
            "Directory symbols are listing/protocol identifiers; provider aliases and issuer identities are not inferred.",
            "Security classes inferred from names are conservative diagnostics, not verified identities.",
            "No liquidity, price, borrow, corporate-action, or execution eligibility is established by a directory.",
        ],
        "sources": [{"title": "Nasdaq Symbol Directory Definitions", "url": DIRECTORY_DEFINITIONS}],
    }
    if snapshot_folder is None:
        return report
    try:
        manifest, parts = _load_parts(snapshot_folder)
    except (OSError, ValueError, TypeError, KeyError) as error:
        report["snapshot"]["status"] = "unavailable"
        report["snapshot"]["error"] = type(error).__name__
        report["coverage"]["reason"] = "snapshot_unreadable_or_invalid"
        return report
    age = _age(manifest["captured_at"], now)
    records, creation, diagnostics = directory_records(parts)
    directory_complete = manifest["directory_complete"] and all(
        diagnostics[p]["complete"] for p in DIRECTORY_PARTS)
    source_dates = {}
    for part in DIRECTORY_PARTS:
        raw = creation.get(part)
        try:
            date = datetime.strptime(raw, "%m%d%Y%H:%M").date() if raw else None
        except ValueError:
            date = None
        source_dates[part] = {"file_creation_time": raw, "timezone": "not_provided",
                              "source_date": date.isoformat() if date else None,
                              "age_calendar_days": (now.date() - date).days if date else None}
    stale_source = any(v["age_calendar_days"] is not None and v["age_calendar_days"] > 3
                       for v in source_dates.values())
    # The upstream footer has no zone. Allow the one-date offset that a
    # timezone boundary can cause; a source date further ahead is unusable.
    future_source = any(v["age_calendar_days"] is not None and v["age_calendar_days"] < -1
                        for v in source_dates.values())
    snapshot_status = ("future_snapshot" if age["freshness"] == "future" else
                       "future_source_date" if future_source else
                       "partial" if not directory_complete else
                       "stale" if age["freshness"] == "stale" or stale_source else "complete")
    report["snapshot"] = {
        "status": snapshot_status, **age,
        "directory_complete": directory_complete,
        "full_snapshot_complete": manifest["complete"],
        "requested_complete": manifest["requested_complete"],
        "parts": {p: {"status": r.get("status"), "sha256": r.get("sha256"),
                       "bytes": r.get("bytes")} for p, r in manifest["parts"].items()},
        "directory_parse": diagnostics, "upstream_creation": source_dates,
    }
    if snapshot_status in ("future_snapshot", "future_source_date"):
        report["coverage"]["reason"] = ("snapshot_not_known_at_audit_cutoff"
                                        if snapshot_status == "future_snapshot"
                                        else "upstream_directory_date_is_in_the_future")
        return report

    occurrences = Counter(r["_symbol"] for r in records)
    duplicates = {s for s, n in occurrences.items() if n > 1}
    included, excluded, classes, financial_flags = {}, Counter(), Counter(), Counter()
    excluded_symbols = defaultdict(set)
    for row in records:
        symbol = row["_symbol"]
        kind = _security_class(row)
        classes[kind] += 1
        flags = []
        if symbol in duplicates:
            flags.append("ambiguous_duplicate_symbol")
        if row.get("Test Issue") != "N":
            flags.append("test_issue" if row.get("Test Issue") == "Y" else "unknown_test_flag")
        if row.get("ETF") not in ("Y", "N"):
            flags.append("unknown_etf_flag")
        if row["_venue"] is None:
            flags.append("unknown_venue")
        if kind not in CATALOGUE_TYPES:
            flags.append(f"security_class_{kind}")
        if not diagnostics[row["_part"]]["complete"]:
            flags.append("incomplete_directory_part")
        if flags:
            # Counts are by reason and may overlap. The unique excluded count
            # below is the additive population complement.
            excluded.update(flags)
            for flag in flags:
                excluded_symbols[flag].add(symbol)
            continue
        included[symbol] = row
        financial_flags[row.get("Financial Status") or "not_provided"] += 1
    denominator = len(included)
    all_targets = set().union(*tier_symbols.values())
    uncovered = sorted(set(included) - all_targets)
    covered = {tier: sorted(symbols & set(included)) for tier, symbols in tier_symbols.items()}
    fraction = len(covered["instrumented"]) / denominator if directory_complete and denominator else None
    report["coverage"] = {
        "scope": "Unambiguous, non-test common-share, ADR and ETF candidates in this captured directory; name classification is heuristic.",
        "snapshot_captured_at": manifest["captured_at"],
        "denominator": denominator if directory_complete else None,
        "observed_eligible_subset": denominator, "raw_directory_rows": len(records),
        "unique_observed_symbols": len(occurrences),
        "excluded_unique_symbols": len(set(occurrences) - set(included)),
        "exclusion_counts_by_reason": dict(sorted(excluded.items())),
        "exclusion_counts_overlap": True,
        "observed_security_classes": dict(sorted(classes.items())),
        "eligible_financial_status_flags": dict(sorted(financial_flags.items())),
        "ambiguous_directory_symbols": sorted(duplicates),
        "covered_by_tier": {tier: {"count": len(symbols), "symbols": symbols}
                            for tier, symbols in covered.items()},
        "instrumented_fraction": fraction,
        "reason": None if directory_complete and denominator else
                  "incomplete_directory_snapshot" if not directory_complete else "no_eligible_directory_members",
        "uncovered_count": len(uncovered), "uncovered_symbol_sample": uncovered[:SAMPLE_LIMIT],
        "sample_limit": SAMPLE_LIMIT, "sample_truncated": len(uncovered) > SAMPLE_LIMIT,
        "configured_not_observed": sorted(all_targets - set(occurrences)),
        "configured_exclusions": {reason: sorted(symbols & all_targets)
                                  for reason, symbols in sorted(excluded_symbols.items())
                                  if symbols & all_targets},
        "interpretation": "Collection coverage only; financial-condition flags are retained, and no absence implies delisting.",
    }
    return report
