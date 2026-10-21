"""Bounded offline reader for the verified final 2025 EIA-860 OOXML layout.

Identifiers and cell number formats are retained. Operation, direct ownership,
and public-company parentage remain separate. The owner-file absence rule is
usable only after reading the entire matching, correctly headed source table.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import re
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from ..identities import digest, instant
from .common import atom, candidate, checked_stakes, decimal_string, nonempty

VERSION = "omni-eia860-2025-1"
SOURCE_URL = "https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip"
CONTRACT = {"version": VERSION, "vintage": 2025, "source_url": SOURCE_URL,
            "generator_file": "3_1_Generator_Y2025.xlsx", "owner_file": "4___Owner_Y2025.xlsx",
            "generator_sheets": ["Operable", "Proposed", "Retired and Canceled"], "owner_sheet": "Ownership",
            "header_row": 2, "owner_percentage_unit": "Excel percentage cell format; raw ratio times 100",
            "absence_rule_source": "https://www.eia.gov/electricity/data/eia860/",
            "absence_rule": "Complete ownership file lists joint/third-party ownership; unlisted matched generator is wholly owned by its operator at this vintage only"}
GENERATOR_HEADERS = ("Utility ID", "Utility Name", "Plant Code", "Plant Name", "Generator ID", "Status")
OWNER_HEADERS = GENERATOR_HEADERS + ("Owner Name", "Ownership ID", "Percent Owned")
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _archive(raw, *, max_bytes, expanded_limit):
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise ValueError("eia860_file_size_limit")
    try:
        archive = ZipFile(BytesIO(raw))
    except BadZipFile:
        raise ValueError("eia860_invalid_zip") from None
    infos = archive.infolist()
    names = [x.filename for x in infos]
    if (len(infos) > 300 or len(set(names)) != len(names) or sum(x.file_size for x in infos) > expanded_limit
            or any(x.file_size > 110_000_000 or x.flag_bits & 1 for x in infos)
            or any(n.startswith("/") or "\\" in n or ".." in n.split("/") for n in names)):
        archive.close()
        raise ValueError("eia860_unsafe_or_oversized_archive")
    return archive


def _xml(archive, path):
    try:
        raw = archive.read(path)
    except (KeyError, BadZipFile):
        raise ValueError("eia860_source_part_missing_or_corrupt") from None
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("eia860_unsafe_xml")
    return raw


def _sheet_table(raw, *, sheet_name, required, row_cap=100000):
    if type(row_cap) is not int or not 1 <= row_cap <= 100000:
        raise ValueError("eia860_invalid_row_cap")
    with _archive(raw, max_bytes=30_000_000, expanded_limit=150_000_000) as archive:
        try:
            workbook = ET.fromstring(_xml(archive, "xl/workbook.xml"))
            rels = ET.fromstring(_xml(archive, "xl/_rels/workbook.xml.rels"))
            relationships = {}
            for rel in rels.findall(P + "Relationship"):
                if rel.get("TargetMode") == "External":
                    raise ValueError("eia860_external_relationship")
                relationships[rel.get("Id")] = rel.get("Target", "")
            matching = [s for s in workbook.findall(S + "sheets/" + S + "sheet") if s.get("name") == sheet_name]
            if len(matching) != 1:
                raise ValueError("eia860_sheet_missing_or_ambiguous")
            target = relationships.get(matching[0].get(R + "id"), "")
            path = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target
            if not path.startswith("xl/") or ".." in path.split("/") or "\\" in path:
                raise ValueError("eia860_invalid_sheet_path")
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ET.fromstring(_xml(archive, "xl/sharedStrings.xml"))
                shared = ["".join(t.text or "" for t in x.iter(S + "t")) for x in strings.findall(S + "si")]
                if len(shared) > 300000 or any(len(x) > 20000 for x in shared):
                    raise ValueError("eia860_shared_strings_limit")
            formats = {0: "General", 9: "0%", 10: "0.00%"}
            style_codes = ["General"]
            if "xl/styles.xml" in archive.namelist():
                style = ET.fromstring(_xml(archive, "xl/styles.xml"))
                for num in style.findall(S + "numFmts/" + S + "numFmt"):
                    formats[int(num.get("numFmtId"))] = num.get("formatCode", "")
                style_codes = [formats.get(int(x.get("numFmtId", "0")), "unsupported")
                               for x in style.findall(S + "cellXfs/" + S + "xf")]
            xml = _xml(archive, path)
            rows, headers, banner, previous, complete = [], {}, None, 0, True
            for _, row in ET.iterparse(BytesIO(xml), events=("end",)):
                if row.tag != S + "row":
                    continue
                number = int(row.get("r", "0"))
                if number <= previous:
                    raise ValueError("eia860_duplicate_or_unordered_rows")
                previous = number
                cells = {}
                if len(row) > 512:
                    raise ValueError("eia860_column_limit")
                for c in row.findall(S + "c"):
                    ref = c.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9]\d*)", ref)
                    if not match or int(match[2]) != number or match[1] in cells:
                        raise ValueError("eia860_cell_reference_invalid")
                    if c.find(S + "f") is not None:
                        raise ValueError("eia860_formula_not_supported")
                    kind = c.get("t", "n")
                    value = c.findtext(S + "v")
                    if kind == "s":
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(c.find(S + "is").itertext())
                    elif kind not in ("n", "str"):
                        value = None
                    style_id = int(c.get("s", "0"))
                    cells[match[1]] = {"cell": ref, "value": value, "type": kind,
                                       "number_format": style_codes[style_id] if style_id < len(style_codes) else "unsupported"}
                if number == 1:
                    banner = " ".join(str(x["value"]) for x in cells.values())
                elif number == 2:
                    for column, cell in cells.items():
                        name = cell["value"]
                        if isinstance(name, str) and name in required:
                            if name in headers:
                                raise ValueError("eia860_duplicate_header")
                            headers[name] = column
                    if set(headers) != set(required):
                        raise ValueError("eia860_required_headers_missing")
                elif number >= 3 and any(x["value"] is not None for x in cells.values()):
                    if len(rows) >= row_cap:
                        complete = False
                        break
                    values = {name: deepcopy(cells.get(column, {"cell": column + str(number), "value": None,
                                                              "type": "missing", "number_format": None}))
                              for name, column in headers.items()}
                    rows.append({"row_number": number, "fields": values})
                row.clear()
            if not banner or "2025" not in banner or "EIA-860" not in banner or set(headers) != set(required):
                raise ValueError("eia860_vintage_or_layout_unverified")
            return {"file_hash": sha256(raw).hexdigest(), "sheet": sheet_name, "vintage": 2025,
                    "headers": headers, "rows": rows, "complete": complete, "parsed_rows": len(rows),
                    "row_cap": row_cap, "source_part": path}
        except (ET.ParseError, IndexError, TypeError, OverflowError) as exc:
            raise ValueError("eia860_malformed_workbook") from exc


def _value(row, field):
    return row["fields"][field]["value"]


def _native(row):
    plant, generator, utility = (_value(row, k) for k in ("Plant Code", "Generator ID", "Utility ID"))
    if (not isinstance(plant, str) or not re.fullmatch(r"[1-9]\d{0,8}", plant)
            or not isinstance(utility, str) or not re.fullmatch(r"[1-9]\d{0,8}", utility)
            or not nonempty(generator, maximum=30)):
        return None
    return plant, generator, utility


def _percentage(cell):
    value = decimal_string(cell["value"], signed=False)
    # Only numeric cells with an explicit verified percentage display format
    # identify a ratio. A bare 0.6 or text '60%' is never silently interpreted.
    if (value is None or cell["type"] != "n" or cell["number_format"] not in ("0%", "0.0%", "0.00%")
            or Decimal(value) > 1):
        return None
    result = format(Decimal(value) * 100, "f")
    return (result.rstrip("0").rstrip(".") or "0") if "." in result else result


def parse_eia860_bundle(raw, *, vintage, observed_at, source_policy_fingerprint,
                        evidence_kind="unverified_local_input", row_cap=100000):
    instant(observed_at)
    if not isinstance(raw, bytes) or len(raw) > 30_000_000:
        raise ValueError("eia860_file_size_limit")
    if vintage != CONTRACT["vintage"] or type(vintage) is not int:
        raise ValueError("eia860_vintage_contract_unverified")
    source_hash = sha256(raw).hexdigest()
    tables, gaps = {}, []
    try:
        with _archive(raw, max_bytes=30_000_000, expanded_limit=100_000_000) as archive:
            generator_raw = archive.read(CONTRACT["generator_file"])
            owner_raw = archive.read(CONTRACT["owner_file"])
        for name in CONTRACT["generator_sheets"]:
            tables[name] = _sheet_table(generator_raw, sheet_name=name, required=GENERATOR_HEADERS, row_cap=row_cap)
        tables["Ownership"] = _sheet_table(owner_raw, sheet_name="Ownership", required=OWNER_HEADERS, row_cap=row_cap)
    except (ValueError, KeyError, BadZipFile) as exc:
        # A partial/missing ownership file cannot trigger the documented absence rule.
        return {"contract": VERSION, "rows": [], "gaps": ["incomplete_ownership_source", str(exc)],
                "source_bundle_hash": source_hash, "ownership_percent": None,
                "human_review_status": "pending", "signal_eligible": False, "complete": False}
    owner_table = tables["Ownership"]
    owners, generators = {}, {}
    for owner in owner_table["rows"]:
        key = _native(owner)
        if key is None or not nonempty(_value(owner, "Owner Name")) or not nonempty(_value(owner, "Ownership ID")):
            owner_table["complete"] = False
            gaps.append("owner_identity_unresolved")
            continue
        owners.setdefault(key[:2], []).append(owner)
    for sheet in CONTRACT["generator_sheets"]:
        for row in tables[sheet]["rows"]:
            key = _native(row)
            if key is None:
                gaps.append("generator_identity_unresolved")
                tables[sheet]["complete"] = False
                continue
            if key[:2] in generators:
                gaps.append("generator_identity_conflict")
                generators[key[:2]] = None
            else:
                generators[key[:2]] = (key, row, sheet)
    if not owner_table["complete"]:
        gaps.append("incomplete_ownership_source")
    if any(not table["complete"] for table in tables.values()):
        gaps.append("eia860_table_incomplete")
    results = []
    for native, entry in generators.items():
        if entry is None:
            continue
        key, generator, sheet = entry
        matched = owners.get(native, [])
        shares, reasons = [], ["public_parent_unresolved", "identity_crosswalk_unreviewed"]
        if matched:
            ids = set()
            for owner in matched:
                owner_id = _value(owner, "Ownership ID")
                percentage = _percentage(owner["fields"]["Percent Owned"])
                if _native(owner)[2] != key[2] or owner_id in ids or percentage is None:
                    reasons.append("ownership_basis_or_percentage_unverified")
                    continue
                ids.add(owner_id)
                shares.append({"owner_native_id": owner_id, "owner_name": _value(owner, "Owner Name"),
                               "direct_stake_percent": percentage, "derived_from_absence": False,
                               "percentage_cell": atom(owner, "/fields/Percent Owned"),
                               "owner_identity": atom(owner, "/fields/Ownership ID"),
                               "source_row": owner, "row_hash": digest(owner)})
            if len(shares) != len(matched) or not checked_stakes([x["direct_stake_percent"] for x in shares])["accepted"]:
                shares = []
                reasons.append("overlapping_or_inconsistent_stakes")
        elif owner_table["complete"] and all(table["complete"] for table in tables.values()) and "generator_identity_conflict" not in gaps:
            shares = [{"owner_native_id": key[2], "owner_name": _value(generator, "Utility Name"),
                       "direct_stake_percent": "100", "derived_from_absence": True,
                       "absence_rule_contract_hash": digest(CONTRACT), "source_bundle_hash": source_hash,
                       "owner_file_hash": owner_table["file_hash"], "owner_rows_examined": owner_table["parsed_rows"],
                       "generator_identity": atom(generator, "/fields/Utility ID")}]
            reasons.append("ownership_absence_rule_vintage_limited")
        else:
            reasons.append("incomplete_ownership_source")
        identity = {"vintage": vintage, "plant_code": key[0], "generator_id": key[1], "operator_native_id": key[2],
                    "direct_owners": shares, "issuer_parent": None, "operator_equals_owner": None,
                    "ownership_inferred_from_operator": False}
        payload = {"generator": generator, "owners": matched, "source_bundle_hash": source_hash,
                   "generator_file_hash": tables[sheet]["file_hash"], "owner_file_hash": owner_table["file_hash"]}
        row = candidate(source_id="eia860", native_id=":".join([str(vintage), *native]), observed_at=observed_at,
                        source_payload=payload, source_policy_fingerprint=source_policy_fingerprint,
                        contract_hash=digest(CONTRACT), facts={"generator": generator, "ownership": shares}, identity=identity,
                        times={"vintage": vintage, "as_of_basis": f"{vintage} dataset only", "valid_from": None, "valid_to": None,
                               "known_from": observed_at, "current_ownership_established": False},
                        lineage={"source_bundle_hash": source_hash, "generator_file_hash": tables[sheet]["file_hash"],
                                 "owner_file_hash": owner_table["file_hash"], "owner_file_complete": owner_table["complete"],
                                 "source_url": SOURCE_URL}, reasons=reasons, evidence_kind=evidence_kind)
        results.append(row)
    return {"contract": VERSION, "rows": results, "gaps": sorted(set(gaps)), "source_bundle_hash": source_hash,
            "tables": {name: {k: v for k, v in table.items() if k != "rows"} for name, table in tables.items()},
            "human_review_status": "pending", "signal_eligible": False, "complete": not gaps,
            "automatic_activation": False, "current_ownership_established": False}
