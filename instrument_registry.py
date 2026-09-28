"""Dated instrument registry (T9): issuer -> instrument -> listing -> provider binding.

A ticker is not an identity. Symbols are reused, share classes trade
separately, and renames move a class to a new symbol. The registry separates:

  issuer        legal entity (a CIK identifies an SEC filer, not a share class)
  instrument    one tradable class: type, share class, coverage tier
  listing       instrument on a venue under a symbol, with native calendar,
                quotation currency and scale, a validity interval
                [valid_from, valid_to) and the time it became known
  binding       provider symbol for a listing (for example yfinance)
  capability    observed provider/endpoint status, never inferred

Rules (R01-R04, R06, R07 of the v3.8 acceptance specification):
- A missing date with a reused symbol is ambiguous, never a guess.
- A rename changes the listing, not the instrument; classes never join prices.
- Absence or a failed fetch is not delisting. A terminal state needs a dated
  source assertion.
- Cross-calendar or cross-currency comparisons need an explicit session
  alignment and a dated FX convention. A native-currency return needs neither.
- Directory snapshots never infer delistings or activate anything.
- Catalogue candidates are a separate tier and leave the protected cohort and
  the coverage-policy manifest unchanged.

History is built forward. A seeded listing's start is unknown (valid_from
null), and knowledge starts when the registry learned it, so strict
known-by queries for older archives return unknown rather than borrowing
today's knowledge.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

CONTRACT = "instrument-registry-1"
DEFAULT_PATH = Path(__file__).resolve().parent / "registry" / "instrument-registry.json"
SEED_VERSION = "seed-config-2026-09-28"
TIERS =("instrumented", "editorial_only", "catalogue_candidate")
TYPES = ("common_stock", "preferred_stock", "adr", "exchange_traded_product", "index",
         "future", "continuous_future_proxy", "option", "unknown")
VERIFICATION = ("primary_verified", "snapshot_matched", "legacy_profile", "unverified")
CAPABILITY_STATUSES = ("supported", "unsupported", "unavailable", "stale", "unknown")
TERMINAL_STATES = ("delisted", "merged", "acquired", "matured")
VENUE_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "NYSE ARCA": "ARCX", "NYSE AMERICAN": "XASE"}
US_EQUITY_VENUES = {"XNAS", "XNYS", "ARCX", "XASE", "BATS"}


def _day(value):
    if value is None:
        return None
    text = str(value).strip()
    datetime.strptime(text[:10], "%Y-%m-%d")
    return text[:10]


def _instant(value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"instant {value!r} needs an explicit time zone")
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now():
    """Knowledge time for facts the registry learns now; never a fixed past date."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


