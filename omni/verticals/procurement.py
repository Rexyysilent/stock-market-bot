"""Disabled USAspending contract-award/transaction adapters.

The award aggregate and transaction delta are different facts. Source action
dates are neither public availability times nor current-period issuer revenue.
"""
from __future__ import annotations

from copy import deepcopy

from ..acquisition import Deferred
from ..identities import digest
from ..transport import USA_AWARD_URL, USA_TRANSACTION_URL, validate_award_query, validate_transaction_query
from .common import (MAX_ROWS, atom, batch, candidate, checkpoint, decimal_string, disabled_batch,
                     escape_pointer, nonempty, resume_checkpoint, source_time)

VERSION = "omni-usaspending-1"
AWARD_CONTRACT_DOCUMENT = "https://github.com/fedspendingtransparency/usaspending-api/blob/a31240757e5987b994692322c317ae55668c6983/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md"
TRANSACTION_CONTRACT_DOCUMENT = "https://github.com/fedspendingtransparency/usaspending-api/blob/277763cd72dc9e5838f52ec4176edbfef79fd5e6/usaspending_api/api_contracts/contracts/v2/transactions.md"
# Public document URLs retain the exact verified commit pins and their context.
CONTRACT = {"version": VERSION,
            "award_contract_commit": AWARD_CONTRACT_DOCUMENT.split("/")[6],
            "transaction_contract_commit": TRANSACTION_CONTRACT_DOCUMENT.split("/")[6],
            "award_route": USA_AWARD_URL, "transaction_route": USA_TRANSACTION_URL,
            "contract_types": ["A", "B", "C", "D"], "currency": None,
            "public_availability": "unknown", "update_deletion_window": "unverified",
            "stable_snapshot_pagination": False}
TRANSACTION_FIELDS = ("id", "type", "type_description", "action_date", "action_type", "action_type_description",
                      "modification_number", "description", "federal_action_obligation")
AGGREGATE_GAP = "transaction_and_correction_coverage_unverified"


def recipient_identity(*, recipient_uei=None, recipient_name=None):
    """A source recipient is a legal-entity lead, never a guessed listed parent."""
    return {"recipient_uei": recipient_uei if nonempty(recipient_uei) else None,
            "recipient_name": recipient_name if nonempty(recipient_name) else None,
            "issuer_parent": None, "reason": "parent_unresolved", "operator_equals_owner": None,
            "ownership_inferred_from_operator": False, "human_review_status": "pending"}


def _page(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None, ["source_content_unavailable"]
    page = payload.get("page_metadata")
    gaps = []
    if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1
            or type(page.get("hasNext")) is not bool):
        gaps.append("pagination_metadata_unverified")
    if len(payload["results"]) > MAX_ROWS:
        gaps.append("response_row_cap")
    return payload["results"][:MAX_ROWS], gaps


