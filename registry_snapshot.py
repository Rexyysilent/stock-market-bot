"""Approved directory snapshots for the dated registry (T9). Public sources only.

    python registry_snapshot.py capture OUT_DIR
    python registry_snapshot.py capture OUT_DIR --directory-only
    python registry_snapshot.py enrich OUT_DIR --write registry/instrument-registry.json

capture  three bounded requests (SEC company_tickers_exchange.json, Nasdaq
         Trader nasdaqlisted.txt and otherlisted.txt) with the SEC contact
         User-Agent. Raw files and a manifest (sha256, bytes, status) stay in
         OUT_DIR, which is local data, not repository content. A failed part
         is recorded, never raised.
enrich   a pure step over a captured folder. A protected operating company is
         "snapshot_matched" only when the SEC row and the directory row agree
         on symbol and issuer name; an exchange-traded product needs the
         directory row only. Conflicts, test issues and symbols absent from the
         directories change nothing (absence is not delisting), and
         primary-verified identity is never downgraded.

Snapshots describe current listings. They do not establish historical
membership or announcement dates, so listing starts stay unknown.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

from instrument_registry import DEFAULT_PATH, Registry, load_registry, reconcile_listing_snapshot

MAX_PART_BYTES = 20 * 1024 * 1024
PARTS = {
    "sec_company_tickers_exchange": {
        "url": "https://www.sec.gov/files/company_tickers_exchange.json",
        "file": "company_tickers_exchange.json", "source": "SEC EDGAR"},
    "nasdaq_listed": {
        "url": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "file": "nasdaqlisted.txt", "source": "Nasdaq Trader symbol directory"},
    "nasdaq_other_listed": {
        "url": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
        "file": "otherlisted.txt", "source": "Nasdaq Trader symbol directory"},
}
OTHER_VENUES = {"A": "XASE", "N": "XNYS", "P": "ARCX", "Z": "BATS", "V": "IEXG"}
VERIFICATION_RANK = {"unverified": 0, "legacy_profile": 1, "snapshot_matched": 2,
                     "primary_verified": 3}
NAME_STOPWORDS = {"the", "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
                  "llc", "lp", "plc", "ag", "sa", "nv", "holdings", "holding", "group", "de", "ma"}
SECURITY_DESCRIPTOR = re.compile(
    r"\b(?:class\s+[a-z]\b|american\s+depositary\s+shares?|common\s+stock|common\s+shares|"
    r"ordinary\s+shares|subordinate\s+voting\s+shares|units?|when[-\s]issued)\b")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DIRECTORY_PARTS = ("nasdaq_listed", "nasdaq_other_listed")


def _instant(value):
    value = value or _now()
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("snapshot time needs an explicit time zone")
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _completeness(manifest, loaded):
    requested = manifest.get("requested_parts", list(PARTS))
    manifest["complete"] = set(loaded) == set(PARTS)
    manifest["directory_complete"] = set(DIRECTORY_PARTS).issubset(loaded)
    manifest["requested_complete"] = bool(requested) and set(requested).issubset(loaded)


def capture(out_dir, user_agent=None, *, opener=urllib.request.urlopen, now=None,
            directory_only=False):
    """Capture once into a new snapshot; never replace prior files.

    Directory-only mode performs exactly two Nasdaq requests. SEC remains
    explicitly not_requested and full-snapshot completeness stays false.
    """
    if not directory_only and (
            not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(user_agent or ""))
            or str(user_agent).strip() == "StockMarketBot contact@example.com"):
        raise ValueError("SEC requires a User-Agent with a real contact email")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    targets = [out / "manifest.json", *(out / p["file"] for p in PARTS.values())]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise FileExistsError("snapshot files already exist; choose a new output directory")
    captured_at = _instant(now)
    parts = {}
    requested = list(DIRECTORY_PARTS) if directory_only else list(PARTS)
    # Exclusive creation reserves the capture before any request. A crashed
    # capture leaves an incomplete file that cannot be silently overwritten.
    with (out / "manifest.json").open("x", encoding="utf-8") as manifest_file:
        for part, spec in PARTS.items():
            record = {"source": spec["source"], "url": spec["url"], "file": spec["file"],
                      "captured_at": captured_at if part in requested else None}
            if part not in requested:
                record.update(status="not_requested", reason="directory_only")
                parts[part] = record
                continue
            request = urllib.request.Request(spec["url"], headers={
                "User-Agent": user_agent or "Heptarchy public directory research",
                "Accept-Encoding": "identity"})
            try:
                if part == "sec_company_tickers_exchange":
                    from source_request_gate import wait_for_sec
                    wait_for_sec()
                with opener(request, timeout=30) as response:
                    data = response.read(MAX_PART_BYTES + 1)
                if len(data) > MAX_PART_BYTES:
                    raise ValueError("part exceeds the size limit")
                with (out / spec["file"]).open("xb") as part_file:
                    part_file.write(data)
                record.update(status="captured", bytes=len(data),
                              sha256=hashlib.sha256(data).hexdigest())
            except Exception as error:  # recorded: a snapshot may be partial
                record.update(status="failed", error=type(error).__name__)
            parts[part] = record
        manifest = {"contract": "registry-snapshot-1", "captured_at": captured_at,
                    "requested_parts": requested, "parts": parts}
        _completeness(manifest, {p for p, r in parts.items() if r["status"] == "captured"})
        manifest_file.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _load_parts(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("contract") != "registry-snapshot-1":
        raise ValueError("unknown registry snapshot contract")
    if not manifest.get("captured_at"):
        raise ValueError("snapshot capture time is required")
    _instant(manifest["captured_at"])
    loaded = {}
    for part, record in manifest["parts"].items():
        if record.get("status") != "captured":
            continue
        if part not in PARTS or record.get("file") != PARTS[part]["file"]:
            record["status"] = "invalid_part"
            continue
        try:
            with (folder / record["file"]).open("rb") as part_file:
                data = part_file.read(MAX_PART_BYTES + 1)
        except OSError:
            record["status"] = "unavailable"
            continue
        if len(data) > MAX_PART_BYTES or len(data) != record.get("bytes"):
            record["status"] = "integrity_failed"
            continue
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            record["status"] = "integrity_failed"
            continue
        try:
            loaded[part] = data.decode("utf-8")
        except UnicodeDecodeError:
            record["status"] = "decode_failed"
    _completeness(manifest, loaded)
    return manifest, loaded


def parse_directory(text, symbol_column):
    """Retain all rows and parser diagnostics; never overwrite a duplicate symbol."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], None, {"complete": False, "errors": ["empty_file"], "malformed_rows": 0}
    header = lines[0].split("|")
    required = {symbol_column, "Security Name", "Test Issue", "ETF"}
    if symbol_column == "ACT Symbol":
        required.add("Exchange")
    if not required.issubset(header) or len(set(header)) != len(header):
        return [], None, {"complete": False, "errors": ["invalid_header"], "malformed_rows": 0}
    rows, created, errors, malformed = [], None, [], 0
    for line_number, line in enumerate(lines[1:], 2):
        if line.startswith("File Creation Time"):
            value = line.split("|")[0].partition(":")[2].strip()
            if created is not None:
                errors.append("duplicate_footer")
            created = value
            continue
        values = line.split("|")
        if created is not None:
            errors.append("rows_after_footer")
        if len(values) != len(header):
            malformed += 1
            continue
        row = dict(zip(header, values))
        symbol = row[symbol_column].strip().upper()
        if not symbol or not row["Security Name"].strip():
            malformed += 1
            continue
        rows.append(dict(row, _symbol=symbol, _line=line_number))
    if not created:
        errors.append("missing_footer")
    else:
        try:
            datetime.strptime(created, "%m%d%Y%H:%M")
        except ValueError:
            errors.append("invalid_footer_time")
    if malformed:
        errors.append("malformed_rows")
    return rows, created, {"complete": not errors, "errors": sorted(set(errors)),
                           "malformed_rows": malformed, "parsed_rows": len(rows)}


