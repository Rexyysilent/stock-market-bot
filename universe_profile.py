"""Strict, bounded universe profiles. Parsing/planning needs only stdlib.

Profiles configure a U.S.-session pilot, not an exchange/security master.
Symbols and aliases are user assertions; validation is not provider coverage.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MAX_TICKERS = 64
MAX_PROFILE_BYTES = 128 * 1024
SYMBOL = re.compile(r"[A-Z][A-Z0-9-]{0,13}\Z")
GROUPS = {"uranium", "defense", "cash_runway"}
ROOT_KEYS = {"schema_version", "name", "equities", "funds", "futures", "indices",
             "focus_ticker", "groups", "baskets", "relative_return_pairs"}


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError(f"{label}: expected object with only {sorted(keys)}")
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected array")
    return value


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"{label}: duplicate symbols are not allowed")
    return values


def _symbol(value, kind="equity"):
    valid = isinstance(value, str)
    if valid and kind == "future":
        valid = value.endswith("=F") and bool(SYMBOL.fullmatch(value[:-2]))
    elif valid and kind == "index":
        valid = value.startswith("^") and bool(SYMBOL.fullmatch(value[1:]))
    elif valid:
        valid = bool(SYMBOL.fullmatch(value))
    if not valid:
        raise ValueError(f"Invalid {kind} symbol {value!r}; this pilot does not infer foreign exchange suffixes")
    return value


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_profile(raw):
    raw = _object(raw, ROOT_KEYS, "profile")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("profile.schema_version must be 1")
    name = raw.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ValueError("profile.name must be a lowercase slug of 1 to 64 characters")
    equities = []
    for entry in _list(raw.get("equities", []), "equities"):
        entry = _object(entry, {"symbol", "aliases"}, "equity")
        symbol = _symbol(entry.get("symbol"))
        aliases = _list(entry.get("aliases", []), f"{symbol}.aliases")
        if len(aliases) > 12 or any(not isinstance(a, str) or not 3 <= len(a.strip()) <= 100 for a in aliases):
            raise ValueError(f"{symbol}: supply at most 12 aliases, each 3 to 100 characters")
        equities.append({"symbol": symbol, "aliases": list(dict.fromkeys(a.strip().lower() for a in aliases))})
    profile = {"schema_version": 1, "name": name, "equities": equities}
    all_symbols = [entry["symbol"] for entry in equities]
    for plural, kind in (("funds", "fund"), ("futures", "future"), ("indices", "index")):
        values = [_symbol(value, kind) for value in _list(raw.get(plural, []), plural)]
        profile[plural] = values
        all_symbols.extend(values)
    _unique(all_symbols, "profile")
    if not 1 <= len(all_symbols) <= MAX_TICKERS:
        raise ValueError(f"active universe must contain 1 to {MAX_TICKERS} unique symbols")
    focus = raw.get("focus_ticker")
    if focus not in all_symbols:
        raise ValueError("focus_ticker must be in the active universe")
    profile["focus_ticker"] = focus
    equity_set = {entry["symbol"] for entry in equities}
    groups = _object(raw.get("groups", {}), GROUPS, "groups")
    profile["groups"] = {}
    for name in sorted(GROUPS):
        values = _list(groups.get(name, []), f"groups.{name}")
        eligible = equity_set | set(profile["funds"]) if name == "defense" else equity_set
        if any(not isinstance(value, str) or value not in eligible for value in values):
            raise ValueError(f"groups.{name} contains an unsupported or inactive symbol")
        profile["groups"][name] = _unique(values, f"groups.{name}")
    baskets = _object(raw.get("baskets", {}), {"growth", "defensive"}, "baskets")
    profile["baskets"] = {}
    for name in ("growth", "defensive"):
        values = _list(baskets.get(name, []), f"baskets.{name}")
        if any(not isinstance(value, str) or value not in all_symbols for value in values):
            raise ValueError(f"baskets.{name} must reference active symbols")
        profile["baskets"][name] = _unique(values, f"baskets.{name}")
    pairs = []
    for pair in _list(raw.get("relative_return_pairs", []), "relative_return_pairs"):
        pair = _object(pair, {"physical", "paper", "commodity"}, "relative return pair")
        if (pair.get("physical") not in all_symbols or pair.get("paper") not in all_symbols
                or pair["physical"] == pair["paper"]):
            raise ValueError("relative return pairs must reference two different active symbols")
        label = pair.get("commodity")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise ValueError("relative return pair label must contain 1 to 80 characters")
        pairs.append({"physical": pair["physical"], "paper": pair["paper"], "commodity": label.strip()})
    if len(pairs) > 16:
        raise ValueError("at most 16 relative return pairs are supported")
    profile["relative_return_pairs"] = pairs
    return profile


def load_profile(path):
    with Path(path).open("rb") as handle:
        content = handle.read(MAX_PROFILE_BYTES + 1)
    if len(content) > MAX_PROFILE_BYTES:
        raise ValueError("profile exceeds size limit")
    return validate_profile(json.loads(content, object_pairs_hook=_reject_duplicate_keys))


def fingerprint(profile):
    content = json.dumps(validate_profile(profile), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def profile_plan(profile):
    profile = validate_profile(profile)
    equities = [entry["symbol"] for entry in profile["equities"]]
    tickers = equities + profile["funds"] + profile["futures"] + profile["indices"]
    return {
        "name": profile["name"], "profile_sha256": fingerprint(profile),
        "active_count": len(tickers), "active_tickers": tickers,
        "pilot_cap": MAX_TICKERS, "comparison_calendar": "NYSE",
        "coverage_verified": False,
        "scope_notes": [
            "A syntactically valid symbol is not proof of listing, liquidity, optionability, or provider entitlement.",
            "The cap is a conservative pilot guard, not a measured throughput guarantee.",
            "Specialized clinical, social, and macro source queries keep their existing scope.",
            "Current-universe membership is not a survivorship-free historical security master.",
            "Futures and indices are context instruments; NYSE comparisons are not their native sessions.",
        ],
        "equity_count": len(equities), "fund_count": len(profile["funds"]),
        "aliases_missing": [entry["symbol"] for entry in profile["equities"] if not entry["aliases"]],
    }


def apply_profile(config, profile):
    """Apply before importing ANY agent/exporter/ledger module."""
    profile = validate_profile(profile)
    plan = profile_plan(profile)
    config.WATCHLIST_STOCKS = [entry["symbol"] for entry in profile["equities"]]
    config.WATCHLIST_COMMODITIES = profile["funds"] + profile["futures"]
    config.WATCHLIST_GAUGES = profile["indices"]
    config.WATCHLIST_URANIUM = profile["groups"]["uranium"]
    config.WATCHLIST_DEFENSE = profile["groups"]["defense"]
    config.WATCHLIST_VULTURE = profile["groups"]["cash_runway"]
    config.ALL_TICKERS = plan["active_tickers"]
    config.TICKER_ALIASES = {entry["symbol"]: entry["aliases"] for entry in profile["equities"]}
    config.FOCUS_TICKER = profile["focus_ticker"]
    # Existing brief.universe.name carries the complete profile fingerprint;
    # workspace/universe.json is the immutable corresponding configuration.
    config.UNIVERSE_NAME = f"{profile['name']}:{plan['profile_sha256']}"
    config.ETF_TICKERS = set(profile["funds"]) | {"SPY"}
    config.GROWTH_BASKET = profile["baskets"]["growth"]
    config.DEFENSIVE_BASKET = profile["baskets"]["defensive"]
    config.INSTRUMENT_RELATIVE_RETURN_PAIRS = profile["relative_return_pairs"]