def parse_awards(payload, *, approved_query, observed_at, source_policy_fingerprint,
                 evidence_kind="unverified_local_input"):
    query = validate_award_query(approved_query)
    raw_rows, gaps = _page(payload)
    query_hash = digest([CONTRACT, {**query, "page": 1}, source_policy_fingerprint])
    if raw_rows is None:
        return batch("usaspending-awards", gaps=gaps, query_hash=query_hash)
    rows, seen = [], set()
    gaps.append(AGGREGATE_GAP)
    for source in raw_rows:
        if not isinstance(source, dict):
            gaps.append("malformed_award_row")
            continue
        raw = {k: deepcopy(source[k]) for k in query["fields"] if k in source}
        native = raw.get("generated_internal_id")
        if not nonempty(native, maximum=256) or not native.startswith("CONT_AWD_"):
            gaps.append("award_identity_unresolved")
            continue
        if native in seen:
            gaps.append("duplicate_award_identity")
            continue
        seen.add(native)
        facts = {k: atom(raw, "/" + escape_pointer(k)) for k in raw}
        amounts = {}
        reasons = [AGGREGATE_GAP, "recipient_parent_unresolved", "publication_lag_unknown"]
        for field, role in (("Award Amount", "award_aggregate"), ("Total Outlays", "reported_outlay_aggregate")):
            if field in raw:
                value = decimal_string(raw[field])
                if value is None and raw[field] is not None:
                    reasons.append("unsupported_amount_value")
                amounts[role] = {"value": value, "currency": None, "source": facts[field],
                                 "new_transaction_inferred": False, "issuer_revenue_inferred": False}
        rows.append(candidate(source_id="usaspending-awards", native_id=native, observed_at=observed_at,
                              source_payload=raw, facts={"source_fields": facts, "amounts": amounts,
                                                        "new_award_inferred": False, "new_obligation_inferred": False},
                              source_policy_fingerprint=source_policy_fingerprint, contract_hash=digest(CONTRACT),
                              identity={"award_family": native, **recipient_identity(recipient_uei=raw.get("Recipient UEI"),
                                                                                     recipient_name=raw.get("Recipient Name"))},
                              times={"award_start": source_time(raw.get("Start Date")), "award_end": source_time(raw.get("End Date")),
                                     "base_obligation_date": source_time(raw.get("Base Obligation Date")),
                                     "last_modified": source_time(raw.get("Last Modified Date")),
                                     "public_first_available": None, "publication_lag": "unknown",
                                     "precise_latency_eligible": False},
                              lineage={"source_route": USA_AWARD_URL, "aggregate_to_transaction": "unresolved"},
                              reasons=reasons, evidence_kind=evidence_kind))
    result = batch("usaspending-awards", rows=rows, gaps=gaps, query_hash=query_hash)
    result.update(page_metadata=deepcopy(payload.get("page_metadata")), returned_rows=len(raw_rows),
                  new_award_inferred=False, new_obligation_inferred=False)
    return result


def parse_transactions(payload, *, approved_query, observed_at, source_policy_fingerprint,
                        evidence_kind="unverified_local_input"):
    query = validate_transaction_query(approved_query)
    raw_rows, gaps = _page(payload)
    query_hash = digest([CONTRACT, {**query, "page": 1}, source_policy_fingerprint])
    if raw_rows is None:
        return batch("usaspending-transactions", gaps=gaps, query_hash=query_hash)
    rows, seen = [], set()
    for source in raw_rows:
        if not isinstance(source, dict):
            gaps.append("malformed_transaction_row")
            continue
        raw = {k: deepcopy(source[k]) for k in TRANSACTION_FIELDS if k in source}
        native = raw.get("id")
        if not nonempty(native, maximum=256) or raw.get("type") not in CONTRACT["contract_types"]:
            gaps.append("transaction_identity_or_type_unresolved")
            continue
        if native in seen:
            gaps.append("duplicate_transaction_identity")
            continue
        seen.add(native)
        if "federal_action_obligation" not in raw:
            gaps.append("transaction_obligation_missing")
        value = decimal_string(raw.get("federal_action_obligation"))
        reasons = ["currency_contract_unverified", "recipient_parent_unresolved", "publication_lag_unknown",
                   "transaction_update_deletion_window_unverified"]
        if value is None:
            reasons.append("obligation_value_unusable")
        modification = raw.get("modification_number")
        if modification is None:
            reasons.append("modification_number_missing")
        elif not nonempty(modification, maximum=100):
            gaps.append("modification_number_unverified")
            continue
        action_time = source_time(raw.get("action_date"))
        if action_time["precision"] != "date":
            reasons.append("transaction_action_date_unverified")
        facts = {k: atom(raw, "/" + escape_pointer(k)) for k in raw}
        rows.append(candidate(source_id="usaspending-transactions", native_id=digest([query["award_id"], native]),
                              observed_at=observed_at, source_payload=raw, source_policy_fingerprint=source_policy_fingerprint,
                              contract_hash=digest(CONTRACT),
                              facts={"source_fields": facts, "obligation_change": value, "currency": None,
                                     "amount_role": "signed_transaction_obligation", "award_ceiling": None,
                                     "option_exercise": False, "cash_proceeds": None, "issuer_revenue_inferred": False},
                              identity={"award_family": query["award_id"], "transaction_id": native,
                                        "modification_number": modification, **recipient_identity()},
                              times={"action_date": action_time, "source_posted": source_time(None),
                                     "public_first_available": None, "publication_lag": "unknown",
                                     "precise_latency_eligible": False},
                              lineage={"source_route": USA_TRANSACTION_URL,
                                       "query_award_id": query["award_id"], "transaction_id_stability": "source_contract_only"},
                              reasons=reasons, evidence_kind=evidence_kind))
    gaps.append("transaction_update_deletion_window_unverified")
    result = batch("usaspending-transactions", rows=rows, gaps=gaps, query_hash=query_hash)
    result.update(page_metadata=deepcopy(payload.get("page_metadata")), returned_rows=len(raw_rows),
                  revenue_inferred=False, source_availability_established=False)
    return result


