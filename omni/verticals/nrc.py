"""APS guide v1.0 metadata parser and explicitly invoked disabled collector."""
from __future__ import annotations

from copy import deepcopy
import re

from ..acquisition import Deferred
from ..identities import digest, instant, time_interval
from ..transport import CONTRACTS, NRC_URL, NRC_FIELDS, validate_nrc_query
from .common import (MAX_ROWS, atom, batch, candidate, checkpoint, disabled_batch,
                     nonempty, resume_checkpoint, source_time)

VERSION = "omni-nrc-aps-1"
GUIDE = "https://adams-search.nrc.gov/assets/APS-API-Guide.pdf"
CONTRACT = {"version": VERSION, "source": GUIDE, "guide_version": "1.0",
            "document_fields": sorted(NRC_FIELDS), "envelope": ["count", "results", "pageNumber"],
            "count_semantics": "not_documented_as_stable_total", "source_added_timezone": None,
            "stable_snapshot_pagination": False}


def parse_nrc_metadata(payload, *, query_fingerprint, observed_at, source_policy_fingerprint,
                       evidence_kind="unverified_local_input"):
    instant(observed_at)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return batch("nrc-aps", gaps=["source_content_unavailable"], query_hash=query_fingerprint)
    rows, gaps, seen = [], [], set()
    if len(payload["results"]) > MAX_ROWS:
        gaps.append("result_cap_before_coverage")
    for entry in payload["results"][:MAX_ROWS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("document"), dict):
            gaps.append("nrc_document_shape_unverified")
            continue
        # APS may return indexed content even in search. No body is retained or
        # interpreted through this metadata contract.
        raw = {k: deepcopy(v) for k, v in entry["document"].items() if k in NRC_FIELDS}
        accession = raw.get("AccessionNumber")
        if not isinstance(accession, str) or not re.fullmatch(r"ML\d{5}[A-Z]\d{3}", accession):
            gaps.append("nrc_accession_unresolved")
            continue
        if accession in seen:
            gaps.append("duplicate_native_id_in_page")
            continue
        seen.add(accession)
        docket = raw.get("DocketNumber")
        if docket is not None and (not isinstance(docket, list) or any(not nonempty(x, maximum=80) for x in docket)):
            gaps.append("nrc_docket_shape_unverified")
            continue
        bad = False
        for name in ("DocumentType", "AuthorName", "AuthorAffiliation", "AddresseeName", "AddresseeAffiliation", "Keyword"):
            if name in raw and raw[name] is not None and (not isinstance(raw[name], list) or any(not nonempty(x) for x in raw[name])):
                bad = True
        if bad or not nonempty(raw.get("DocumentTitle"), maximum=4096):
            gaps.append("nrc_metadata_shape_unverified")
            continue
        facts = {k: atom(raw, "/" + k) for k in sorted(raw)}
        posted = source_time(raw.get("DateAddedTimestamp"))
        reasons = ["project_identity_unreviewed", "licensing_stage_not_established_by_metadata"]
        if posted["normalized_instant"] is None:
            reasons.append("source_posted_timezone_or_precision_unknown")
        rows.append(candidate(source_id="nrc-aps", native_id=accession, observed_at=observed_at,
                              source_payload=raw, facts=facts, source_policy_fingerprint=source_policy_fingerprint,
                              contract_hash=digest(CONTRACT), identity={"accession": accession, "dockets": docket or [],
                                                                     "project_id": None, "issuer_id": None},
                              times={"document_date": time_interval(raw.get("DocumentDate")), "source_posted": posted,
                                     "effective_date": time_interval(None)},
                              lineage={"host": "NRC", "reporting_party": "unresolved_metadata_author",
                                       "independence_established": False}, reasons=reasons, evidence_kind=evidence_kind))
    gaps.extend(["aps_pagination_completeness_unverified", "correction_surveillance_unverified"])
    result = batch("nrc-aps", rows=rows, gaps=gaps, query_hash=query_fingerprint)
    count = payload.get("count")
    result["source_count"] = count if type(count) is int and count >= 0 else None
    result["source_page_number"] = payload.get("pageNumber") if type(payload.get("pageNumber")) is int else None
    result["returned_rows"] = len(payload["results"])
    result["precise_latency_eligible"] = False
    return result


def collect_nrc_aps(client, *, approved_query, observed_at, checkpoint_value=None,
                    credential_ref=None, enabled=False, max_pages=1, evidence_kind="prospective"):
    query = validate_nrc_query(approved_query)
    query["skip"] = 0
    query_hash = digest([CONTRACT, query, digest(client.policy)])
    if enabled is not True:
        return disabled_batch("nrc-aps", query_hash=query_hash)
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError("invalid pagination budget")
    offset, seen, page_hashes = resume_checkpoint(checkpoint_value, query_hash)
    rows, gaps, receipts = [], [], []
    if checkpoint_value:
        gaps.append("resumed_mutable_search_not_continuous")
    for _ in range(max_pages):
        body = {**query, "skip": offset}
        try:
            response = client.read_json("POST", NRC_URL, contract_id="nrc-aps-search", body=body,
                                        credential_ref=credential_ref)
            parsed = parse_nrc_metadata(response["payload"], query_fingerprint=query_hash,
                                        observed_at=observed_at, source_policy_fingerprint=digest(client.policy),
                                        evidence_kind=evidence_kind)
        except (Deferred, PermissionError, ValueError) as exc:
            gaps.append(str(exc)); break
        receipts.append(response["receipt"])
        gaps.extend(parsed["gaps"])
        page_hash = digest([(r["native_id"], r["revision_id"]) for r in parsed["rows"]])
        if page_hash in page_hashes:
            gaps.append("repeated_page"); break
        page_hashes.add(page_hash)
        for row in parsed["rows"]:
            if row["native_id"] in seen:
                gaps.append("overlapping_mutable_page")
                continue
            seen.add(row["native_id"]); rows.append(row)
        returned = parsed.get("returned_rows", 0)
        if returned == 0:
            gaps.append("empty_page_not_completeness_proof"); break
        offset += returned
    else:
        gaps.append("result_cap_before_coverage")
    return batch("nrc-aps", rows=rows, gaps=gaps,
                 continuation=checkpoint(query_hash, offset, seen, page_hashes=page_hashes),
                 query_hash=query_hash, request_counts=client.counts, page_receipts=receipts)


