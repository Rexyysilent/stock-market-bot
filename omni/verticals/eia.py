"""Pinned EIA API metadata and exact, revision-preserving daily observations.

The public route contract establishes transport, not its current facet names or
units. A locally supplied metadata snapshot is mandatory. No key is obtained by
this module and collectors are disabled unless explicitly invoked with a policy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from urllib.parse import urlencode

from ..acquisition import Deferred
from ..identities import digest, instant
from ..transport import EIA_METADATA_URL, EIA_DATA_URL, validate_eia_query, redact
from .common import (MAX_ROWS, atom, batch, candidate, checkpoint, decimal_string,
                     disabled_batch, escape_pointer, nonempty, resume_checkpoint, source_time)

VERSION = "omni-eia-api-1"
CONTRACT = {"version": VERSION, "metadata_route": EIA_METADATA_URL, "data_route": EIA_DATA_URL,
            "frequency": "daily", "maximum_page_rows": MAX_ROWS, "units": "pinned_route_metadata",
            "native_identity": ["route", "facets", "period", "data_column"],
            "corrections": "append_only_candidate_revisions", "stable_snapshot_pagination": False}


def pin_metadata(payload, *, observed_at, evidence_kind="unverified_local_input"):
    """Check the documented metadata envelope; do not guess any route fields."""
    instant(observed_at)
    if evidence_kind not in ("synthetic", "unverified_local_input", "historical_replay", "prospective"):
        raise ValueError("unsupported evidence kind")
    if not isinstance(payload, dict):
        raise ValueError("eia_metadata_schema_missing")
    payload = redact(payload)
    response = payload.get("response")
    if not isinstance(response, dict):
        raise ValueError("eia_metadata_schema_missing")
    columns, facets, frequencies = response.get("data"), response.get("facets"), response.get("frequency")
    if not isinstance(columns, dict) or not 1 <= len(columns) <= 20:
        raise ValueError("eia_data_columns_missing")
    for name, field in columns.items():
        if not nonempty(name, maximum=80) or not isinstance(field, dict) or not nonempty(field.get("units"), maximum=100):
            raise ValueError("eia_units_unverified")
    for values, label, cap in ((facets, "facet", 8), (frequencies, "frequency", 10)):
        if not isinstance(values, list) or not 1 <= len(values) <= cap:
            raise ValueError("eia_" + label + "_metadata_missing")
        ids = [item.get("id") for item in values if isinstance(item, dict)]
        if len(ids) != len(values) or any(not nonempty(x, maximum=80) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("eia_" + label + "_metadata_invalid")
    if "daily" not in [x["id"] for x in frequencies]:
        raise ValueError("eia_daily_frequency_unverified")
    if not nonempty(payload.get("apiVersion"), maximum=80):
        raise ValueError("eia_api_version_missing")
    return {"contract": VERSION, "route": EIA_METADATA_URL, "payload": payload,
            "snapshot_hash": digest(payload), "observed_at": observed_at,
            "evidence_kind": evidence_kind, "source_approval": "pending", "signal_eligible": False}


def checked_metadata(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("route") != EIA_METADATA_URL:
        raise ValueError("eia_route_metadata_unverified")
    if snapshot.get("snapshot_hash") != digest(snapshot.get("payload")):
        raise ValueError("eia_metadata_hash_mismatch")
    checked = pin_metadata(snapshot["payload"], observed_at=snapshot["observed_at"],
                           evidence_kind=snapshot.get("evidence_kind", "unverified_local_input"))
    if checked["snapshot_hash"] != snapshot["snapshot_hash"]:
        raise ValueError("eia_metadata_contains_credentials")
    return checked["payload"]["response"]


def parse_eia_observations(payload, *, metadata_snapshot, observed_at, source_policy_fingerprint,
                           query_fingerprint=None, evidence_kind="unverified_local_input"):
    metadata = checked_metadata(metadata_snapshot)
    if instant(metadata_snapshot["observed_at"]) > instant(observed_at):
        raise ValueError("eia_metadata_not_yet_known")
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        return batch("eia-nuclear", gaps=["source_content_unavailable"], query_hash=query_fingerprint)
    data = response["data"]
    gaps, rows = [], []
    if len(data) > MAX_ROWS:
        gaps.append("response_row_cap")
    if response.get("frequency") != "daily" or response.get("dateFormat") != "YYYY-MM-DD":
        return batch("eia-nuclear", gaps=["eia_period_contract_mismatch"], query_hash=query_fingerprint)
    if payload.get("apiVersion") != metadata_snapshot["payload"].get("apiVersion"):
        return batch("eia-nuclear", gaps=["eia_api_version_changed"], query_hash=query_fingerprint)
    facets = [x["id"] for x in metadata["facets"]]
    seen = set()
    for raw in data[:MAX_ROWS]:
        if not isinstance(raw, dict) or any(not nonempty(raw.get(key), maximum=200) for key in facets):
            gaps.append("eia_native_identity_unresolved")
            continue
        period = raw.get("period")
        try:
            if not isinstance(period, str) or date.fromisoformat(period).isoformat() != period:
                raise ValueError("invalid period")
        except (TypeError, ValueError):
            gaps.append("eia_period_unverified")
            continue
        selected = [col for col in metadata["data"] if col in raw]
        if not selected:
            gaps.append("eia_measurements_missing")
        for column in selected:
            unit = metadata["data"][column]["units"]
            reasons = ["entity_crosswalk_unreviewed", "correction_surveillance_unverified", "upstream_lineage_unreviewed"]
            # An explicit row unit may corroborate metadata but cannot override it.
            row_unit = raw.get(column + "-units")
            value = decimal_string(raw[column])
            if row_unit is not None and row_unit != unit:
                value = None
                reasons.append("eia_unit_mismatch")
                gaps.append("eia_unit_mismatch")
            if raw[column] is None:
                reasons.append("measurement_missing")
            elif value is None:
                reasons.append("nonfinite_or_unsupported_numeric_value")
                gaps.append("nonfinite_or_unsupported_numeric_value")
            native = digest([EIA_DATA_URL, {k: raw[k] for k in facets}, period, column])
            if native in seen:
                gaps.append("duplicate_native_observation")
                continue
            seen.add(native)
            row = candidate(source_id="eia-nuclear", native_id=native, observed_at=observed_at,
                            source_payload=raw, source_policy_fingerprint=source_policy_fingerprint,
                            contract_hash=digest([CONTRACT, metadata_snapshot["snapshot_hash"]]),
                            facts={"period": atom(raw, "/period"),
                                   "facets": {key: atom(raw, "/" + escape_pointer(key)) for key in facets},
                                   "measurement": {"column": column, "value": value,
                                                   "raw": atom(raw, "/" + escape_pointer(column)), "unit": unit,
                                                   "unit_proof": atom(metadata_snapshot["payload"], "/response/data/" + escape_pointer(column) + "/units")}},
                            identity={"native_facets": {k: raw[k] for k in facets}, "unit_id": None,
                                      "plant_id": None, "operator_id": None, "owner_id": None, "issuer_id": None},
                            times={"observation_period": source_time(period), "source_posted": source_time(None),
                                   "first_available": None, "precise_latency_eligible": False,
                                   "metadata_known_from": metadata_snapshot["observed_at"]},
                            lineage={"source_route": EIA_DATA_URL, "metadata_snapshot_hash": metadata_snapshot["snapshot_hash"],
                                     "api_version": payload.get("apiVersion"), "assertion_origin": "unresolved",
                                     "nrc_correspondence": "requires_exact_unit_period_and_review_receipt"},
                            reasons=reasons, evidence_kind=evidence_kind)
            rows.append(row)
    total = response.get("total")
    if type(total) is str and total.isdecimal() and len(total) <= 9:
        total = int(total)
    if type(total) is not int or total < len(data):
        total = None
        gaps.append("eia_total_unverified")
    result = batch("eia-nuclear", rows=rows, gaps=gaps, query_hash=query_fingerprint)
    result.update(returned_rows=len(data), advertised_total=total, outage_zero_inferred=False,
                  restart_inferred=False, first_availability_established=False)
    return result


def collect_eia(client, query_pairs, *, metadata_snapshot, observed_at, credential_ref=None,
                checkpoint_value=None, enabled=False, max_pages=1, evidence_kind="prospective"):
    checked_metadata(metadata_snapshot)
    query_pairs = validate_eia_query(query_pairs, metadata_snapshot)
    base_pairs = [(k, "0" if k == "offset" else v) for k, v in query_pairs]
    query_hash = digest([CONTRACT, metadata_snapshot["snapshot_hash"], base_pairs, digest(client.policy)])
    if not enabled:
        return disabled_batch("eia-nuclear", query_hash=query_hash)
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError("invalid_page_budget")
    offset, seen, page_hashes = resume_checkpoint(checkpoint_value, query_hash)
    rows, gaps, receipts = [], ["mutable_offset_pagination", "correction_surveillance_unverified"], []
    reached = False
    for _ in range(max_pages):
        pairs = [(k, str(offset) if k == "offset" else v) for k, v in query_pairs]
        try:
            fetched = client.read_json("GET", EIA_DATA_URL + "?" + urlencode(pairs), contract_id="eia-nuclear-data",
                                       credential_ref=credential_ref, metadata_snapshot=metadata_snapshot)
            parsed = parse_eia_observations(fetched["payload"], metadata_snapshot=metadata_snapshot,
                                           observed_at=observed_at, source_policy_fingerprint=digest(client.policy),
                                           query_fingerprint=query_hash, evidence_kind=evidence_kind)
        except (Deferred, PermissionError, ValueError) as exc:
            gaps.append(str(exc))
            break
        receipts.append(fetched["receipt"])
        gaps.extend(parsed["gaps"])
        page_hash = digest([[r["candidate_id"], r["revision_id"]] for r in parsed["rows"]])
        if page_hash in page_hashes:
            gaps.append("repeated_page")
            break
        page_hashes.add(page_hash)
        for row in parsed["rows"]:
            if row["candidate_id"] in seen:
                gaps.append("pagination_overlap")
            else:
                seen.add(row["candidate_id"])
                rows.append(row)
        count = parsed.get("returned_rows", 0)
        offset += count
        total = parsed.get("advertised_total")
        if total is not None and offset >= total:
            reached = True
            break
        if count == 0:
            gaps.append("empty_page_before_coverage")
            break
    if not reached:
        gaps.append("result_cap_before_coverage")
    continuation = None if reached else checkpoint(query_hash, offset, seen, page_hashes=page_hashes)
    return batch("eia-nuclear", rows=rows, gaps=gaps, continuation=continuation, query_hash=query_hash,
                 request_counts=client.counts, page_receipts=receipts,
                 index_set_complete=reached and not any(x not in {"mutable_offset_pagination", "correction_surveillance_unverified"} for x in gaps))
