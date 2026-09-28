"""Approved directory snapshots for the dated registry (T9). Public sources only.

    python registry_snapshot.py capture OUT_DIR
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
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

from instrument_registry import Registry, reconcile_listing_snapshot

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
NAME_STOPWORDS = {"the", "inc", "corp", "corporation", "co", "company", "ltd", "llc", "lp",
                  "plc", "ag", "sa", "nv", "holdings", "holding", "group", "de", "ma"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture(out_dir, user_agent, *, opener=urllib.request.urlopen, now=None):
    """Download the three directory parts once; returns the manifest."""
    if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(user_agent or "")):
        raise ValueError("SEC requires a User-Agent with a real contact email")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    captured_at = now or _now()
    parts = {}
    for part, spec in PARTS.items():
        request = urllib.request.Request(spec["url"], headers={
            "User-Agent": user_agent, "Accept-Encoding": "identity"})
        record = {"source": spec["source"], "url": spec["url"], "file": spec["file"],
                  "captured_at": captured_at}
        try:
            with opener(request, timeout=30) as response:
                data = response.read(MAX_PART_BYTES + 1)
            if len(data) > MAX_PART_BYTES:
                raise ValueError("part exceeds the size limit")
            (out / spec["file"]).write_bytes(data)
            record.update(status="captured", bytes=len(data),
                          sha256=hashlib.sha256(data).hexdigest())
        except Exception as error:  # recorded, not raised: a snapshot may be partial
            record.update(status="failed", error=type(error).__name__)
        parts[part] = record
    manifest = {"contract": "registry-snapshot-1", "captured_at": captured_at, "parts": parts,
                "complete": all(p["status"] == "captured" for p in parts.values())}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _load_parts(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    loaded = {}
    for part, record in manifest["parts"].items():
        if record.get("status") != "captured":
            continue
        data = (folder / record["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            record["status"] = "integrity_failed"
            continue
        loaded[part] = data.decode("utf-8")
    manifest["complete"] = set(loaded) == set(PARTS)
    return manifest, loaded


def _directory(text, symbol_column):
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].split("|")
    rows, created = {}, None
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            created = line.split("|")[0].split(":", 1)[1].strip()
            continue
        row = dict(zip(header, line.split("|")))
        rows[row[symbol_column].strip().upper()] = row
    return rows, created


def _name_key(name):
    tokens = [t for t in re.split(r"[^a-z0-9]+", str(name).casefold()) if t]
    tokens = [t for t in tokens if t not in NAME_STOPWORDS]
    return tokens[0] if tokens else ""


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
    directory, created = {}, {}
    for part, column in (("nasdaq_listed", "Symbol"), ("nasdaq_other_listed", "ACT Symbol")):
        if part in parts:
            rows, created[part] = _directory(parts[part], column)
            for symbol, row in rows.items():
                if row.get("Test Issue") == "Y":
                    continue
                venue = "XNAS" if part == "nasdaq_listed" else OTHER_VENUES.get(row.get("Exchange"))
                directory[symbol] = dict(row, _part=part, _venue=venue)

    evidence = [f"snapshot:{part}@{record['sha256'][:16]}"
                for part, record in manifest["parts"].items() if part in parts]
    data = registry.to_dict()
    data["snapshots"] = data.get("snapshots", []) + [
        {"part": part, **{k: record.get(k) for k in ("source", "url", "captured_at", "status",
                                                     "bytes", "sha256")},
         "file_creation_time": created.get(part)}
        for part, record in manifest["parts"].items()]
    issuers = {i["issuer_id"]: i for i in data["issuers"]}
    instruments = {i["instrument_id"]: i for i in data["instruments"]}
    report = {"snapshot_complete": manifest["complete"], "matched": [], "conflicts": {},
              "not_in_directory": [], "inferred_delistings": [], "activations": []}

    for listing in data["listings"]:
        instrument = instruments[listing["instrument_id"]]
        if listing["valid_to"] is not None or instrument["tier"] == "catalogue_candidate":
            continue
        symbol = listing["symbol"].upper()
        row = directory.get(symbol)
        if row is None:
            report["not_in_directory"].append(symbol)
            continue
        kind = _security_type(row["Security Name"], row.get("ETF"))
        sec_rows = sec.get(symbol, [])
        issuer_id = instrument["issuer_id"]
        if kind != "exchange_traded_product":
            if len(sec_rows) != 1:
                reason = "no_sec_row" if not sec_rows else "several_sec_rows"
                if not manifest["complete"] and "sec_company_tickers_exchange" not in parts:
                    reason = "sec_part_missing"
                report["conflicts"][symbol] = reason
                continue
            filer = sec_rows[0]
            if _name_key(filer["name"]) != _name_key(row["Security Name"]):
                report["conflicts"][symbol] = "issuer_name_disagrees"
                continue
            cik = f"{int(filer['cik']):010d}"
            existing = issuers.get(issuer_id) if issuer_id else None
            if existing and existing.get("cik") and existing["cik"] != cik:
                report["conflicts"][symbol] = "cik_disagrees_with_registry"
                continue
            issuer_id = issuer_id or f"cik:{cik}"
            issuer = issuers.setdefault(issuer_id, {"issuer_id": issuer_id, "legal_name": None,
                                                    "cik": None, "evidence": [],
                                                    "verification": "unverified"})
            issuer.update(legal_name=issuer["legal_name"] or filer["name"], cik=cik,
                          evidence=list(dict.fromkeys(issuer["evidence"] + evidence)))
            if VERIFICATION_RANK[issuer["verification"]] < VERIFICATION_RANK["snapshot_matched"]:
                issuer["verification"] = "snapshot_matched"
        share_class = re.search(r"\bclass ([a-z])\b", row["Security Name"], re.I)
        verification = instrument["verification"]
        if VERIFICATION_RANK[verification] < VERIFICATION_RANK["snapshot_matched"]:
            verification = "snapshot_matched"
        instrument.update(issuer_id=issuer_id, type=kind,
                          share_class=share_class.group(1).upper() if share_class else instrument["share_class"],
                          verification=verification,
                          evidence=list(dict.fromkeys(instrument["evidence"] + evidence)))
        listing.update(venue=row["_venue"], calendar="XNYS", currency="USD", price_scale=1,
                       known_from=manifest["captured_at"], verification=verification,
                       evidence=list(dict.fromkeys(listing["evidence"] + evidence)))
        report["matched"].append(symbol)

    report["not_in_directory"].sort()
    data["issuers"] = list(issuers.values())
    data["version"] = f"{registry.version}+snapshot-{manifest['captured_at']}"
    enriched = Registry(data)
    report["reconcile"] = reconcile_listing_snapshot(enriched, {
        "source": "nasdaq-trader-directories", "captured_at": manifest["captured_at"],
        "expected_pages": 2,
        "successful_pages": sum(p in parts for p in ("nasdaq_listed", "nasdaq_other_listed")),
        "symbols": list(directory)})["registry_symbols"]
    return enriched, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("out", type=Path)
    enr = sub.add_parser("enrich")
    enr.add_argument("folder", type=Path)
    enr.add_argument("--write", type=Path, help="write the enriched registry JSON here")
    args = parser.parse_args(argv)
    if args.command == "capture":
        from config import SEC_USER_AGENT
        manifest = capture(args.out, SEC_USER_AGENT)
        print(json.dumps({k: v for k, v in manifest.items() if k != "parts"}
                         | {"parts": {p: r["status"] for p, r in manifest["parts"].items()}}, indent=2))
        return 0 if manifest["complete"] else 1
    registry, report = enrich(Registry.from_config(), args.folder)
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