def directory_records(parts):
    """Normalize both directory files without treating symbols as stable identities."""
    records, created, diagnostics = [], {}, {}
    for part, column in (("nasdaq_listed", "Symbol"), ("nasdaq_other_listed", "ACT Symbol")):
        if part not in parts:
            diagnostics[part] = {"complete": False, "errors": ["part_unavailable"],
                                 "malformed_rows": 0, "parsed_rows": 0}
            continue
        rows, created[part], diagnostics[part] = parse_directory(parts[part], column)
        for row in rows:
            venue = "XNAS" if part == "nasdaq_listed" else OTHER_VENUES.get(row.get("Exchange"))
            records.append(dict(row, _part=part, _venue=venue))
    return records, created, diagnostics


def _name_key(name):
    """Significant issuer-name tokens, without the directory's security descriptor."""
    text = str(name).casefold().split(" - ", 1)[0]
    text = re.sub(r"\([^)]*\)", " ", text)
    text = SECURITY_DESCRIPTOR.sub(" ", text)
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    return [t for t in tokens if t not in NAME_STOPWORDS]


def _names_agree(sec_name, directory_name):
    """The first two significant tokens must agree (one when a name has only one).

    A single shared first word is too weak: "American Express" and "American
    Airlines" would pass.
    """
    a, b = _name_key(sec_name), _name_key(directory_name)
    k = min(2, len(a), len(b))
    return k > 0 and a[:k] == b[:k]


