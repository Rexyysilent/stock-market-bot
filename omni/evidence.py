"""Immutable evidence contracts and source-purpose checks for local review."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote, urlsplit

from .identities import digest, instant, time_interval

VERSION = "omni-evidence-2"
LEGACY_VERSION = "omni-evidence-1"
MAX_TEXT = 500_000
USES = {"acquire", "store_text", "local_analysis", "review_display", "model_processing", "commercial_display"}


def public_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        # Requests and origin servers can normalize paths after a raw prefix
        # check. Reject dot segments, encoded separators and nested escapes so
        # permission always describes the route actually requested.
        if (re.search(r"%(?:2f|5c|25)|%(?![0-9a-f]{2})", parsed.path, re.I)
                or any(part in (".", "..") for part in unquote(parsed.path).split("/"))
                or any(ord(c)<32 or ord(c)==127 for c in unquote(url))):
            return False
        host = parsed.hostname.lower()
        if host in ("localhost",) or host.endswith((".local", ".internal")) or "." not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return not any(c in url for c in ("\\", "\r", "\n", "\x00"))
    except (ValueError, TypeError):
        return False


def source_decision(policy, url, purpose):
    """Policy is an operator-owned snapshot, never supplied by a source document."""
    if not policy or not policy.get("policy_id") or not policy.get("version") or not policy.get("evidence"):
        return "source_not_approved"
    if not public_url(url):
        return "untrusted_url"
    parsed = urlsplit(url)
    routes = policy.get("routes", [])
    if not any(parsed.hostname.lower() == r.get("host", "").lower()
               and parsed.path.startswith(r.get("path_prefix", "/")) for r in routes):
        return "source_route_not_approved"
    if purpose not in USES or policy.get("uses", {}).get(purpose) is not True:
        return "purpose_not_approved"
    return None


def document(*, policy, url, native_id, text, title, published, observed_at,
             source_class, reporting_party, body_complete, origin_reference=None,
             completeness_reasons=(), evidence_kind="unverified_local_input"):
    """Create a versioned local record; no fetch and no privilege from source text."""
    if not isinstance(text, str) or len(text) > MAX_TEXT or not isinstance(title, str):
        raise ValueError("evidence text missing or oversized")
    if not isinstance(native_id, str) or not native_id.strip() or len(native_id) > 512:
        raise ValueError("source-native identity required")
    if source_class not in ("issuer", "regulator", "registry", "filing", "secondary", "discovery"):
        raise ValueError("unknown source class")
    if evidence_kind not in ("unverified_local_input", "synthetic", "historical_replay", "prospective"):
        raise ValueError("unknown evidence kind")
    instant(observed_at)
    for purpose in ("acquire", "store_text"):
        reason = source_decision(policy, url, purpose)
        if reason:
            raise PermissionError(reason)
    eid = "evidence:" + digest([policy["policy_id"], native_id])
    content_hash = digest(text)
    publication = time_interval(published)
    # Cross-route origin equivalence requires a separately verified reference.
    # Caller must not fill this from vendor related tags or similar headline text.
    filing_reference = (source_class == "filing" and isinstance(origin_reference, list)
        and len(origin_reference) == 2 and origin_reference[0] == "filing-accession"
        and isinstance(origin_reference[1], str)
        and re.fullmatch(r"urn:tag:sec.gov,2008:accession-number=\d{10}-\d{2}-\d{6}", origin_reference[1])
        and native_id.startswith(origin_reference[1] + "/")
        and (evidence_kind == "synthetic" or (urlsplit(url).hostname == "www.sec.gov"
            and origin_reference[1].rsplit("=",1)[1].replace("-", "") in urlsplit(url).path)))
    origin = origin_reference if filing_reference else [policy["policy_id"], native_id]
    record = {"contract": VERSION, "evidence_id": eid,
            "evidence_kind": evidence_kind,
            "native_id": native_id, "url": url, "title": title, "text": text,
            "content_hash": content_hash, "source_policy_id": policy["policy_id"],
            "source_policy_fingerprint": digest(policy), "source_class": source_class,
            "reporting_party": reporting_party, "publication": publication,
            "first_observed_at": observed_at, "retrieved_at": observed_at,
            "body_complete": body_complete is True, "completeness_reasons": list(completeness_reasons),
            "origin_cluster_id": "origin:" + digest(origin),
            "origin_basis": "filing_native_group" if filing_reference else "source_native",
            "origin_reference": origin if filing_reference else None,
            "unreviewed_origin_reference": origin_reference if origin_reference and not filing_reference else None,
            "signal_eligible": False}
    record["revision_id"] = digest(_revision_fields(record))
    return record


def _revision_fields(doc):
    return {k: doc.get(k) for k in ("content_hash", "title", "publication", "body_complete",
        "completeness_reasons", "url", "native_id", "source_policy_id", "source_policy_fingerprint",
        "source_class", "reporting_party", "origin_cluster_id", "origin_basis", "origin_reference", "evidence_kind")}


def check_document(doc):
    if not isinstance(doc, dict) or doc.get("contract") not in (VERSION, LEGACY_VERSION):
        raise ValueError("invalid evidence contract")
    required_strings = ("evidence_id", "revision_id", "native_id", "url", "title", "text",
                        "content_hash", "source_policy_id", "source_policy_fingerprint",
                        "source_class", "reporting_party", "origin_cluster_id", "origin_basis")
    if any(not isinstance(doc.get(key), str) for key in required_strings):
        raise ValueError("missing evidence field")
    if (len(doc["text"]) > MAX_TEXT or not doc["native_id"].strip() or len(doc["native_id"]) > 512
            or type(doc.get("body_complete")) is not bool or doc.get("signal_eligible") is not False
            or not isinstance(doc.get("completeness_reasons"), list)
            or any(not isinstance(r, str) for r in doc["completeness_reasons"])
            or doc.get("evidence_kind") not in ("unverified_local_input", "synthetic", "historical_replay", "prospective")
            or doc["source_class"] not in ("issuer", "regulator", "registry", "filing", "secondary", "discovery")):
        raise ValueError("invalid evidence field")
    publication = doc.get("publication")
    if not isinstance(publication, dict) or publication != time_interval(publication.get("raw")):
        raise ValueError("inconsistent publication precision")
    if doc["evidence_id"] != "evidence:" + digest([doc["source_policy_id"], doc["native_id"]]):
        raise ValueError("evidence identity mismatch")
    if doc.get("content_hash") != digest(doc.get("text")):
        raise ValueError("invalid evidence contract or content hash")
    expected = digest([doc["content_hash"], doc["title"], doc["publication"]["raw"],
                       doc["body_complete"], doc["completeness_reasons"]])
    if doc["contract"] == VERSION:
        expected = digest(_revision_fields(doc))
        origin = doc.get("origin_reference") if doc["origin_basis"] == "filing_native_group" else [doc["source_policy_id"], doc["native_id"]]
        if doc["origin_cluster_id"] != "origin:" + digest(origin):
            raise ValueError("unverified origin identity")
    if doc["revision_id"] != expected:
        raise ValueError("evidence revision mismatch")
    instant(doc["first_observed_at"])
    if instant(doc["retrieved_at"]) < instant(doc["first_observed_at"]):
        raise ValueError("retrieval precedes observation")
    return True


def span(doc, start, end):
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(doc["text"])):
        raise ValueError("invalid evidence span")
    return {"evidence_id": doc["evidence_id"], "revision_id": doc["revision_id"],
            "start": start, "end": end, "text": doc["text"][start:end]}


def check_span(doc, support):
    try:
        return support == span(doc, support["start"], support["end"])
    except (ValueError, KeyError, TypeError):
        return False