def hydration_status(*, metadata_available, download_status, body_complete=False):
    # A newly indexed document can remain unavailable for about 20 minutes. No
    # promise is made about a particular document or scheduled retry time.
    if metadata_available and download_status in (404, 409, 429, 503):
        return {"hydration": "pending", "body_complete": False, "coverage_gap": "document_not_available", "new_claim": False}
    return {"hydration": "acquired" if download_status == 200 and body_complete else "pending",
            "body_complete": download_status == 200 and body_complete is True,
            "coverage_gap": None if download_status == 200 and body_complete else "document_content_unverified",
            "new_claim": False}


STAGES = (
    ("intent", r"(?:letter of intent|notified (?:the )?NRC of (?:its )?intent)"),
    ("pre_application", r"(?:pre[- ]application (?:engagement|meeting|discussions?))"),
    ("application_submitted", r"(?:submitted (?:its |the |an? )?(?:license |licensing )?application)"),
    ("application_accepted", r"(?:accepted (?:the )?application for (?:formal |technical )?review)"),
    ("technical_review", r"(?:technical review|safety evaluation report)"),
    ("environmental_review", r"(?:environmental review|environmental (?:assessment|impact statement))"),
    ("hearing", r"(?:licensing hearing|mandatory hearing)"),
    ("authorization_issued", r"(?:issued (?:the |a )?(?:license|authorization))"),
    ("authorization_amended", r"(?:amended (?:the |a )?(?:license|authorization))"),
    ("authorization_denied", r"(?:denied (?:the |a )?(?:license|authorization|application))"),
    ("authorization_withdrawn", r"(?:withdrew (?:the |a )?(?:license|authorization|application))"),
)


def licensing_stages(text, *, subject_hint=None, subject_kind=None, reporting_party="unresolved"):
    """Bounded stage mentions, exact local text spans; never a license decision."""
    if not isinstance(text, str) or len(text) > 100_000:
        raise ValueError("invalid licensing text")
    stages, refusals = [], []
    for start in re.finditer(r"[^.!?\n]+[.!?]?", text):
        sentence = start.group()
        for stage, pattern in STAGES:
            for match in re.finditer(pattern, sentence, re.I):
                before = sentence[:match.start()]
                if re.search(r"\b(?:not|never|no|denies|denied that|didn't|hasn't)\b", before, re.I):
                    refusals.append("negated_licensing_stage"); continue
                if re.search(r"\b(?:plans?|expects?|will|may|could|would|intends?|previously|historical)\b", before, re.I):
                    # A later conjunction with an explicit past-tense verb can
                    # distinguish observed engagement from a preceding forecast.
                    clause = re.split(r"\band\b|;", before, flags=re.I)[-1]
                    if not re.search(r"\b(?:began|started|held)\b", clause, re.I):
                        refusals.append("future_or_historical_stage"); continue
                low, high = start.start() + match.start(), start.start() + match.end()
                stages.append({"stage": stage, "support": {"kind": "text_span", "snapshot_hash": digest(text),
                                                            "start": low, "end": high, "text": text[low:high]},
                               "subject_id": None, "subject_hint": subject_hint, "subject_kind": subject_kind,
                               "reporting_party": reporting_party, "human_review_status": "pending"})
    future = []
    for match in re.finditer(r"\bQ[1-4] \d{4}\b", text):
        future.append({"time": time_interval(match.group()), "support": {"snapshot_hash": digest(text),
                        "start": match.start(), "end": match.end(), "text": match.group()}})
    return {"stages": stages, "future_events": future, "license_issued": False,
            "reason_codes": sorted(set(refusals + ["authorization_scope_and_decision_review_required"])),
            "signal_eligible": False, "human_review_status": "pending"}


def licensing_paragraphs(paragraphs):
    """Keep local subject contexts separate until an evidenced crosswalk exists."""
    if not isinstance(paragraphs, list) or len(paragraphs) > 100:
        raise ValueError("invalid licensing paragraphs")
    contexts = []
    for index, text in enumerate(paragraphs):
        parsed = licensing_stages(text)
        historic = bool(re.search(r"\b(?:previously announced|historical|in prior years)\b", text, re.I))
        contexts.append({"paragraph": index, "text_hash": digest(text), "stages": parsed["stages"],
                         "subject_id": None, "subject_context": "paragraph:" + str(index),
                         "contract_context": "historical" if historic else "unresolved",
                         "new_license": False, "new_obligation": False,
                         "human_review_status": "pending", "signal_eligible": False})
    return {"contexts": contexts, "cross_paragraph_subject_merge": False, "signal_eligible": False}
