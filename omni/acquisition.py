"""Bounded explicit acquisition, XML/HTML parsing and conservative coverage receipts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import hashlib
import json
import re
import socket
import time
from urllib.parse import urljoin, urlsplit, urlencode
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from source_request_gate import wait_for_sec
from .evidence import public_url, source_decision
from .identities import instant, digest

MAX_BYTES = 5_000_000


class Deferred(RuntimeError):
    """A visible retry/defer reason; caller must persist coverage debt."""


class SourceClient:
    def __init__(self, store, policy, *, user_agent, now, session=None, resolver=socket.getaddrinfo,
                 sec_gate=wait_for_sec, sleep=time.sleep, max_attempts=30, max_metadata=12, max_documents=8):
        if not user_agent or "@" not in user_agent:
            raise ValueError("a real operator contact User-Agent is required")
        if not all(type(x) is int and 0 <= x <= ceiling for x, ceiling in
                   ((max_attempts,30),(max_metadata,12),(max_documents,8))):
            raise ValueError("cycle limits exceed the approved initial budget")
        self.store, self.policy, self.now = store, policy, instant(now)
        self.session = session or requests.Session()
        self.resolver, self.sec_gate, self.sleep = resolver, sec_gate, sleep
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        self.limits = {"attempt": max_attempts, "metadata": max_metadata, "document": max_documents}
        self.counts = dict.fromkeys(self.limits, 0)
        retry_at = store.backoff(policy["policy_id"])
        self.closed_until = retry_at if retry_at and instant(retry_at) > self.now else None

    def _reserve(self, kind):
        daily = {"metadata": 300, "document": 80, "attempt": 750}[kind]
        if self.counts[kind] >= self.limits[kind] or not self.store.reserve(kind, day=self.now.date().isoformat(), limit=daily):
            raise Deferred("budget_deferred:" + kind)
        self.counts[kind] += 1

    def get(self, url, *, kind="metadata", content_types=("application/json", "application/atom+xml", "application/rss+xml", "application/xml", "text/xml", "text/html", "text/plain")):
        reason = source_decision(self.policy, url, "acquire")
        if reason:
            raise PermissionError(reason)
        if self.closed_until:
            raise Deferred("source_circuit_open:" + self.closed_until)
        host = urlsplit(url).hostname
        try:
            addresses = self.resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise Deferred("dns_unavailable") from None
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise PermissionError("nonpublic_dns_destination")
        self._reserve(kind)
        for attempt in range(3):
            self._reserve("attempt")
            if host in ("www.sec.gov", "data.sec.gov"):
                try:
                    self.sec_gate()
                except RuntimeError as exc:
                    # The local shared gate may refuse backlog/clock skew. Keep
                    # that outage visible as coverage debt without an HTTP call.
                    raise Deferred("sec_rate_gate_unavailable") from exc
            try:
                response = self.session.get(url, headers=self.headers, timeout=(5,20), allow_redirects=False, stream=True)
            except requests.RequestException:
                if attempt == 2:
                    raise Deferred("network_unavailable") from None
                self.sleep(2 ** attempt)
                continue
            try:
                if response.status_code in (429, 503):
                    # Persist the upstream retry request in the receipt; do not
                    # sleep for minutes or silently restart a blocked source.
                    self.closed_until = str(response.headers.get("Retry-After") or "next_cycle")[:120]
                    try:
                        delay = int(self.closed_until)
                        retry_at = self.now + timedelta(seconds=max(60, delay))
                    except (ValueError, OverflowError):
                        try:
                            retry_at = parsedate_to_datetime(self.closed_until)
                            if retry_at.tzinfo is None:
                                raise ValueError("Retry-After timezone unknown")
                            retry_at = retry_at.astimezone(timezone.utc)
                        except (ValueError, TypeError, OverflowError):
                            retry_at = self.now + timedelta(minutes=15)
                    self.store.backoff(self.policy["policy_id"], max(retry_at, self.now + timedelta(minutes=1)).isoformat())
                    raise Deferred("source_throttled:" + self.closed_until)
                if response.status_code in (500, 502, 504) and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise Deferred("http_" + str(response.status_code))
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type not in content_types:
                    raise Deferred("unexpected_content_type:" + content_type[:100])
                body = bytearray()
                try:
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise Deferred("response_size_limit")
                except requests.RequestException:
                    raise Deferred("response_stream_incomplete") from None
                length=response.headers.get('Content-Length')
                if length and length.isdecimal() and not response.headers.get('Content-Encoding') and int(length)!=len(body):
                    raise Deferred("response_length_mismatch")
                if not body:
                    raise Deferred("empty_response")
                return bytes(body)
            finally:
                response.close()
        raise Deferred("network_unavailable")


def parse_atom(raw, *, base_url, result_cap=100):
    if len(raw) > MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe or oversized XML")
    root = ET.fromstring(raw)
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("not an Atom feed")
    rows, gaps, seen = [], [], {}
    if not entries: gaps.append("empty_feed_inventory")
    for entry in entries[:result_cap]:
        val = lambda name: entry.findtext("{http://www.w3.org/2005/Atom}" + name)
        link = entry.find("{http://www.w3.org/2005/Atom}link")
        url = urljoin(base_url, link.get("href", "")) if link is not None else None
        stamp = val("updated") or val("published")
        try:
            instant(stamp)
        except (ValueError, TypeError):
            gaps.append("unusable_source_time")
            continue
        if not val("id") or not val("title") or not public_url(url):
            gaps.append("malformed_feed_entry")
            continue
        row={"native_id": val("id"), "title": val("title"), "url": url,
             "published": stamp, "summary": val("summary"), "signal_eligible": False}
        if row["native_id"] in seen:
            gaps.append("conflicting_duplicate_native_id" if row!=seen[row["native_id"]] else "duplicate_native_id")
            continue
        seen[row["native_id"]]=row; rows.append(row)
    return {"rows": rows, "result_cap_reached": len(entries) >= result_cap, "gaps": sorted(set(gaps))}


def edgar_url(start=0, count=100):
    if type(start) is not int or start < 0 or count != 100:
        raise ValueError("invalid filing page")
    return "https://www.sec.gov/cgi-bin/browse-edgar?" + urlencode({"action":"getcurrent", "type":"", "owner":"exclude", "start":start, "count":count, "output":"atom"})


def collect_edgar(client, *, watermark=None, max_pages=3, continuation=None):
    """Broad company-filings metadata, independent of headline display/cohort caps."""
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError("invalid pagination budget")
    previous = instant(watermark) if watermark else None
    offset = int(continuation) if continuation else 0
    if offset < 0 or offset % 100:
        raise ValueError("invalid reconciliation offset")
    start = max(0, offset-100)
    rows, gaps, newest, reached = [], [], None, False
    page_proofs, observations = [], []
    seen = set()
    for page in range(max_pages):
        try:
            raw = client.get(edgar_url(start+page*100))
            parsed = parse_atom(raw, base_url="https://www.sec.gov")
        except (Deferred, ValueError, ET.ParseError) as exc:
            gaps.append(str(exc)); break
        gaps.extend(parsed["gaps"])
        page_proofs.append({"offset": start+page*100, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                            "native_ids": [r["native_id"] for r in parsed["rows"]],
                            "publication_times": [r["published"] for r in parsed["rows"]],
                            "response_complete": not parsed["gaps"]})
        observations.append({"rows": parsed["rows"], "raw_sha256": hashlib.sha256(raw).hexdigest(),
                             "offset": start+page*100, "gaps": parsed["gaps"],
                             "result_cap_reached": parsed["result_cap_reached"]})
        for row in parsed["rows"]:
            stamp = instant(row["published"])
            newest = row["published"] if newest is None or stamp > instant(newest) else newest
            if previous and stamp <= previous:
                reached = True
            # Keep the overlapping watermark entries for idempotent reconciliation.
            if row["native_id"] not in seen:
                seen.add(row["native_id"]); rows.append(row)
        if reached or previous is None:
            reached = True; break
        if not parsed["result_cap_reached"]:
            gaps.append("feed_window_before_watermark"); break
    if not reached:
        gaps.append("result_cap_before_watermark")
    if previous is None:
        gaps.append("initial_window_only")
    if continuation:
        # A mutable feed offset can recover candidates, but it cannot prove an
        # uninterrupted interval. Keep the gap until a head scan reaches the
        # old watermark or a separately verified index reconciliation exists.
        gaps.append("reconciliation_requires_contiguous_verification")
    proof = {"contract": "omni-feed-traversal-1", "source_id": client.policy["policy_id"],
             "previous_watermark": watermark, "watermark": newest, "started_from_head": start == 0 and not continuation,
             "pages": page_proofs, "gaps": sorted(set(gaps))}
    proof["proof_hash"] = digest(proof)
    return {"rows": rows, "watermark": newest, "complete": reached and not gaps,
            "initial_seed": previous is None,
            "gaps": sorted(set(gaps)), "continuation": None if reached else str(start+(page+1)*100),
            "request_counts": dict(client.counts), "scope": "latest EDGAR filings, not SEC newsroom",
            "coverage_proof": proof, "page_observations": observations}


def filing_documents(raw, *, filing_url, limit=3):
    """Read the filing's document table, not a guessed EX-99.1 filename."""
    if len(raw) > MAX_BYTES:
        raise ValueError("filing index too large")
    if type(limit) is not int or not 1 <= limit <= 100: raise ValueError("invalid document listing cap")
    soup = BeautifulSoup(raw, "html.parser")
    result = []
    for table in soup.select("table.tableFile"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            form = cells[3].get_text(" ", strip=True).upper()
            description = cells[1].get_text(" ", strip=True)
            link = cells[2].find("a", href=True)
            if not link or not (form in ("8-K", "8-K/A", "6-K", "6-K/A") or form.startswith("EX-99")):
                continue
            url = urljoin(filing_url, link["href"])
            if not public_url(url) or urlsplit(url).hostname != "www.sec.gov" or not urlsplit(url).path.startswith("/Archives/edgar/data/"):
                continue
            result.append({"url": url, "type": form, "description": description,
                           "filed_or_furnished": "not_established"})
    return {"documents": result[:limit], "deferred": max(0,len(result)-limit),
            "gaps": ["exhibit_listing_missing"] if not result else ["document_budget_deferred"] if len(result)>limit else []}


def extract_document_text(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("document too large")
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    incomplete = len(text) < 120 or bool(re.search(r"access denied|enable javascript|captcha|just a moment", text[:1000], re.I))
    return {"text": text, "body_complete": not incomplete, "gaps": ["content_incomplete"] if incomplete else []}
