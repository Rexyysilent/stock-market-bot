"""Verified route shapes, with acquisition disabled until operator source decisions."""
from __future__ import annotations

from .evidence import source_decision
from .identities import digest
from .attestations import validate_attestation

VERSION = "omni-route-contracts-1"
ROUTES = {
    "sec-full-index": {"url": "https://www.sec.gov/Archives/edgar/full-index/master.idx",
        "family": "sec_filings", "format": "sec_master_index", "enabled": False,
        "source": "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
        "date_basis": "filed_date_only", "stable_id": "accession_with_registrants",
        "retention": "weekly rebuild can change historical membership", "scope_gap": "not original public availability"},
    "sec-quarterly-index": {"url_template": "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx",
        "family": "sec_filings", "format": "sec_master_index", "enabled": False,
        "source": "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
        "date_basis": "filed_date_only", "stable_id": "accession_with_registrants",
        "retention": "historical indexes can be rebuilt", "scope_gap": "absence is a discrepancy"},
    "fda-oncology-index": {"url": "https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications",
        "family": "oncology_notification", "format": "named_html_table", "enabled": False,
        "source": "https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications",
        "date_basis": "date_only", "stable_id": "canonical_notice_url", "retention": "unknown",
        "scope_gap": "not all FDA approvals, devices, recalls or label changes"},
    "fda-medwatch-rss": {"url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml",
        "family": "safety_alert", "format": "rss", "enabled": False,
        "source": "https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program/medwatch-rss-feed",
        "date_basis": "require_explicit_timezone_per_item", "stable_id": "guid",
        "retention": "unknown", "scope_gap": "legacy route redirects; canonical route observed 2026-10-20; retention unknown"},
    "beam-issuer-rss": {"url": "https://investors.beamtx.com/rss/news-releases.xml",
        "family": "issuer_release", "format": "rss", "enabled": False,
        "source": "https://investors.beamtx.com/stockholder-tools/rss-feeds",
        "date_basis": "require_explicit_timezone_per_item", "stable_id": "guid",
        "retention": "unknown", "scope_gap": "single issuer feed; historical continuity not established"},
}


def route_plan(route_id, *, year=None, quarter=None):
    if route_id not in ROUTES:
        raise ValueError("canonical route unverified; no URL fallback")
    contract = dict(ROUTES[route_id])
    if "url_template" in contract:
        if type(year) is not int or not 1994 <= year <= 2200 or type(quarter) is not int or quarter not in range(1, 5):
            raise ValueError("explicit valid year and quarter required")
        contract["url"] = contract["url_template"].format(year=year, quarter=quarter)
    return {"contract": VERSION, "route_id": route_id, **contract, "max_response_bytes": 5_000_000,
            "max_feed_entries": 100, "redirects": "refused", "source_purpose_status": "pending",
            "network_requests": 0, "automatic_activation": False}


def policy_basis(policy):
    """Route approval references are excluded to avoid a circular approval hash.

    The approved subject separately binds the exact route contract and purpose.
    All ordinary source policy, allowed uses, limits and hosts remain bound.
    """
    return digest({k: v for k, v in policy.items() if k != "route_contracts"})


def check_route(policy, route_id, url, *, store=None, now=None, purpose="acquire",
                year=None, quarter=None, allow_synthetic=False):
    contract = route_plan(route_id, year=year, quarter=quarter)
    if url != contract["url"]:
        raise PermissionError("route_contract_mismatch")
    reason = source_decision(policy, url, purpose)
    if reason:
        raise PermissionError(reason)
    grant = policy.get("route_contracts", {}).get(route_id, {})
    expected = {"contract": VERSION, "route_id": route_id, "route_hash": digest(contract),
                "url": url, "purpose": purpose}
    body = {k: v for k, v in grant.items() if k != "approval_ref"}
    if body != expected or store is None or now is None:
        raise PermissionError("route_contract_approval_pending")
    try:
        validate_attestation(store, grant.get("approval_ref"), purpose="source_route_approval",
            subject_hash=digest(body), bindings={"source_policy_id": policy["policy_id"],
                "source_policy_fingerprint": policy_basis(policy)}, now=now, allow_synthetic=allow_synthetic)
    except (KeyError, ValueError) as exc:
        raise PermissionError("route_contract_approval_pending") from exc
    return contract