class Registry:
    def __init__(self, data):
        data = copy.deepcopy(data)
        _require(data.get("contract") == CONTRACT, f"registry contract must be {CONTRACT}")
        self.version = data.get("version")
        self.snapshots = list(data.get("snapshots", []))
        self.issuers = {i["issuer_id"]: i for i in data.get("issuers", [])}
        self.instruments = {}
        for item in data.get("instruments", []):
            _require(item["instrument_id"] not in self.instruments,
                     f"duplicate instrument {item['instrument_id']}")
            _require(item["tier"] in TIERS, f"unknown tier {item['tier']!r}")
            _require(item["type"] in TYPES, f"unknown instrument type {item['type']!r}")
            _require(item["verification"] in VERIFICATION,
                     f"unknown verification {item['verification']!r}")
            _require(item["issuer_id"] is None or item["issuer_id"] in self.issuers,
                     f"unknown issuer {item['issuer_id']!r}")
            self.instruments[item["instrument_id"]] = item
        self.listings = []
        for row in data.get("listings", []):
            _require(row["instrument_id"] in self.instruments,
                     f"listing {row['listing_id']} names an unknown instrument")
            row["valid_from"], row["valid_to"] = _day(row["valid_from"]), _day(row["valid_to"])
            row["known_from"] = _instant(row["known_from"])
            _require(row["known_from"] is not None, f"listing {row['listing_id']} needs known_from")
            _require(row["valid_from"] is None or row["valid_to"] is None
                     or row["valid_from"] < row["valid_to"],
                     f"listing {row['listing_id']} has an empty validity interval")
            terminal = row.get("terminal")
            if terminal is not None:
                _require(terminal.get("state") in TERMINAL_STATES
                         and terminal.get("asserted_at") and terminal.get("evidence")
                         and row["valid_to"] is not None,
                         f"listing {row['listing_id']}: a terminal state needs a dated source "
                         "assertion and a valid_to; absence from a feed is not one")
                _day(terminal["asserted_at"])
            self.listings.append(row)
        self.bindings = list(data.get("provider_bindings", []))
        self.capabilities = {}
        for cap in data.get("capabilities", []):
            _require(cap.get("status") in CAPABILITY_STATUSES,
                     f"unknown capability status {cap.get('status')!r}")
            _require(cap["instrument_id"] in self.instruments,
                     "capability names an unknown instrument")
            self.capabilities[(cap["instrument_id"], cap["provider"], cap["endpoint"])] = cap

    # ------------------------------------------------------------ identity
    def to_dict(self):
        return {"contract": CONTRACT, "version": self.version, "snapshots": self.snapshots,
                "issuers": list(self.issuers.values()),
                "instruments": list(self.instruments.values()),
                "listings": self.listings, "provider_bindings": self.bindings,
                "capabilities": list(self.capabilities.values())}

    def fingerprint(self):
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def instrument(self, instrument_id):
        return copy.deepcopy(self.instruments[instrument_id])

    def _active(self, row, on):
        return ((row["valid_from"] is None or row["valid_from"] <= on)
                and (row["valid_to"] is None or on < row["valid_to"]))

    def resolve(self, symbol, *, on=None, known_by=None):
        """Symbol -> instrument as of a date; ambiguity and unknowns are explicit."""
        symbol = str(symbol).strip().upper()
        on, known_by = _day(on), _instant(known_by)
        rows = [r for r in self.listings if r["symbol"].upper() == symbol
                and (known_by is None or r["known_from"] <= known_by)]
        if on is not None:
            rows = [r for r in rows if self._active(r, on)]
        instruments = sorted({r["instrument_id"] for r in rows})
        result = {"symbol": symbol, "on": on, "known_by": known_by, "instrument_id": None,
                  "issuer_id": None, "listing": None, "candidates": instruments, "history": None}
        if not instruments:
            return dict(result, status="unknown")
        if len(instruments) > 1:
            return dict(result, status="ambiguous")
        listing = max(rows, key=lambda r: r["valid_from"] or "")
        instrument_id = instruments[0]
        return dict(result, status="resolved", instrument_id=instrument_id,
                    issuer_id=self.instruments[instrument_id]["issuer_id"],
                    listing=copy.deepcopy(listing),
                    history="start_unknown" if listing["valid_from"] is None else "dated")

    def _listing_on(self, instrument_id, on):
        rows = [r for r in self.listings if r["instrument_id"] == instrument_id
                and self._active(r, on)]
        return max(rows, key=lambda r: r["valid_from"] or "") if rows else None

    def listing_state(self, instrument_id, *, on):
        """Listed, terminal (only from a dated assertion), or unknown."""
        on = _day(on)
        current = self._listing_on(instrument_id, on)
        if current is not None:
            return {"state": "listed_as_last_known" if current["valid_to"] is None else "listed",
                    "listing_id": current["listing_id"], "evidence": current["evidence"]}
        ended = [r for r in self.listings if r["instrument_id"] == instrument_id
                 and r["valid_to"] is not None and r["valid_to"] <= on and r.get("terminal")]
        if ended:
            row = max(ended, key=lambda r: r["valid_to"])
            return {"state": row["terminal"]["state"], "listing_id": row["listing_id"],
                    "asserted_at": row["terminal"]["asserted_at"],
                    "evidence": row["terminal"]["evidence"]}
        return {"state": "unknown", "listing_id": None, "evidence": []}

    def price_join_allowed(self, first, second):
        """Prices join only within one instrument; share classes never mix."""
        return first == second and first in self.instruments

    def capability(self, instrument_id, provider, endpoint):
        return copy.deepcopy(self.capabilities.get(
            (instrument_id, provider, endpoint),
            {"instrument_id": instrument_id, "provider": provider, "endpoint": endpoint,
             "status": "unknown", "checked_at": None, "cause": "never_observed"}))

    # ------------------------------------------------------------ comparison
    def native_return_basis(self, instrument_id, *, on):
        listing = self._listing_on(instrument_id, _day(on))
        if listing is None or not listing["currency"] or not listing["calendar"]:
            return {"allowed": False, "reason": "basis_unknown", "currency": None, "calendar": None}
        return {"allowed": True, "reason": None, "currency": listing["currency"],
                "calendar": listing["calendar"], "price_scale": listing["price_scale"]}

    def comparison(self, first, second, *, on, session_alignment=None, fx_convention=None):
        """Refuse an unqualified cross-calendar/cross-currency comparison (R04)."""
        on = _day(on)
        a, b = self._listing_on(first, on), self._listing_on(second, on)
        reasons = []
        if a is None or b is None or not all((a["calendar"], a["currency"],
                                              b["calendar"], b["currency"])):
            reasons.append("basis_unknown")
        else:
            if a["calendar"] != b["calendar"] and not session_alignment:
                reasons.append("calendar_mismatch")
            dated_fx = isinstance(fx_convention, dict) and all(
                fx_convention.get(k) for k in ("pair", "as_of", "source"))
            if (a["currency"], a["price_scale"]) != (b["currency"], b["price_scale"]) and not dated_fx:
                reasons.append("currency_mismatch")
        return {"allowed": not reasons, "reasons": reasons, "on": on,
                "session_alignment": session_alignment, "fx_convention": fx_convention,
                "bases": [None if r is None else {k: r[k] for k in ("calendar", "currency", "price_scale")}
                          for r in (a, b)]}

    # ------------------------------------------------------------ tiers
    def tier_symbols(self, tier):
        symbols = []
        for instrument_id, item in self.instruments.items():
            if item["tier"] != tier:
                continue
            open_rows = [r for r in self.listings
                         if r["instrument_id"] == instrument_id and r["valid_to"] is None]
            symbols.extend(r["symbol"] for r in open_rows)
        return symbols

    def with_candidates(self, candidates):
        """A new registry with catalogue-only candidates; nothing is activated."""
        data = self.to_dict()
        learned = _now()
        registered = {r["symbol"].upper() for r in self.listings if r["valid_to"] is None}
        for candidate in candidates:
            symbol = str(candidate["symbol"]).strip().upper()
            _require(symbol not in registered,
                     f"{symbol} is already registered; catalogue candidates must be new symbols")
            _require(candidate.get("evidence"), f"catalogue candidate {symbol} needs evidence")
            registered.add(symbol)
            issuer_id = f"candidate:{symbol}"
            data["issuers"].append({"issuer_id": issuer_id, "legal_name": candidate.get("legal_name"),
                                    "cik": candidate.get("cik"), "evidence": candidate["evidence"],
                                    "verification": "unverified"})
            instrument_id = f"candidate:{symbol}"
            data["instruments"].append({"instrument_id": instrument_id, "issuer_id": issuer_id,
                                        "type": "unknown", "share_class": None,
                                        "tier": "catalogue_candidate",
                                        "evidence": candidate["evidence"],
                                        "verification": "unverified"})
            data["listings"].append({
                "listing_id": f"candidate:{symbol}", "instrument_id": instrument_id,
                "venue": candidate.get("venue"), "symbol": symbol, "calendar": None,
                "currency": None, "price_scale": None, "valid_from": None, "valid_to": None,
                "known_from": candidate.get("known_from") or learned, "terminal": None,
                "evidence": candidate["evidence"], "verification": "unverified"})
        data["version"] = f"{self.version}+candidates"
        return Registry(data)

    # ------------------------------------------------------------ seed
    @classmethod
    def from_config(cls, known_from=None):
        """Seed the protected cohort from config at its existing verification level.

        Nothing is upgraded by guessing: instruments without an issuer profile
        stay unverified with unknown venue, calendar and currency until an
        approved snapshot establishes them. Knowledge starts when the seed is
        built (or the explicit known_from), never at a fixed earlier date.
        """
        from config import EDITORIAL_ONLY_TICKERS, SIGNAL_ELIGIBLE_TICKERS
        from coverage_policy import IDENTITY_EVIDENCE, SECURITY_CAPABILITIES

        known_from = _instant(known_from) if known_from else _now()
        issuers, instruments, listings = {}, [], []
        for ticker in (*SIGNAL_ELIGIBLE_TICKERS, *EDITORIAL_ONLY_TICKERS):
            security = SECURITY_CAPABILITIES[ticker]
            profile = security["issuer"]
            status = profile["identity_status"]
            verification = {"primary_verified": "primary_verified",
                            "legacy_profile": "legacy_profile"}.get(status, "unverified")
            evidence = ([IDENTITY_EVIDENCE[ticker]["filing"]] if ticker in IDENTITY_EVIDENCE
                        else [f"config:{security['coverage_tier']}"])
            issuer_id = None
            if profile["cik"] or profile["legal_name"]:
                issuer_id = f"cik:{profile['cik']}" if profile["cik"] else f"legacy:{ticker}"
                issuers.setdefault(issuer_id, {
                    "issuer_id": issuer_id, "legal_name": profile["legal_name"],
                    "cik": profile["cik"], "evidence": evidence, "verification": verification})
            instrument_id = f"seed1:{ticker}"
            instruments.append({"instrument_id": instrument_id, "issuer_id": issuer_id,
                                "type": security["security_type"], "share_class": None,
                                "tier": security["coverage_tier"], "evidence": evidence,
                                "verification": verification})
            venue = VENUE_MIC.get(str(profile["exchange"] or "").upper())
            us_equity = venue in US_EQUITY_VENUES
            listings.append({
                "listing_id": f"seed1:{ticker}", "instrument_id": instrument_id, "venue": venue,
                "symbol": ticker, "calendar": "XNYS" if us_equity else None,
                "currency": "USD" if us_equity else None, "price_scale": 1 if us_equity else None,
                "valid_from": None, "valid_to": None, "known_from": known_from,
                "terminal": None, "evidence": evidence, "verification": verification})
        return cls({"contract": CONTRACT, "version": SEED_VERSION,
                    "issuers": list(issuers.values()), "instruments": instruments,
                    "listings": listings, "provider_bindings": [], "capabilities": []})


