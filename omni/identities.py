"""OMNI-03: evidence-backed identities, scoped aliases and honest time precision.

Mention resolution never establishes a claim subject, coverage membership or an
economic exposure. Relationships are returned separately with their sourced role.
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "omni-identity-1"
KINDS = {"issuer", "security", "asset", "trial", "project", "contract"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string with an explicit timezone")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timestamp needs an explicit timezone")
    return stamp.astimezone(timezone.utc)


def time_interval(value):
    """Preserve source precision; interval end is exclusive, never an event day."""
    unknown = {"raw": value, "precision": "unknown", "start": None, "end": None}
    if not isinstance(value, str):
        return unknown
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            start = date.fromisoformat(value)
            end = date.fromordinal(start.toordinal() + 1)
            precision = "date"
        elif re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            start = date(year, month, 1)
            end = date.fromordinal(start.toordinal() + calendar.monthrange(year, month)[1])
            precision = "month"
        elif (match := re.fullmatch(r"(?:Q([1-4]) (\d{4})|(\d{4})-Q([1-4]))", value)):
            quarter, year = (int(match[1]), int(match[2])) if match[1] else (int(match[4]), int(match[3]))
            month = 3 * quarter - 2
            start = date(year, month, 1)
            end = date(year + (quarter == 4), 1 if quarter == 4 else month + 3, 1)
            precision = "quarter"
        elif re.fullmatch(r"\d{4}", value):
            start, end, precision = date(int(value), 1, 1), date(int(value) + 1, 1, 1), "year"
        else:
            stamp = instant(value).isoformat().replace("+00:00", "Z")
            return {"raw": value, "precision": "timestamp", "start": stamp, "end": stamp}
        return {"raw": value, "precision": precision, "start": start.isoformat(), "end": end.isoformat()}
    except (ValueError, TypeError, OverflowError):
        return unknown


class EntityRegistry:
    def __init__(self, data, *, clinical_catalog=None):
        self.data = copy.deepcopy(data)
        if data.get("version") != VERSION:
            raise ValueError("unsupported entity registry version")
        if clinical_catalog is not None:
            self.data["clinical_catalog"] = copy.deepcopy(clinical_catalog)
        # This optional catalog is fingerprinted with the identity snapshot.
        # Its own evidence, review and disclosure-time gates are checked by the
        # clinical parser. Merely including pending rows grants no mapping.
        self.clinical_catalog = self.data.get("clinical_catalog")
        self.entities = {}
        for row in self.data.get("entities", []):
            key = row["entity_id"]
            if key in self.entities or row.get("kind") not in KINDS or not row.get("evidence"):
                raise ValueError("duplicate, untyped or unsourced entity")
            instant(row["known_from"])
            self.entities[key] = row
        for row in self.data.get("aliases", []):
            self._validate_link(row, ("entity_id",))
            if not isinstance(row.get("alias"), str) or not row["alias"].strip():
                raise ValueError("empty alias")
        for row in self.data.get("relationships", []):
            self._validate_link(row, ("from_id", "to_id"))
            if not row.get("role"):
                raise ValueError("relationship needs a role")

    def _validate_link(self, row, keys):
        if any(row[k] not in self.entities for k in keys) or not row.get("evidence"):
            raise ValueError("unsourced or dangling entity link")
        instant(row["known_from"])
        low, high = row.get("valid_from"), row.get("valid_to")
        for day in (low, high):
            if day is not None:
                date.fromisoformat(day)
        if low is not None and high is not None and low >= high:
            raise ValueError("empty validity interval")

    def fingerprint(self):
        return digest(self.data)

    def _visible(self, row, known_by, on):
        return (instant(row["known_from"]) <= known_by
                and (row.get("valid_from") is None or row["valid_from"] <= on)
                and (row.get("valid_to") is None or on < row["valid_to"]))

    def resolve(self, text, *, known_by, on=None, related_symbols=()):
        """Return exact mention spans. Vendor tags remain untrusted hints only."""
        stamp = instant(known_by)
        day = on or stamp.date().isoformat()
        date.fromisoformat(day)
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise ValueError("invalid or oversized identity input")
        matches = []
        for alias in self.data.get("aliases", []):
            entity = self.entities[alias["entity_id"]]
            if not self._visible(alias, stamp, day) or instant(entity["known_from"]) > stamp:
                continue
            pattern = r"(?<!\w)" + re.escape(alias["alias"]) + r"(?!\w)"
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matches.append((match.start(), match.end(), alias))
        # Prefer a legal name to an overlapping generic token such as Merck.
        matches = [m for m in matches if not any(
            n[0] <= m[0] and m[1] <= n[1] and n[1]-n[0] > m[1]-m[0]
            for n in matches)]
        groups = {}
        for start, end, alias in matches:
            groups.setdefault((start, end), []).append(alias)
        mentions, unresolved = [], []
        for (start, end), aliases in sorted(groups.items()):
            ids = sorted({a["entity_id"] for a in aliases})
            if len(ids) != 1 or any(a.get("ambiguous") for a in aliases):
                unresolved.append({"text": text[start:end], "start": start, "end": end,
                                   "reason": "ambiguous_alias", "candidates": ids})
                continue
            mentions.append({"entity_id": ids[0], "kind": self.entities[ids[0]]["kind"],
                             "text": text[start:end], "start": start, "end": end,
                             "role": "mentioned", "evidence": aliases[0]["evidence"]})
        ids = {m["entity_id"] for m in mentions}
        relationships = [copy.deepcopy(r) for r in self.data.get("relationships", [])
                         if self._visible(r, stamp, day) and (r["from_id"] in ids or r["to_id"] in ids)]
        return {"version": VERSION, "registry_fingerprint": self.fingerprint(),
                "mentions": mentions, "relationships": relationships,
                "unresolved": unresolved, "related_symbol_hints": sorted(set(related_symbols)),
                "status": "review_required" if unresolved or not mentions else "resolved_mentions",
                "reason": "ambiguous_alias" if unresolved else "entity_unresolved" if not mentions else None,
                "signal_eligible": False}


def load_registry(path=None, *, clinical_catalog_path=None):
    catalog = json.loads(Path(clinical_catalog_path).read_text(encoding="utf-8")) if clinical_catalog_path else None
    return EntityRegistry(json.loads(Path(path or ROOT / "registry/event-entities.json").read_text(encoding="utf-8")),
                          clinical_catalog=catalog)


def from_instruments(instruments, *, known_from, aliases=None):
    """Derive current identity facts; preserve existing evidence/verification labels."""
    instant(known_from)
    entities, names, relations = [], [], []
    aliases = aliases or {}
    for issuer in instruments["issuers"]:
        if not issuer.get("legal_name") or not issuer.get("evidence"):
            continue
        entities.append({"entity_id": issuer["issuer_id"], "kind": "issuer",
                         "name": issuer["legal_name"], "known_from": known_from,
                         "cik": issuer.get("cik"), "evidence": issuer["evidence"],
                         "verification": issuer.get("verification", "unverified")})
        names.append({"entity_id": issuer["issuer_id"], "alias": issuer["legal_name"],
                      "known_from": known_from, "evidence": issuer["evidence"]})
    issuer_ids = {e["entity_id"] for e in entities}
    for security in instruments["instruments"]:
        issuer = security.get("issuer_id")
        if issuer not in issuer_ids:
            continue
        listings = [r for r in instruments["listings"] if r["instrument_id"] == security["instrument_id"] and not r.get("valid_to")]
        for listing in listings:
            ev = listing.get("evidence") or ["instrument-registry:" + digest(instruments)]
            # Exchange-qualified symbols resolve the issuer; the relationship
            # retains the specific security and listing identity.
            venue = {"XNAS": "NASDAQ", "XNYS": "NYSE", "XASE": "NYSE AMERICAN"}.get(listing.get("venue"))
            if venue:
                for separator in (":", ": "):
                    names.append({"entity_id": issuer, "alias": venue + separator + listing["symbol"],
                                  "known_from": known_from, "evidence": ev})
            for name in aliases.get(listing["symbol"], ()):
                names.append({"entity_id": issuer, "alias": name, "known_from": known_from, "evidence": ev})
        entities.append({"entity_id": security["instrument_id"], "kind": "security",
                         "name": security["instrument_id"], "known_from": known_from,
                         "evidence": ["instrument-registry:" + digest(instruments)],
                         "verification": security.get("verification", "unverified")})
        relations.append({"from_id": issuer, "to_id": security["instrument_id"], "role": "issues",
                          "known_from": known_from, "evidence": ["instrument-registry:" + digest(instruments)]})
    return {"version": VERSION, "entities": entities, "aliases": names, "relationships": relations}
