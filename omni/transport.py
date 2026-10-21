"""Narrow JSON transport for documented read-only vertical searches.

Existing SourceClient.get is unchanged. Nothing in this module supplies an
operator policy, key, approval, scheduler or enabled collector configuration.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import json
import re
import socket
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

from .acquisition import SourceClient, Deferred, MAX_BYTES
from .evidence import source_decision
from .identities import digest

VERSION = "omni-read-only-json-1"
NRC_URL = "https://adams-api.nrc.gov/aps/api/search"
USA_AWARD_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USA_TRANSACTION_URL = "https://api.usaspending.gov/api/v2/transactions/"
EIA_METADATA_URL = "https://api.eia.gov/v2/nuclear-outages/facility-nuclear-outages"
EIA_DATA_URL = EIA_METADATA_URL + "/data"
NRC_FIELDS = frozenset({"AccessionNumber", "DocumentTitle", "AuthorName", "AuthorAffiliation", "AddresseeName",
                        "AddresseeAffiliation", "DocumentDate", "DocumentType", "Keyword", "DocketNumber",
                        "DateAddedTimestamp", "EstimatedPageCount", "Url"})
NRC_OPERATORS = frozenset({"contains", "notcontains", "starts", "notstarts", "equals", "notequals"})
USA_FIELDS = frozenset({"Award ID", "Recipient Name", "Recipient UEI", "Start Date", "End Date", "Award Amount",
                       "Awarding Agency", "Awarding Sub Agency", "Contract Award Type", "Award Type", "Funding Agency",
                       "Funding Sub Agency", "Description", "Last Modified Date", "Base Obligation Date",
                       "generated_internal_id", "Total Outlays", "recipient_id", "NAICS", "PSC"})
CONTRACTS = {
    "nrc-aps-search": {"version": "APS-guide-1.0", "method": "POST", "url": NRC_URL,
                       "body_schema": "nrc-aps-search-bounded-1", "credential": "nrc_header"},
    "nrc-aps-document": {"version": "APS-guide-1.0", "method": "GET", "url": NRC_URL + "/{accession}",
                         "body_schema": None, "credential": "nrc_header"},
    "eia-nuclear-metadata": {"version": "eia-openapi-20261020", "method": "GET", "url": EIA_METADATA_URL,
                             "body_schema": None, "credential": "eia_query"},
    "eia-nuclear-data": {"version": "eia-openapi-20261020", "method": "GET", "url": EIA_DATA_URL,
                         "body_schema": "eia-pinned-query-1", "credential": "eia_query"},
    "usa-award-search": {"version": "usaspending-awards-contract-20260610", "method": "POST", "url": USA_AWARD_URL,
                         "body_schema": "usa-award-search-bounded-1", "credential": None},
    "usa-transactions": {"version": "usaspending-transactions-contract-20200929", "method": "POST", "url": USA_TRANSACTION_URL,
                         "body_schema": "usa-transactions-bounded-1", "credential": None},
}


def _text(value, limit=512):
    return isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_nrc_query(body):
    required = {"q", "filters", "anyFilters", "legacyLibFilter", "mainLibFilter", "sort", "sortDirection", "skip"}
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError("unsupported_nrc_query_fields")
    if (not _text(body["q"]) or type(body["legacyLibFilter"]) is not bool or type(body["mainLibFilter"]) is not bool
            or not (body["legacyLibFilter"] or body["mainLibFilter"])
            or body["sort"] not in ("DocumentDate", "DateAddedTimestamp", "AccessionNumber")
            or not _integer(body["sortDirection"], 0, 1) or not _integer(body["skip"], 0, 60000)):
        raise ValueError("invalid_nrc_query")
    for key in ("filters", "anyFilters"):
        values = body[key]
        if not isinstance(values, list) or len(values) > 12:
            raise ValueError("unbounded_nrc_filters")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("field"), str) or item["field"] not in NRC_FIELDS or not _text(item.get("value")):
                raise ValueError("invalid_nrc_filter")
            if item["field"] in ("DocumentDate", "DateAddedTimestamp"):
                if set(item) != {"field", "value"}:
                    raise ValueError("invalid_nrc_date_filter")
                field = re.escape(item["field"])
                pattern = rf"\({field} (?:ge|le|eq) '(\d{{4}}-\d{{2}}-\d{{2}})'\)"
                pieces = item["value"].split(" and ")
                if not 1 <= len(pieces) <= 2:
                    raise ValueError("invalid_nrc_date_filter")
                for piece in pieces:
                    match = re.fullmatch(pattern, piece)
                    if not match or not _date(match[1]):
                        raise ValueError("invalid_nrc_date_filter")
            elif set(item) != {"field", "value", "operator"} or not isinstance(item["operator"], str) or item["operator"] not in NRC_OPERATORS:
                raise ValueError("invalid_nrc_text_filter")
    return deepcopy(body)


def validate_award_query(body):
    required = {"filters", "fields", "limit", "page", "sort", "order", "subawards"}
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError("unsupported_award_query_fields")
    if (not _integer(body["limit"], 1, 100) or not _integer(body["page"], 1, 600)
            or body["order"] not in ("asc", "desc") or body["subawards"] is not False
            or not isinstance(body["fields"], list) or not 1 <= len(body["fields"]) <= 25
            or any(not isinstance(x, str) for x in body["fields"])
            or len(set(body["fields"])) != len(body["fields"]) or not set(body["fields"]) <= USA_FIELDS
            or "generated_internal_id" not in body["fields"] or body["sort"] not in body["fields"]):
        raise ValueError("invalid_award_query")
    filters = body["filters"]
    if (not isinstance(filters, dict) or not {"award_type_codes", "time_period"} <= set(filters)
            or not set(filters) <= {"award_type_codes", "time_period", "award_ids", "recipient_search_text"}):
        raise ValueError("unsupported_award_filters")
    types = filters["award_type_codes"]
    if not isinstance(types, list) or not types or len(types) > 4 or any(x not in ("A", "B", "C", "D") for x in types):
        raise ValueError("procurement_contract_types_required")
    periods = filters["time_period"]
    if not isinstance(periods, list) or len(periods) != 1:
        raise ValueError("one_bounded_award_window_required")
    window = periods[0]
    if (not isinstance(window, dict) or set(window) != {"start_date", "end_date"}
            or not _date(window["start_date"]) or not _date(window["end_date"])
            or window["start_date"] > window["end_date"]
            or (date.fromisoformat(window["end_date"]) - date.fromisoformat(window["start_date"])).days > 366):
        raise ValueError("invalid_award_window")
    for key in ("award_ids", "recipient_search_text"):
        if key in filters and (not isinstance(filters[key], list) or not 1 <= len(filters[key]) <= 20
                               or any(not _text(x, 256) or not x for x in filters[key])):
            raise ValueError("invalid_award_identity_filter")
    return deepcopy(body)


def validate_transaction_query(body):
    if not isinstance(body, dict) or set(body) != {"award_id", "limit", "page", "sort", "order"}:
        raise ValueError("unsupported_transaction_query_fields")
    if (not _text(body["award_id"], 256) or not body["award_id"].startswith("CONT_AWD_")
            or not _integer(body["limit"], 1, 100) or not _integer(body["page"], 1, 600)
            or body["sort"] not in ("action_date", "modification_number") or body["order"] not in ("asc", "desc")):
        raise ValueError("invalid_transaction_query")
    return deepcopy(body)


def validate_eia_query(pairs, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("route") != EIA_METADATA_URL:
        raise ValueError("eia_route_metadata_unverified")
    payload = snapshot.get("payload")
    if not isinstance(payload, dict) or snapshot.get("snapshot_hash") != digest(payload):
        raise ValueError("eia_metadata_hash_mismatch")
    metadata = payload.get("response", {})
    columns = metadata.get("data")
    facets = metadata.get("facets")
    frequencies = metadata.get("frequency")
    if not isinstance(columns, dict) or not isinstance(facets, list) or not isinstance(frequencies, list):
        raise ValueError("eia_metadata_schema_missing")
    facet_ids = {x["id"] for x in facets if isinstance(x, dict) and _text(x.get("id"), 80)}
    frequency_ids = {x["id"] for x in frequencies if isinstance(x, dict) and _text(x.get("id"), 80)}
    base = {"frequency", "data[]", "start", "end", "offset", "length"}
    base |= {f"facets[{f}][]" for f in facet_ids}
    base |= {f"sort[{i}][{p}]" for i in range(len(facet_ids) + 1) for p in ("column", "direction")}
    if len(pairs) > 60 or any(k not in base or not _text(v, 200) for k, v in pairs):
        raise ValueError("unsupported_eia_query_fields")
    values = {}
    for key, value in pairs:
        values.setdefault(key, []).append(value)
    for key in ("frequency", "start", "end", "offset", "length"):
        if len(values.get(key, [])) != 1:
            raise ValueError("missing_or_duplicate_eia_query_field")
    if values["frequency"][0] not in frequency_ids or not values.get("data[]") or any(x not in columns for x in values["data[]"]):
        raise ValueError("eia_column_or_frequency_unverified")
    if not all(_date(values[x][0]) for x in ("start", "end")) or values["start"][0] > values["end"][0]:
        raise ValueError("bounded_daily_eia_window_required")
    if values["frequency"] != ["daily"]:
        raise ValueError("unsupported_eia_frequency")
    for key, cap in (("length", 5000), ("offset", 60000)):
        if not values[key][0].isdecimal() or not (0 if key == "offset" else 1) <= int(values[key][0]) <= cap:
            raise ValueError("invalid_eia_pagination")
    sort_columns = []
    for i in range(len(facet_ids) + 1):
        col, direction = values.get(f"sort[{i}][column]"), values.get(f"sort[{i}][direction]")
        if col is None and direction is None:
            break
        if not col or len(col) != 1 or col[0] not in facet_ids | {"period"} or direction not in (["asc"], ["desc"]):
            raise ValueError("invalid_eia_sort")
        sort_columns.append(col[0])
    if set(sort_columns) != facet_ids | {"period"} or len(sort_columns) != len(facet_ids) + 1:
        raise ValueError("eia_full_identity_sort_required")
    return deepcopy(pairs)


def redact(value, secrets=()):
    """Strip named credential fields and exact/URL-encoded secret echoes."""
    secret_names = {"api_key", "apikey", "ocp-apim-subscription-key", "authorization", "access_token", "token", "password", "secret"}
    variants = {x for s in secrets if isinstance(s, str) and s for x in (s, quote(s, safe=""), quote(s, safe="").lower())}
    def clean(item):
        if isinstance(item, dict):
            return {clean(str(k)): "[REDACTED]" if str(k).casefold() in secret_names else clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(x) for x in item]
        if isinstance(item, str):
            for secret in sorted(variants, key=len, reverse=True):
                item = item.replace(secret, "[REDACTED]")
            return re.sub(r"(?i)(api_key|access_token|token|ocp-apim-subscription-key)=([^&\s\"<>]+)", r"\1=[REDACTED]", item)
        return item
    return clean(value)


class ReadOnlyJSONClient(SourceClient):
    """Explicit operator-owned request grants augment, never relax, source policy."""

    def __init__(self, *args, credential_lookup=None, allow_synthetic_approvals=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.credential_lookup = credential_lookup
        self.allow_synthetic_approvals = allow_synthetic_approvals is True

    def read_json(self, method, url, *, contract_id, body=None, credential_ref=None, metadata_snapshot=None):
        # Validate method, route, body and purpose before credential access, DNS or budgets.
        reason = source_decision(self.policy, url, "acquire")
        if reason:
            raise PermissionError(reason)
        if contract_id == "nrc-aps-document":
            reason = source_decision(self.policy, url, "store_text")
            if reason:
                raise PermissionError(reason)
        contract = CONTRACTS.get(contract_id)
        parsed = urlsplit(url)
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if not contract or method != contract["method"] or parsed.fragment:
            raise PermissionError("read_only_method_not_approved")
        if contract_id == "nrc-aps-document":
            if not re.fullmatch(re.escape(NRC_URL) + r"/ML\d{5}[A-Z]\d{3}", base):
                raise PermissionError("read_only_route_not_approved")
        elif base != contract["url"]:
            raise PermissionError("read_only_route_not_approved")
        grant = {"contract_id": contract_id, "contract_hash": digest(contract), "method": method, "url": contract["url"]}
        grants = self.policy.get("read_only_requests", [])
        matching = [g for g in grants if isinstance(g, dict) and {k: v for k, v in g.items() if k != "approval_ref"} == grant] if isinstance(grants, list) else []
        if len(matching) != 1 or not isinstance(matching[0].get("approval_ref"), str):
            raise PermissionError("read_only_contract_not_approved")
        from .attestations import validate_attestation
        try:
            validate_attestation(self.store, matching[0]["approval_ref"], purpose="vertical_transport_contract",
                                 subject_hash=digest(grant), bindings={"source_policy_id": self.policy["policy_id"],
                                 "source_policy_fingerprint": digest({k: v for k, v in self.policy.items() if k != "read_only_requests"})},
                                 now=self.now.isoformat(), allow_synthetic=self.allow_synthetic_approvals)
        except (ValueError, KeyError, TypeError):
            raise PermissionError("read_only_approval_receipt_invalid") from None
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if contract_id == "eia-nuclear-data":
            validate_eia_query(pairs, metadata_snapshot)
        elif pairs:
            raise ValueError("unexpected_query_parameters")
        if method == "GET" and body is not None:
            raise ValueError("get_body_not_allowed")
        if contract_id == "nrc-aps-search":
            body = validate_nrc_query(body)
        elif contract_id == "usa-award-search":
            body = validate_award_query(body)
        elif contract_id == "usa-transactions":
            body = validate_transaction_query(body)
        if len(json.dumps(body, allow_nan=False, ensure_ascii=False).encode()) > 16000:
            raise ValueError("request_body_limit")
        if self.closed_until:
            raise Deferred("source_circuit_open")
        budget_kind = "document" if contract_id == "nrc-aps-document" else "metadata"
        if any(self.counts[kind] >= self.limits[kind] for kind in (budget_kind, "attempt")):
            raise Deferred("budget_deferred")
        headers = {**self.headers, "Accept": "application/json"}
        secret = None
        request_url = url
        if contract["credential"]:
            if (not isinstance(credential_ref, str) or not re.fullmatch(r"[a-z][a-z0-9._:-]{0,127}", credential_ref)
                    or self.credential_lookup is None):
                raise PermissionError("credential_reference_required")
            try:
                secret = self.credential_lookup(credential_ref)
            except Exception:
                raise PermissionError("credential_reference_unresolved") from None
            if not isinstance(secret, str) or not 1 <= len(secret) <= 512 or any(not 33 <= ord(c) <= 126 for c in secret):
                raise PermissionError("credential_reference_unresolved")
            if contract["credential"] == "nrc_header":
                headers["Ocp-Apim-Subscription-Key"] = secret
            else:
                request_url = base + "?" + urlencode(pairs + [("api_key", secret)])
        elif credential_ref is not None:
            raise ValueError("credential_not_required_for_contract")
        try:
            addresses = self.resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise Deferred("dns_unavailable") from None
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise PermissionError("nonpublic_dns_destination")
        self._reserve(budget_kind)
        for attempt in range(3):
            self._reserve("attempt")
            try:
                response = self.session.request(method, request_url, json=body if method == "POST" else None,
                                                headers=headers, timeout=(5, 20), allow_redirects=False, stream=True)
            except requests.RequestException:
                if attempt == 2:
                    raise Deferred("network_unavailable") from None
                self.sleep(2 ** attempt)
                continue
            try:
                if response.status_code in (429, 503):
                    retry = response.headers.get("Retry-After", "")
                    try:
                        delay = int(retry)
                        when = self.now + timedelta(seconds=max(60, min(delay, 86400)))
                    except (ValueError, TypeError, OverflowError):
                        try:
                            when = parsedate_to_datetime(retry)
                            if when.tzinfo is None:
                                raise ValueError("unknown retry timezone")
                            when = when.astimezone(timezone.utc)
                        except (ValueError, TypeError, OverflowError):
                            when = self.now + timedelta(minutes=15)
                    when = max(when, self.now + timedelta(minutes=1))
                    self.closed_until = when.isoformat()
                    self.store.backoff(self.policy["policy_id"], self.closed_until)
                    raise Deferred("source_throttled")
                if response.status_code in (500, 502, 504) and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise Deferred("http_" + str(response.status_code))
                if response.headers.get("Content-Type", "").split(";")[0].lower() != "application/json":
                    raise Deferred("unexpected_content_type")
                raw = bytearray()
                try:
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise Deferred("response_size_limit")
                except requests.RequestException:
                    raise Deferred("response_stream_incomplete") from None
                length = response.headers.get("Content-Length")
                if length and str(length).isdecimal() and not response.headers.get("Content-Encoding") and int(length) != len(raw):
                    raise Deferred("response_length_mismatch")
                if not raw:
                    raise Deferred("empty_response")
                try:
                    data = json.loads(raw, parse_float=str, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
                except (ValueError, UnicodeDecodeError):
                    raise Deferred("malformed_json") from None
                if not isinstance(data, dict):
                    raise Deferred("json_object_required")
                if contract_id == "nrc-aps-search" and isinstance(data.get("results"), list):
                    # Search responses may include the indexed body. This
                    # metadata route never exports or retains those fields.
                    for item in data["results"]:
                        if isinstance(item, dict) and isinstance(item.get("document"), dict):
                            item["document"] = {k: v for k, v in item["document"].items() if k in NRC_FIELDS}
                clean = redact(data, (secret,))
                safe_url = redact(url, (secret,))
                receipt = {"contract": VERSION, "contract_id": contract_id, "contract_hash": digest(contract),
                           "method": method, "url": safe_url, "body_hash": digest(body),
                           "credential_ref": credential_ref, "source_policy_fingerprint": digest(self.policy),
                           "response_hash": digest(clean), "observed_at": self.now.isoformat(),
                           "byte_count": len(raw), "http_status": 200, "request_counts": dict(self.counts),
                           "redaction_applied": True, "source_availability_established": False}
                return {"payload": clean, "receipt": receipt}
            finally:
                response.close()
        raise Deferred("network_unavailable")
