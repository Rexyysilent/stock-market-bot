"""Archive adapters: an archived document becomes evidence items with pointers.

Supported documents and what they contribute:

native-2.8   daily brief schema 2.8/2.9. SEC filing rows
             (/sections/sec_filings/<i>) and OMNI-02 intake records
             (/data_quality/headline_pool/evidence_intake/records/<i>) when
             the manifest validates. Knowledge time is the brief's zoned
             generated_at ("archive_bound": known no later than the brief).
legacy-2.5   schemas 2.5-2.7 (documented legacy). SEC filing rows only; the
             briefs predate OMNI-02. generated_at carries an explicit zone.
legacy-2.0   schema 2.0 (documented legacy). SEC filing rows only.
             generated_at has no zone, so knowledge time is not established
             ("legacy_inferred"); strict views exclude these sightings.
extract-1    evidence-extract-1 documents: structured claims from a
             permitted extract. Headlines alone never supply numbers.

Anything else is quarantined: recorded by exact bytes, contributes nothing.
Other brief sections are not adapted in this contract version. Capture policy
is copied from what the archive itself recorded; today's policy is never
applied retroactively to an older capture.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re

from .db import INSTANT_FORMAT

NORMALIZER_VERSION = "event-history-normalizer-1"
EXTRACT_CONTRACT = "evidence-extract-1"
ADAPTER_BY_SCHEMA = {
    "2.9": "native-2.8", "2.8": "native-2.8",
    "2.7": "legacy-2.5", "2.6": "legacy-2.5", "2.5": "legacy-2.5",
    "2.0": "legacy-2.0",
}
KINDS = ("filing", "news", "registry", "measurement", "notice", "context")
SEC_FIELDS = ("accession_number", "form_type", "description", "ticker",
              "filed_at", "date", "primary_doc_url")
ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
DECIMAL = re.compile(r"-?\d+(\.\d+)?")
OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")
INTAKE_POINTER = "/data_quality/headline_pool/evidence_intake/records/{}"
NO_CAPTURE_POLICY = {"recorded": False,
                     "reason": "archive_did_not_record_capture_policy"}


def normalize_instant(raw):
    """UTC instant text, or None when the value carries no explicit zone."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        stamp = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).strftime(INSTANT_FORMAT)