def load_registry(path=None):
    """The committed registry, or the config seed when no file exists.

    A file whose protected tiers no longer contain the configured symbols is
    refused: a cohort change must regenerate the registry, not silently mix
    identities. Order is not identity, so a reordered config is accepted.
    """
    from config import EDITORIAL_ONLY_TICKERS, SIGNAL_ELIGIBLE_TICKERS
    path = Path(path) if path is not None else DEFAULT_PATH
    if not path.is_file():
        return Registry.from_config()
    registry = Registry(json.loads(path.read_text(encoding="utf-8")))
    if (sorted(registry.tier_symbols("instrumented")) != sorted(SIGNAL_ELIGIBLE_TICKERS)
            or sorted(registry.tier_symbols("editorial_only")) != sorted(EDITORIAL_ONLY_TICKERS)):
        raise ValueError(f"{path} does not match the configured cohorts; regenerate it with "
                         "registry_snapshot.py enrich")
    return registry


def reconcile_listing_snapshot(registry, snapshot):
    """Compare a directory snapshot with open listings; infer nothing (R03, R06)."""
    expected, received = snapshot.get("expected_pages"), snapshot.get("successful_pages")
    complete = (isinstance(expected, int) and expected > 0
                and isinstance(received, int) and received >= expected)
    seen = {str(s).strip().upper() for s in snapshot.get("symbols", [])}
    missing = "unobserved" if complete else "unobserved_incomplete_snapshot"
    open_symbols = {r["symbol"].upper() for r in registry.listings if r["valid_to"] is None}
    return {
        "source": snapshot.get("source"), "captured_at": snapshot.get("captured_at"),
        "complete": complete, "expected_pages": expected, "successful_pages": received,
        "registry_symbols": {s: "observed" if s in seen else missing for s in sorted(open_symbols)},
        "unregistered_symbols": sorted(seen - open_symbols),
        # Absence from a directory, even a complete one, is not a dated
        # delisting assertion; and a snapshot never activates coverage.
        "inferred_delistings": [], "activations": [],
    }