def collect_procurement(client, approved_query, *, kind, observed_at, enabled=False,
                         checkpoint_value=None, max_pages=1, evidence_kind="prospective"):
    if kind not in ("awards", "transactions"):
        raise ValueError("unsupported_procurement_kind")
    validate, parse, route, contract_id = ((validate_award_query, parse_awards, USA_AWARD_URL, "usa-award-search")
                                          if kind == "awards" else
                                          (validate_transaction_query, parse_transactions, USA_TRANSACTION_URL, "usa-transactions"))
    query = validate(approved_query)
    query_hash = digest([CONTRACT, {**query, "page": 1}, digest(client.policy)])
    source_id = "usaspending-" + kind
    if not enabled:
        return disabled_batch(source_id, query_hash=query_hash)
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError("invalid_page_budget")
    offset, seen, page_hashes = resume_checkpoint(checkpoint_value, query_hash)
    page = offset + 1
    rows, gaps, receipts, reached = [], ["mutable_page_pagination"], [], False
    for _ in range(max_pages):
        body = {**query, "page": page}
        try:
            fetched = client.read_json("POST", route, contract_id=contract_id, body=body)
            parsed = parse(fetched["payload"], approved_query=body, observed_at=observed_at,
                           source_policy_fingerprint=digest(client.policy), evidence_kind=evidence_kind)
        except (ValueError, PermissionError, Deferred) as exc:
            gaps.append(str(exc))
            break
        receipts.append(fetched["receipt"])
        gaps.extend(parsed["gaps"])
        metadata = parsed.get("page_metadata")
        if not isinstance(metadata, dict) or metadata.get("page") != page or type(metadata.get("hasNext")) is not bool:
            gaps.append("pagination_metadata_unverified")
            break
        page_hash = digest([[row["candidate_id"], row["revision_id"]] for row in parsed["rows"]])
        if page_hash in page_hashes:
            gaps.append("repeated_page")
            break
        page_hashes.add(page_hash)
        for row in parsed["rows"]:
            if row["candidate_id"] in seen:
                gaps.append("pagination_overlap")
            else:
                rows.append(row)
                seen.add(row["candidate_id"])
        if not metadata["hasNext"]:
            reached = True
            break
        if not parsed.get("returned_rows") or (kind == "transactions" and metadata.get("next") != page + 1):
            gaps.append("pagination_continuation_unverified")
            break
        page += 1
    if not reached:
        gaps.append("result_cap_before_coverage")
    gap_allowed = {"mutable_page_pagination", AGGREGATE_GAP, "transaction_update_deletion_window_unverified"}
    return batch(source_id, rows=rows, gaps=gaps, query_hash=query_hash,
                 continuation=None if reached else checkpoint(query_hash, page - 1, seen, page_hashes=page_hashes),
                 request_counts=client.counts, page_receipts=receipts,
                 index_set_complete=reached and not any(x not in gap_allowed for x in gaps))