def _date(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def day_bounds(day, utc_offset=None):
    """Bounds of a calendar day. Unknown zone: every zone from UTC+14 to UTC-12.

    An unknown-zone date is never assigned UTC midnight; its bounds are wide
    enough that ordering it against an instant on the same day abstains.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    if utc_offset is None:
        lo, hi = start - timedelta(hours=14), start + timedelta(days=1, hours=12)
    else:
        sign, hours, minutes = OFFSET.fullmatch(utc_offset).groups()
        shift = timedelta(hours=int(hours), minutes=int(minutes))
        shift = shift if sign == "+" else -shift
        lo, hi = start - shift, start - shift + timedelta(days=1)
    return lo.strftime(INSTANT_FORMAT), hi.strftime(INSTANT_FORMAT), "day"


def _time(instant=None, day=None, utc_offset=None):
    exact = normalize_instant(instant)
    if exact:
        return exact, exact, "instant"
    parsed = _date(day)
    if parsed and (utc_offset is None or OFFSET.fullmatch(str(utc_offset))):
        return day_bounds(parsed, utc_offset)
    return None, None, "unknown"


def _knowledge(generated_at):
    observed = normalize_instant(generated_at)
    return observed, "archive_bound" if observed else "legacy_inferred"


def quarantine(reason):
    return {"status": "quarantined", "reason": reason, "adapter": "quarantine",
            "items": [], "components": {}}


def adapt(doc):
    if not isinstance(doc, dict):
        return quarantine("not_a_json_object")
    if doc.get("artifact") == EXTRACT_CONTRACT:
        return _adapt_extract(doc)
    schema = doc.get("schema_version")
    adapter = ADAPTER_BY_SCHEMA.get(schema)
    if adapter is None:
        return quarantine(f"unsupported_schema:{schema!r}")
    sections = doc.get("sections")
    if not isinstance(sections, dict):
        return quarantine("missing_sections")
    observed, quality = _knowledge(doc.get("generated_at"))
    items, components = [], {}
    sec_items, rejected = [], 0
    rows = sections.get("sec_filings")
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        item = _sec_item(index, row)
        if item is None:
            rejected += 1
        else:
            sec_items.append(item)
    components["sec_filings"] = {"status": "imported" if isinstance(rows, list) else "absent",
                                 "items": len(sec_items), "rejected": rejected}
    items += sec_items
    if adapter == "native-2.8":
        intake_items, report = _intake_items(doc)
        items += intake_items
        components["evidence_intake"] = report
    return {"status": "indexed", "reason": None, "adapter": adapter,
            "schema_version": schema, "pipeline_version": doc.get("pipeline_version"),
            "generated_at_raw": doc.get("generated_at"), "observed_at": observed,
            "knowledge_quality": quality, "items": items, "components": components}


def _sec_item(index, row):
    if (not isinstance(row, dict) or not isinstance(row.get("accession_number"), str)
            or not ACCESSION.fullmatch(row["accession_number"])):
        return None
    payload = {k: row[k] for k in SEC_FIELDS if isinstance(row.get(k), str)}
    ticker = payload.get("ticker")
    return {
        "pointer": f"/sections/sec_filings/{index}",
        "source_key": "sec_edgar:" + payload["accession_number"],
        "kind": "filing",
        "native_evidence_id": "sec:" + payload["accession_number"],
        "native_revision_id": None,
        "origin_hint": "sec:" + payload["accession_number"],
        # A listing symbol as reported, not a verified issuer identity (T9).
        "subject_id": "symbol:" + ticker if ticker else None,
        "publication": _time(payload.get("filed_at"), payload.get("date")),
        "event": (None, None, "unknown"),
        "payload": payload,
        "capture_policy": NO_CAPTURE_POLICY,
    }


def _intake_items(doc):
    import evidence_intake

    pool = (doc.get("data_quality") or {}).get("headline_pool") or {}
    manifest = pool.get("evidence_intake")
    if manifest is None:
        return [], {"status": "absent", "items": 0}
    if isinstance(manifest, dict) and manifest.get("status") == "error":
        return [], {"status": "capture_failed", "items": 0}
    errors = evidence_intake.validate_manifest(manifest)
    if errors:
        return [], {"status": "rejected", "items": 0, "errors": sorted(set(errors))}
    items = []
    for index, row in enumerate(manifest["records"]):
        metadata = {k: v for k, v in row["metadata"].items() if k != "provider_seen_at"}
        publication = row["publication"]
        items.append({
            "pointer": INTAKE_POINTER.format(index),
            "source_key": f"intake:{row['source_policy_id']}:{row['evidence_id']}",
            "kind": "news",
            "native_evidence_id": row["evidence_id"],
            "native_revision_id": row["revision_id"],
            "origin_hint": row["origin_cluster_id"],
            "subject_id": None,
            "publication": _time(publication.get("published_at"), publication.get("published_date")),
            "event": (None, None, "unknown"),
            "payload": {
                "metadata": metadata,
                "origin_basis": row["origin_basis"],
                # Preserved as archived: intake never establishes an event.
                "origin_verified_as_event": row["origin_verified_as_event"],
                "original_issuer": row["original_issuer"],
                "directness": row["directness"],
            },
            "capture_policy": {
                "recorded": True, "intake_version": manifest["version"],
                "source_policy_id": row["source_policy_id"],
                "policy_fingerprint": manifest["policy_fingerprint"],
                "rights_status": row["rights_status"], "uses": row["uses"],
                "signal_eligible": row["signal_eligible"],
            },
        })
    return items, {"status": "imported", "items": len(items),
                   "replay_status": manifest.get("replay_status")}


def validate_claims(claims):
    if not isinstance(claims, dict):
        raise ValueError("claims must be an object")
    for key, claim in claims.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(key)):
            raise ValueError(f"invalid claim key {key!r}")
        if not isinstance(claim, dict) or not DECIMAL.fullmatch(str(claim.get("value", ""))):
            raise ValueError(f"claim {key} needs a decimal-string value")
        if type(claim["value"]) is not str:
            raise ValueError(f"claim {key} value must be a string, not a float")
        for field in ("unit", "period"):
            if not isinstance(claim.get(field), str) or not claim[field]:
                raise ValueError(f"claim {key} needs {field}")


def _adapt_extract(doc):
    observed, quality = _knowledge(doc.get("generated_at"))
    policy = doc.get("capture_policy")
    if not isinstance(policy, dict) or not policy:
        return quarantine("extract_without_capture_policy")
    extracts = doc.get("extracts")
    if not isinstance(extracts, list):
        return quarantine("extract_without_records")
    items, rejected = [], []
    for index, extract in enumerate(extracts):
        try:
            items.append(_extract_item(index, extract, policy))
        except (ValueError, TypeError, AttributeError) as error:
            rejected.append({"index": index, "reason": str(error)})
    return {"status": "indexed", "reason": None, "adapter": "extract-1",
            "schema_version": EXTRACT_CONTRACT, "pipeline_version": None,
            "generated_at_raw": doc.get("generated_at"), "observed_at": observed,
            "knowledge_quality": quality, "items": items,
            "components": {"extracts": {"status": "imported", "items": len(items),
                                        "rejected": rejected}}}


def _extract_item(index, extract, policy):
    if not isinstance(extract, dict):
        raise ValueError("extract must be an object")
    if extract.get("kind") not in KINDS:
        raise ValueError(f"unknown kind {extract.get('kind')!r}")
    if not isinstance(extract.get("source_key"), str) or not extract["source_key"]:
        raise ValueError("extract needs source_key")
    validate_claims(extract.get("claims", {}))
    offset = extract.get("event_utc_offset")
    if offset is not None and not OFFSET.fullmatch(str(offset)):
        raise ValueError("event_utc_offset must look like +05:30")
    return {
        "pointer": f"/extracts/{index}",
        "source_key": extract["source_key"],
        "kind": extract["kind"],
        "native_evidence_id": extract.get("native_evidence_id"),
        "native_revision_id": extract.get("native_revision_id"),
        "origin_hint": extract.get("origin_hint"),
        "subject_id": extract.get("subject_id"),
        "publication": _time(extract.get("published_at"), extract.get("published_date")),
        "event": _time(extract.get("event_at"), extract.get("event_date"), offset),
        "payload": extract,
        "capture_policy": policy,
    }


def resolve_pointer(doc, pointer):
    """RFC 6901 JSON pointer lookup; raises KeyError when it does not resolve."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = doc
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                raise KeyError(pointer)
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise KeyError(pointer)
    return node