def _security_type(name, etf):
    text = name.casefold()
    if etf == "Y":
        return "exchange_traded_product"
    if "american depositary" in text:
        return "adr"
    if "preferred" in text:
        return "preferred_stock"
    if re.search(r"common stock|common shares|ordinary shares|subordinate voting shares", text):
        return "common_stock"
    return "unknown"


def enrich(registry, folder):
    """New registry with snapshot-matched facts for protected members, plus a report."""
    manifest, parts = _load_parts(folder)
    sec = {}
    if "sec_company_tickers_exchange" in parts:
        payload = json.loads(parts["sec_company_tickers_exchange"])
        fields = payload["fields"]
        for values in payload["data"]:
            row = dict(zip(fields, values))
            sec.setdefault(str(row["ticker"]).upper(), []).append(row)
    records, created, directory_diagnostics = directory_records(parts)
    occurrences = Counter(row["_symbol"] for row in records)
    ambiguous = {symbol for symbol, count in occurrences.items() if count > 1}
    directory = {row["_symbol"]: row for row in records
                 if row["_symbol"] not in ambiguous and row.get("Test Issue") == "N"
                 and directory_diagnostics[row["_part"]]["complete"]}

    refs = {part: f"snapshot:{part}@{record['sha256'][:16]}"
            for part, record in manifest["parts"].items() if part in parts}
    data = copy.deepcopy(registry.to_dict())
    data["snapshots"] = data.get("snapshots", []) + [
        {"part": part, **{k: record.get(k) for k in ("source", "url", "captured_at", "status",
                                                     "bytes", "sha256")},
         "file_creation_time": created.get(part)}
        for part, record in manifest["parts"].items()]
    issuers = {i["issuer_id"]: i for i in data["issuers"]}
    instruments = {i["instrument_id"]: i for i in data["instruments"]}
    report = {"snapshot_complete": manifest["complete"],
              "directory_complete": all(d["complete"] for d in directory_diagnostics.values()),
              "directory_diagnostics": directory_diagnostics,
              "ambiguous_symbols": sorted(ambiguous), "matched": [], "reconfirmed": [],
              "conflicts": {}, "not_in_directory": [], "inferred_delistings": [],
              "activations": []}

    for listing in data["listings"]:
        instrument = instruments[listing["instrument_id"]]
        if listing["valid_to"] is not None or instrument["tier"] == "catalogue_candidate":
            continue
        symbol = listing["symbol"].upper()
        if symbol in ambiguous:
            report["conflicts"][symbol] = "ambiguous_directory_symbol"
            continue
        row = directory.get(symbol)
        if row is None:
            report["not_in_directory"].append(symbol)
            continue
        if row["_venue"] is None:
            # An exchange code this tool does not map: no venue, calendar or currency.
            report["conflicts"][symbol] = "unmapped_venue"
            continue
        kind = _security_type(row["Security Name"], row.get("ETF"))
        if row.get("ETF") not in ("Y", "N"):
            report["conflicts"][symbol] = "unknown_etf_flag"
            continue
        if re.search(r"\b(?:warrants?|rights?|units?)\b", row["Security Name"], re.I):
            report["conflicts"][symbol] = "unsupported_security_class"
            continue
        if listing["known_from"] > manifest["captured_at"]:
            report["conflicts"][symbol] = "snapshot_predates_registry_knowledge"
            continue
        sec_rows = sec.get(symbol, [])
        issuer_id = instrument["issuer_id"]
        evidence = [refs[row["_part"]]]     # only the sources actually consulted
        issuer_changed = False
        if kind != "exchange_traded_product":
            if len(sec_rows) != 1:
                reason = "no_sec_row" if not sec_rows else "several_sec_rows"
                if not manifest["complete"] and "sec_company_tickers_exchange" not in parts:
                    reason = "sec_part_missing"
                report["conflicts"][symbol] = reason
                continue
            filer = sec_rows[0]
            if not _names_agree(filer["name"], row["Security Name"]):
                report["conflicts"][symbol] = "issuer_name_disagrees"
                continue
            evidence = [refs["sec_company_tickers_exchange"], *evidence]
            cik = f"{int(filer['cik']):010d}"
            existing = issuers.get(issuer_id) if issuer_id else None
            if existing and existing.get("cik") and existing["cik"] != cik:
                report["conflicts"][symbol] = "cik_disagrees_with_registry"
                continue
            issuer_id = issuer_id or f"cik:{cik}"
            issuer = issuers.setdefault(issuer_id, {"issuer_id": issuer_id, "legal_name": None,
                                                    "cik": None, "evidence": [],
                                                    "verification": "unverified"})
            issuer_fields = {"legal_name": issuer["legal_name"] or filer["name"], "cik": cik,
                             "verification": issuer["verification"]}
            if VERIFICATION_RANK[issuer_fields["verification"]] < VERIFICATION_RANK["snapshot_matched"]:
                issuer_fields["verification"] = "snapshot_matched"
            issuer_changed = any(issuer.get(k) != v for k, v in issuer_fields.items())
            if issuer_changed:
                issuer.update(issuer_fields, evidence=list(dict.fromkeys(issuer["evidence"] + evidence)))
        share_class = re.search(r"\bclass ([a-z])\b", row["Security Name"], re.I)
        verification = instrument["verification"]
        if VERIFICATION_RANK[verification] < VERIFICATION_RANK["snapshot_matched"]:
            verification = "snapshot_matched"
        # A name the heuristic cannot type keeps the registry's existing type.
        instrument_fields = {"issuer_id": issuer_id,
                             "type": instrument["type"] if kind == "unknown" else kind,
                             "share_class": share_class.group(1).upper() if share_class else instrument["share_class"],
                             "verification": verification}
        instrument_changed = any(instrument.get(k) != v for k, v in instrument_fields.items())
        if instrument_changed:
            instrument.update(instrument_fields,
                              evidence=list(dict.fromkeys(instrument["evidence"] + evidence)))
        listing_fields = {"venue": row["_venue"], "calendar": "XNYS", "currency": "USD",
                          "price_scale": 1, "verification": verification}
        listing_changed = any(listing.get(k) != v for k, v in listing_fields.items())
        if issuer_changed or instrument_changed or listing_changed:
            # New facts become known at this capture. Identical refreshes keep
            # the original fields and evidence, so later snapshots cannot
            # erase a previously established strict known-by resolution.
            listing.update(listing_fields, known_from=manifest["captured_at"],
                           evidence=list(dict.fromkeys(listing["evidence"] + evidence)))
        else:
            report["reconfirmed"].append(symbol)
        report["matched"].append(symbol)

    report["not_in_directory"].sort()
    data["issuers"] = list(issuers.values())
    data["version"] = f"{registry.version}+snapshot-{manifest['captured_at']}"
    enriched = Registry(data)
    report["reconcile"] = reconcile_listing_snapshot(enriched, {
        "source": "nasdaq-trader-directories", "captured_at": manifest["captured_at"],
        "expected_pages": 2,
        "successful_pages": sum(directory_diagnostics[p]["complete"] for p in DIRECTORY_PARTS),
        "symbols": list(directory)})["registry_symbols"]
    return enriched, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("out", type=Path)
    cap.add_argument("--directory-only", action="store_true",
                     help="capture only Nasdaq directories; SEC is explicitly not requested")
    enr = sub.add_parser("enrich")
    enr.add_argument("folder", type=Path)
    enr.add_argument("--write", type=Path,
                     help="registry JSON to build on (if it exists) and write back")
    enr.add_argument("--no-config-check", action="store_true",
                     help="skip the configured-cohort check (synthetic registries)")
    args = parser.parse_args(argv)
    if args.command == "capture":
        from config import SEC_USER_AGENT
        manifest = capture(args.out, None if args.directory_only else SEC_USER_AGENT,
                           directory_only=args.directory_only)
        print(json.dumps({k: v for k, v in manifest.items() if k != "parts"}
                         | {"parts": {p: r["status"] for p, r in manifest["parts"].items()}}, indent=2))
        return 0 if manifest["requested_complete"] else 1
    # Build on the existing registry so dated listings, terminal assertions,
    # bindings, capabilities and earlier snapshot records survive a refresh.
    base_path = args.write or DEFAULT_PATH
    if base_path.is_file() and args.no_config_check:
        base = Registry(json.loads(base_path.read_text(encoding="utf-8")))
    else:
        base = load_registry(base_path)
    registry, report = enrich(base, args.folder)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(registry.to_dict(), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    shown = copy.deepcopy(report)
    shown.pop("reconcile")
    print(json.dumps(shown, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
