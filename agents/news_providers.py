"""Normalized, independently testable headline-source adapters.

The adapters in this module deliberately do not score, deduplicate, or select
headlines.  They perform transport and source-specific parsing only, then emit
one common record shape for ``NewsAgent`` to orchestrate.

Network access is injectable through ``request_get`` so every parser can be
covered by offline fixtures. API credentials are sent using each provider's
required header or query-parameter contract and are never copied into errors
or health metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
import hashlib
import html
import time
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests

from coverage_policy import fmp_coverage_contract


RequestGet = Callable[..., Any]

DEFAULT_GOOGLE_PRIMARY_QUERY = "Stock Market OR Global Economy OR Finance"
DEFAULT_GOOGLE_FALLBACK_QUERIES = (
    "stock market today OR S&P 500 OR Nasdaq OR Dow",
)

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "mc_cid",
    "mc_eid",
    "oc",
    "ref",
}

_PUBLISHER_ALIASES = {
    "alpha vantage": "Alpha Vantage",
    "ap": "Associated Press",
    "ap news": "Associated Press",
    "associated press": "Associated Press",
    "business wire": "Business Wire",
    "federal reserve": "Federal Reserve",
    "federal reserve board": "Federal Reserve",
    "globe newswire": "GlobeNewswire",
    "globenewswire": "GlobeNewswire",
    "nasdaq": "Nasdaq",
    "nasdaq trader": "Nasdaq Trader",
    "pr newswire": "PR Newswire",
    "reuters": "Reuters",
    "sec": "SEC",
    "securities and exchange commission": "SEC",
    "u.s. securities and exchange commission": "SEC",
    "yahoo": "Yahoo Finance",
    "yahoo finance": "Yahoo Finance",
}

_PUBLISHER_DOMAINS = {
    "Alpha Vantage": "alphavantage.co",
    "Associated Press": "apnews.com",
    "Business Wire": "businesswire.com",
    "Federal Reserve": "federalreserve.gov",
    "GlobeNewswire": "globenewswire.com",
    "Nasdaq": "nasdaq.com",
    "Nasdaq Trader": "nasdaqtrader.com",
    "PR Newswire": "prnewswire.com",
    "Reuters": "reuters.com",
    "SEC": "sec.gov",
    "Yahoo Finance": "yahoo.com",
}

_PRESS_RELEASE_DOMAINS = {
    "businesswire.com",
    "globenewswire.com",
    "prnewswire.com",
}


@dataclass
class ProviderResult:
    """Outcome of one provider fetch.

    ``status`` is one of ``ok``, ``empty``, ``error``, or ``skipped``.  An
    adapter may return ``ok`` with ``metadata.partial_failures`` when one feed
    failed but another feed still yielded usable records.
    """

    name: str
    status: str
    records: list[dict[str, Any]]
    error: Optional[str] = None
    configured: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    intake_records: Optional[list[dict[str, Any]]] = None

    def health(self) -> dict[str, Any]:
        """Return a JSON-safe provider-health summary."""
        result = {
            "name": self.name,
            "status": self.status,
            "configured": self.configured,
            "record_count": len(self.records),
            "error": self.error,
        }
        result.update(dict(self.metadata))
        return result


def normalize_domain(value: Optional[str]) -> Optional[str]:
    """Normalize a URL or hostname for publisher grouping.

    This intentionally avoids guessing an eTLD+1 (which breaks domains such as
    ``co.uk``).  Known publisher subdomains are collapsed explicitly.
    """
    if not value:
        return None
    candidate = html.unescape(str(value)).strip().lower()
    if not candidate:
        return None
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    host = parsed.hostname
    if not host:
        host = candidate.split("/", 1)[0].split(":", 1)[0]
    host = host.strip(".")
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if host == "yahoo.com" or host.endswith(".yahoo.com"):
        return "yahoo.com"
    if host == "reuters.com" or host.endswith(".reuters.com"):
        return "reuters.com"
    if host == "sec.gov" or host.endswith(".sec.gov"):
        return "sec.gov"
    if host == "federalreserve.gov" or host.endswith(".federalreserve.gov"):
        return "federalreserve.gov"
    if host == "nasdaqtrader.com" or host.endswith(".nasdaqtrader.com"):
        return "nasdaqtrader.com"
    return host or None


def canonicalize_url(value: Optional[str]) -> Optional[str]:
    """Remove fragments and common tracking parameters from an article URL."""
    if not value:
        return None
    raw = html.unescape(str(value)).strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    host = (parsed.hostname or "").lower()
    if not host:
        return raw
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "")
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, val))
    query.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(query), ""))


def normalize_publisher(
    publisher: Optional[str],
    url: Optional[str] = None,
    title: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(display_name, normalized_domain)``.

    Explicit provider metadata wins, then the URL host.  A title suffix is
    used only when it matches a known publisher alias, avoiding destructive
    guesses on ordinary hyphenated headlines.
    """
    raw_name = html.unescape(str(publisher)).strip() if publisher else ""
    alias = _PUBLISHER_ALIASES.get(raw_name.casefold()) if raw_name else None
    display = alias or raw_name or None

    if display is None and title and " - " in title:
        suffix = html.unescape(title.rsplit(" - ", 1)[-1]).strip()
        display = _PUBLISHER_ALIASES.get(suffix.casefold())

    domain = normalize_domain(url)
    if display in _PUBLISHER_DOMAINS:
        domain = _PUBLISHER_DOMAINS[display]
    if display is None and domain:
        display = domain
    return display, domain


def classify_source(default_class: str, publisher_domain: Optional[str]) -> str:
    """Identify press-release wires while retaining the provider trust class."""
    if publisher_domain in _PRESS_RELEASE_DOMAINS:
        return "press_release"
    return default_class


def make_source_record_id(
    provider: str,
    canonical_url: Optional[str],
    title: str,
    published: Optional[str],
    raw_id: Optional[str] = None,
) -> str:
    """Build a deterministic provider-scoped source-document identifier."""
    material = raw_id or canonical_url or f"{title}|{published or ''}"
    digest = hashlib.sha256(str(material).encode("utf-8")).hexdigest()[:24]
    slug = re.sub(r"[^a-z0-9]+", "-", provider.casefold()).strip("-")
    return f"{slug}:{digest}"


def _utc_z(value: Any) -> Optional[str]:
    """Normalize trusted observation timestamps, not source publication time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        dt = None
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                pass
        if dt is None:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                try:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tickers(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    result = []
    for value in values:
        ticker = str(value or "").strip().upper()
        if ticker and ticker not in result:
            result.append(ticker)
    return result


def _market_tickers(values: Any) -> list[str]:
    """Normalize vendor symbols such as ``NYSE:RHP`` to ``RHP``."""
    result = []
    for value in _tickers(values):
        ticker = value.rsplit(":", 1)[-1]
        if ticker and ticker not in result:
            result.append(ticker)
    return result


def _record_unchecked(
    *,
    title: Any,
    link: Any,
    published: Any,
    source_time_kind: str,
    provider_seen_at: Any,
    provider: str,
    publisher: Optional[str],
    publisher_url: Optional[str],
    source_class: str,
    source_record_id: Optional[str] = None,
    tickers: Any = None,
    ticker_metadata_kind: str = "subject",
    summary: Any = None,
) -> Optional[dict[str, Any]]:
    clean_title = _clean_text(title)
    if not clean_title:
        return None
    clean_link = _clean_text(link) or None
    canonical_url = canonicalize_url(clean_link)
    publisher_name, publisher_domain = normalize_publisher(
        publisher, publisher_url or canonical_url, clean_title
    )
    clean_ticker_metadata_kind = str(ticker_metadata_kind).strip().casefold()
    if clean_ticker_metadata_kind not in {"subject", "related"}:
        raise ValueError("unsupported ticker metadata kind")
    clean_published = _clean_text(published) or None
    return {
        "title": clean_title,
        "link": clean_link,
        "canonical_url": canonical_url,
        "published": clean_published,
        "source_time_kind": source_time_kind,
        "provider_seen_at": _utc_z(provider_seen_at),
        "provider": provider,
        "publisher": publisher_name,
        "publisher_domain": publisher_domain,
        "source_class": classify_source(source_class, publisher_domain),
        "source_record_id": make_source_record_id(
            provider,
            canonical_url,
            clean_title,
            clean_published,
            raw_id=source_record_id,
        ),
        "tickers": _tickers(tickers),
        "ticker_metadata_kind": clean_ticker_metadata_kind,
        "summary": _clean_text(summary) or None,
    }


def _record(**kwargs) -> Optional[dict[str, Any]]:
    """Normalize one row without allowing malformed siblings to poison a feed."""
    try:
        return _record_unchecked(**kwargs)
    except (TypeError, ValueError, UnicodeError):
        return None


def _response_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if content is not None:
        return str(content).encode("utf-8")
    return str(getattr(response, "text", "")).encode("utf-8")


def _response_json(response: Any) -> Any:
    json_method = getattr(response, "json", None)
    if callable(json_method):
        return json_method()
    return json.loads(_response_bytes(response).decode("utf-8"))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _child_text(element: ET.Element, names: Iterable[str]) -> Optional[str]:
    wanted = {name.casefold() for name in names}
    for child in list(element):
        if _local_name(child.tag).casefold() in wanted:
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return None


def _entry_link(element: ET.Element) -> Optional[str]:
    for child in list(element):
        if _local_name(child.tag).casefold() != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in ("alternate", ""):
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_generic_feed(
    raw: bytes,
    *,
    provider: str,
    provider_seen_at: Any,
    default_publisher: Optional[str],
    default_publisher_url: Optional[str],
    source_class: str,
    limit: int,
) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    entries = [node for node in root.iter() if _local_name(node.tag) in ("item", "entry")]
    records = []
    for entry in entries[:limit]:
        source_node = next(
            (child for child in list(entry) if _local_name(child.tag) == "source"),
            None,
        )
        source_text = None
        source_url = None
        if source_node is not None:
            source_text = "".join(source_node.itertext()).strip() or None
            source_url = source_node.attrib.get("url") or source_node.attrib.get("href")
        rec = _record(
            title=_child_text(entry, ("title",)),
            link=_entry_link(entry),
            published=_child_text(entry, ("pubDate", "published", "updated", "date")),
            source_time_kind="published",
            provider_seen_at=provider_seen_at,
            provider=provider,
            publisher=source_text or default_publisher,
            publisher_url=source_url or default_publisher_url,
            source_class=source_class,
            source_record_id=_child_text(entry, ("guid", "id")),
            tickers=[],
            summary=_child_text(entry, ("description", "summary", "content")),
        )
        if rec:
            records.append(rec)
    return records


def _parse_nasdaq_halts(
    raw: bytes,
    *,
    provider: str,
    provider_seen_at: Any,
    publisher: str,
    publisher_url: str,
    source_class: str,
    limit: int,
) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    entries = [node for node in root.iter() if _local_name(node.tag) == "item"]
    records = []
    for entry in entries[:limit]:
        symbol = _child_text(entry, ("IssueSymbol", "Symbol"))
        issue = _child_text(entry, ("IssueName", "SecurityName"))
        reason = _child_text(entry, ("ReasonCode", "Reason"))
        halt_date = _child_text(entry, ("HaltDate",))
        halt_time = _child_text(entry, ("HaltTime",))
        halt_timestamp = None
        if halt_date and halt_time:
            for halt_format in (
                "%m/%d/%Y %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    halt_dt = datetime.strptime(
                        f"{halt_date} {halt_time}", halt_format
                    ).replace(tzinfo=ZoneInfo("America/New_York"))
                    halt_timestamp = halt_dt.isoformat()
                    break
                except ValueError:
                    pass
        title = _child_text(entry, ("title",))
        if not title or title.strip().upper() == str(symbol or "").upper():
            label = symbol or issue or "Security"
            issue_suffix = f" ({issue})" if issue and issue != symbol else ""
            reason_suffix = f" - {reason}" if reason else ""
            title = f"{label}{issue_suffix} trading halt{reason_suffix}"
        details = []
        if halt_date or halt_time:
            details.append(f"halt={halt_date or '?'} {halt_time or ''}".strip())
        for field_name, label in (
            ("ResumptionDate", "resume_date"),
            ("ResumptionQuoteTime", "resume_quote"),
            ("ResumptionTradeTime", "resume_trade"),
        ):
            value = _child_text(entry, (field_name,))
            if value:
                details.append(f"{label}={value}")
        rec = _record(
            title=title,
            link=_entry_link(entry) or publisher_url,
            published=halt_timestamp,
            source_time_kind="event_time",
            provider_seen_at=provider_seen_at,
            provider=provider,
            publisher=publisher,
            publisher_url=publisher_url,
            source_class=source_class,
            source_record_id=_child_text(entry, ("guid", "id"))
            or f"{symbol or ''}|{halt_date or ''}|{halt_time or ''}",
            tickers=[symbol] if symbol else [],
            summary="; ".join(details),
        )
        if rec:
            rec["canonical_url"] = None
            rec["source_record_id"] = make_source_record_id(provider, None, title, halt_timestamp, raw_id=f"{symbol or ''}|{halt_date or ''}|{halt_time or ''}|{reason or ''}")
            records.append(rec)
    return records


class OfficialFeedProvider:
    """Fetch an allowlisted collection of regulator/exchange RSS or Atom feeds.

    Each feed spec accepts ``url``, ``publisher``, ``publisher_url``, ``parser``
    (``generic`` or ``nasdaq_halts``), ``source_class``, and optional ``limit``.
    URLs intentionally live in configuration rather than this module so a feed
    change is reviewable without changing parser code.
    """

    name = "Official Feeds"

    def __init__(
        self,
        feeds: Optional[Iterable[Mapping[str, Any]]] = None,
        *,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        limit: int = 40,
    ):
        self.feeds = [dict(feed) for feed in (feeds or [])]
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.limit = limit

    def fetch(self) -> ProviderResult:
        if not self.feeds:
            return ProviderResult(
                self.name,
                "skipped",
                [],
                configured=False,
                metadata={"reason": "no_feeds_configured", "feeds": []},
            )
        records = []
        feed_health = []
        for spec in self.feeds:
            url = str(spec.get("url") or "").strip()
            publisher = str(spec.get("publisher") or spec.get("name") or "Official Source")
            if not url:
                feed_health.append({
                    "publisher": publisher,
                    "status": "error",
                    "record_count": 0,
                    "error": "missing_url",
                })
                continue
            try:
                response = self.request_get(
                    url,
                    headers={
                        "User-Agent": "StockMarketBot/2.6 headline adapter",
                        **dict(spec.get("headers") or {}),
                    },
                    timeout=self.timeout,
                )
                status_code = int(getattr(response, "status_code", 0))
                if status_code != 200:
                    raise ValueError(f"http_status_{status_code}")
                parser = spec.get("parser", "generic")
                parser_limit = int(spec.get("limit", self.limit))
                if parser == "nasdaq_halts":
                    parsed = _parse_nasdaq_halts(
                        _response_bytes(response),
                        provider=self.name,
                        provider_seen_at=self.now,
                        publisher=publisher,
                        publisher_url=spec.get("publisher_url") or url,
                        source_class=spec.get("source_class", "official"),
                        limit=parser_limit,
                    )
                elif parser == "generic":
                    parsed = _parse_generic_feed(
                        _response_bytes(response),
                        provider=self.name,
                        provider_seen_at=self.now,
                        default_publisher=publisher,
                        default_publisher_url=spec.get("publisher_url") or url,
                        source_class=spec.get("source_class", "official"),
                        limit=parser_limit,
                    )
                else:
                    raise ValueError(f"unsupported_parser_{parser}")
                records.extend(parsed)
                feed_health.append({
                    "publisher": publisher,
                    "status": "ok" if parsed else "empty",
                    "record_count": len(parsed),
                    "result_limit": parser_limit,
                    "result_limit_reached": len(parsed) >= parser_limit,
                    "error": None,
                })
            except Exception as exc:  # isolated per official feed
                feed_health.append({
                    "publisher": publisher,
                    "status": "error",
                    "record_count": 0,
                    "error": str(exc)[:200],
                })
        failures = [row for row in feed_health if row["status"] == "error"]
        if records:
            status = "ok"
            error = None
        elif failures:
            status = "error"
            error = f"{len(failures)}/{len(feed_health)} official feeds failed"
        else:
            status = "empty"
            error = None
        return ProviderResult(
            self.name,
            status,
            records,
            error=error,
            metadata={
                "fetched_at": _utc_z(self.now),
                "feeds": feed_health,
                "partial_failures": len(failures),
                "result_limit_reached": any(f.get("result_limit_reached") for f in feed_health),
            },
        )


class FMPNewsProvider:
    """Financial Modeling Prep news adapter.

    Paid plans use ticker-filtered Stock News. HTTP 402 falls back to the
    narrower, publisher-owned FMP Articles feed available to Basic accounts.
    The fallback is explicit in provider health metadata.
    """

    name = "FMP"
    default_endpoint = "https://financialmodelingprep.com/stable/news/stock"
    default_articles_endpoint = (
        "https://financialmodelingprep.com/stable/fmp-articles"
    )

    def __init__(
        self,
        api_key: Optional[str],
        *,
        tickers: Optional[Iterable[str]] = None,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        articles_endpoint: Optional[str] = None,
        endpoint: Optional[str] = None,
        limit: int = 100,
    ):
        self.api_key = (api_key or "").strip()
        self.tickers = _tickers(tickers)
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.endpoint = endpoint or self.default_endpoint
        self.articles_endpoint = (
            articles_endpoint or self.default_articles_endpoint
        )
        self.limit = limit

    def fetch(self) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                self.name,
                "skipped",
                [],
                configured=False,
                metadata={"reason": "missing_api_key"},
            )
        headers = {
            "User-Agent": "StockMarketBot/2.6 headline adapter",
            "apikey": self.api_key,
        }
        fallback_used = False
        params: dict[str, Any] = {
            "page": 0,
            "limit": self.limit,
        }
        if self.tickers:
            params["symbols"] = ",".join(self.tickers)
        try:
            response = self.request_get(
                self.endpoint,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            status_code = int(getattr(response, "status_code", 0))
            primary_status_code = status_code
            if status_code == 402:
                fallback_used = True
                response = self.request_get(
                    self.articles_endpoint,
                    params={"page": 0, "limit": self.limit},
                    headers=headers,
                    timeout=self.timeout,
                )
                status_code = int(getattr(response, "status_code", 0))
            if status_code != 200:
                raise ValueError(f"http_status_{status_code}")
            payload = _response_json(response)
            if isinstance(payload, dict):
                message = (
                    payload.get("Error Message")
                    or payload.get("error")
                    or payload.get("message")
                )
                raise ValueError(str(message or "unexpected_object_payload")[:200])
            if not isinstance(payload, list):
                raise ValueError("unexpected_payload_type")
            records = []
            for row in payload:
                if not isinstance(row, dict):
                    continue
                rec = _record(
                    title=row.get("title"),
                    link=row.get("url") or row.get("link"),
                    published=(
                        row.get("publishedDate")
                        or row.get("published_at")
                        or row.get("date")
                    ),
                    source_time_kind="published",
                    provider_seen_at=self.now,
                    provider=self.name,
                    publisher=row.get("site") or row.get("source"),
                    publisher_url=row.get("url") or row.get("link"),
                    source_class="api_news_discovery",
                    source_record_id=row.get("id") or row.get("link"),
                    tickers=_market_tickers(
                        row.get("symbol") or row.get("symbols") or row.get("tickers")
                    ),
                    summary=(
                        row.get("text") or row.get("summary") or row.get("content")
                    ),
                )
                if rec:
                    records.append(rec)
            return ProviderResult(
                self.name,
                "ok" if records else "empty",
                records,
                metadata={
                    "fetched_at": _utc_z(self.now),
                    "malformed_record_count": sum(isinstance(row, dict) for row in payload) - len(records),
                    "result_limit": self.limit,
                    "result_limit_reached": len(payload) >= self.limit,
                    "fallback_used": fallback_used,
                    "coverage": (
                        "fmp_articles" if fallback_used else "ticker_stock_news"
                    ),
                    "primary_status_code": primary_status_code,
                    "request_scope_fingerprint": hashlib.sha256(json.dumps({
                        "symbols": self.tickers, "limit": self.limit,
                        "coverage": "fmp_articles" if fallback_used else "ticker_stock_news",
                    }, sort_keys=True).encode()).hexdigest(),
                    "coverage_contract": fmp_coverage_contract(
                        "fmp_articles" if fallback_used else "ticker_stock_news"
                    ),
                },
            )
        except Exception as exc:
            error = str(exc).replace(self.api_key, "[redacted]")[:200]
            return ProviderResult(
                self.name,
                "error",
                [],
                error=error,
                metadata={"fetched_at": _utc_z(self.now)},
            )


class AlphaVantageNewsProvider:
    """Alpha Vantage NEWS_SENTIMENT adapter."""

    name = "Alpha Vantage News"
    default_endpoint = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: Optional[str],
        *,
        tickers: Optional[Iterable[str]] = None,
        topics: Optional[Iterable[str]] = None,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        endpoint: Optional[str] = None,
        limit: int = 100,
    ):
        self.api_key = (api_key or "").strip()
        self.tickers = _tickers(tickers)
        self.topics = [str(topic) for topic in (topics or []) if str(topic).strip()]
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.endpoint = endpoint or self.default_endpoint
        self.limit = limit

    def fetch(self) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                self.name,
                "skipped",
                [],
                configured=False,
                metadata={"reason": "missing_api_key"},
            )
        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "time_from": (self.now - timedelta(days=3)).strftime("%Y%m%dT%H%M"),
            "sort": "LATEST",
            "limit": self.limit,
            "apikey": self.api_key,
        }
        if self.tickers:
            params["tickers"] = ",".join(self.tickers)
        if self.topics:
            params["topics"] = ",".join(self.topics)
        try:
            response = self.request_get(
                self.endpoint,
                params=params,
                headers={"User-Agent": "StockMarketBot/2.6 headline adapter"},
                timeout=self.timeout,
            )
            status_code = int(getattr(response, "status_code", 0))
            if status_code != 200:
                raise ValueError(f"http_status_{status_code}")
            payload = _response_json(response)
            if not isinstance(payload, dict):
                raise ValueError("unexpected_payload_type")
            api_error = (
                payload.get("Error Message")
                or payload.get("Information")
                or payload.get("Note")
            )
            if api_error:
                raise ValueError(f"api_error: {str(api_error)[:160]}")
            feed = payload.get("feed", [])
            if not isinstance(feed, list):
                raise ValueError("unexpected_feed_type")
            records = []
            for row in feed:
                if not isinstance(row, dict):
                    continue
                sentiment = row.get("ticker_sentiment") or []
                row_tickers = [
                    item.get("ticker")
                    for item in sentiment
                    if isinstance(item, dict) and item.get("ticker")
                ]
                rec = _record(
                    title=row.get("title"),
                    link=row.get("url"),
                    published=_utc_z(row.get("time_published")),
                    source_time_kind="published",
                    provider_seen_at=self.now,
                    provider=self.name,
                    publisher=row.get("source"),
                    publisher_url=row.get("source_domain") or row.get("url"),
                    source_class="api_news_discovery",
                    source_record_id=row.get("url"),
                    tickers=row_tickers,
                    ticker_metadata_kind="related",
                    summary=row.get("summary"),
                )
                if rec:
                    records.append(rec)
            return ProviderResult(
                self.name,
                "ok" if records else "empty",
                records,
                metadata={
                    "fetched_at": _utc_z(self.now),
                    "items_reported": payload.get("items"),
                    "malformed_record_count": sum(isinstance(row, dict) for row in feed) - len(records),
                    "result_limit": self.limit,
                    "result_limit_reached": len(feed) >= self.limit,
                },
            )
        except Exception as exc:
            error = str(exc).replace(self.api_key, "[redacted]")[:200]
            return ProviderResult(
                self.name,
                "error",
                [],
                error=error,
                metadata={"fetched_at": _utc_z(self.now)},
            )


class GDELTNewsProvider:
    """GDELT DOC 2.0 discovery adapter.

    GDELT's ``seendate`` is retained as ``provider_seen_at`` and is not
    relabeled as publisher publication time.  Consequently ``published`` is
    null and ``source_time_kind`` is ``provider_seen`` for these records.
    """

    name = "GDELT"
    default_endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(
        self,
        query: str,
        *,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        endpoint: Optional[str] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        limit: int = 100,
    ):
        self.query = str(query or "").strip()
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.endpoint = endpoint or self.default_endpoint
        self.sleep_fn = sleep_fn or time.sleep
        self.limit = limit

    def fetch(self) -> ProviderResult:
        if not self.query:
            return ProviderResult(
                self.name,
                "skipped",
                [],
                configured=False,
                metadata={"reason": "missing_query"},
            )
        params = {
            "query": self.query,
            "mode": "ArtList",
            "maxrecords": self.limit,
            "format": "json",
            "sort": "DateDesc",
            "timespan": "3d",
        }
        attempt_count = 0
        transient_retries = 0
        try:
            for attempt in range(2):
                attempt_count += 1
                response = self.request_get(
                    self.endpoint,
                    params=params,
                    headers={"User-Agent": "StockMarketBot/2.6 headline adapter"},
                    timeout=self.timeout,
                )
                status_code = int(getattr(response, "status_code", 0))
                if status_code not in (429, 502, 503, 504) or attempt:
                    break
                transient_retries += 1
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                try:
                    delay = min(max(float(retry_after or 5), 0), 10)
                except (TypeError, ValueError):
                    delay = 5
                self.sleep_fn(delay)
            if status_code != 200:
                raise ValueError(f"http_status_{status_code}")
            try:
                payload = _response_json(response)
            except Exception:
                body = _clean_text(
                    _response_bytes(response).decode("utf-8", errors="replace")
                )
                detail = body[:160] or "empty_body"
                raise ValueError(f"non_json_response: {detail}") from None
            if not isinstance(payload, dict):
                raise ValueError("unexpected_payload_type")
            if payload.get("error"):
                raise ValueError(str(payload["error"])[:200])
            articles = payload.get("articles", [])
            if not isinstance(articles, list):
                raise ValueError("unexpected_articles_type")
            records = []
            for row in articles:
                if not isinstance(row, dict):
                    continue
                rec = _record(
                    title=row.get("title"),
                    link=row.get("url"),
                    published=None,
                    source_time_kind="provider_seen",
                    provider_seen_at=row.get("seendate"),
                    provider=self.name,
                    publisher=row.get("domain"),
                    publisher_url=row.get("url") or row.get("domain"),
                    source_class="global_discovery",
                    source_record_id=row.get("url"),
                    tickers=[],
                    summary=None,
                )
                if rec:
                    records.append(rec)
            return ProviderResult(
                self.name,
                "ok" if records else "empty",
                records,
                metadata={
                    "fetched_at": _utc_z(self.now),
                    "timestamp_policy": "seendate_is_provider_seen_not_published",
                    "attempt_count": attempt_count,
                    "transient_retries": transient_retries,
                    "malformed_record_count": sum(isinstance(row, dict) for row in articles) - len(records),
                    "result_limit": self.limit,
                    "result_limit_reached": len(articles) >= self.limit,
                },
            )
        except Exception as exc:
            return ProviderResult(
                self.name,
                "error",
                [],
                error=str(exc)[:200],
                metadata={
                    "fetched_at": _utc_z(self.now),
                    "attempt_count": attempt_count,
                    "transient_retries": transient_retries,
                },
            )


class BatchedGDELTNewsProvider:
    """Run bounded GDELT queries sequentially under its public rate limit."""

    name = "GDELT"

    def __init__(
        self,
        queries: Iterable[str],
        *,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        endpoint: Optional[str] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        batch_delay_seconds: float = 5,
        limit: int = 100,
    ):
        if isinstance(queries, str):
            queries = (queries,)
        self.queries = tuple(
            query
            for query in (str(value or "").strip() for value in queries)
            if query
        )
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.endpoint = endpoint
        self.sleep_fn = sleep_fn or time.sleep
        self.batch_delay_seconds = max(float(batch_delay_seconds), 0.0)
        self.limit = limit

    def fetch(self) -> ProviderResult:
        if not self.queries:
            return ProviderResult(
                self.name,
                "skipped",
                [],
                configured=False,
                metadata={"reason": "missing_query", "query_count": 0},
            )

        records = []
        seen = set()
        intake_records = []
        batches = []
        total_attempts = 0
        total_retries = 0
        total_malformed = 0
        failure_count = 0
        first_error = None

        for index, query in enumerate(self.queries):
            if index:
                self.sleep_fn(self.batch_delay_seconds)
            result = GDELTNewsProvider(
                query,
                now=self.now,
                timeout=self.timeout,
                request_get=self.request_get,
                endpoint=self.endpoint,
                sleep_fn=self.sleep_fn,
                limit=self.limit,
            ).fetch()
            metadata = result.metadata or {}
            intake_records.extend(result.records)
            total_attempts += int(metadata.get("attempt_count", 0) or 0)
            total_retries += int(metadata.get("transient_retries", 0) or 0)
            total_malformed += int(
                metadata.get("malformed_record_count", 0) or 0
            )
            if result.status == "error":
                failure_count += 1
                first_error = first_error or result.error
            batches.append({
                "index": index,
                "query_length": len(query),
                "result_limit_reached": bool(metadata.get("result_limit_reached")),
                "status": result.status,
                "record_count": len(result.records),
                "error": result.error,
                "attempt_count": metadata.get("attempt_count", 0),
                "transient_retries": metadata.get("transient_retries", 0),
            })
            for record in result.records:
                key = (
                    record.get("source_record_id")
                    or record.get("canonical_url")
                    or record.get("title")
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)

        if records:
            status = "ok"
            error = None
        elif failure_count == len(self.queries):
            status = "error"
            error = f"all_gdelt_batches_failed: {first_error or 'unknown'}"[:200]
        else:
            status = "empty"
            error = None
        partial_failures = (
            failure_count if failure_count < len(self.queries) else 0
        )
        return ProviderResult(
            self.name,
            status,
            records,
            error=error,
            metadata={
                "fetched_at": _utc_z(self.now),
                "timestamp_policy": "seendate_is_provider_seen_not_published",
                "query_count": len(self.queries),
                "query_lengths": [len(query) for query in self.queries],
                "batch_delay_seconds": self.batch_delay_seconds,
                "attempt_count": total_attempts,
                "transient_retries": total_retries,
                "malformed_record_count": total_malformed,
                "partial_failures": partial_failures,
                "batches": batches,
                "result_limit": self.limit,
                "result_limit_reached": any(b.get("result_limit_reached") for b in batches),
            },
            intake_records=intake_records,
        )


class GoogleNewsProvider:
    """Google News RSS discovery fallback with its targeted-query fallback."""

    name = "Google News"
    base_url = (
        "https://news.google.com/rss/search?q={query}"
        "&hl=en-US&gl=US&ceid=US:en"
    )

    def __init__(
        self,
        *,
        primary_query: str = DEFAULT_GOOGLE_PRIMARY_QUERY,
        fallback_queries: Iterable[str] = DEFAULT_GOOGLE_FALLBACK_QUERIES,
        now: Optional[datetime] = None,
        timeout: float = 10,
        request_get: Optional[RequestGet] = None,
        limit: int = 40,
    ):
        self.primary_query = primary_query
        self.fallback_queries = tuple(fallback_queries)
        self.now = now or datetime.now(timezone.utc)
        self.timeout = timeout
        self.request_get = request_get or requests.get
        self.limit = limit

    def fetch_query(self, query: str, limit: Optional[int] = None) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Fetch one Google RSS query without applying a second fallback."""
        try:
            url = self.base_url.format(query=quote(query))
            response = self.request_get(
                url,
                headers={"User-Agent": "StockMarketBot/2.6 headline adapter"},
                timeout=self.timeout,
            )
            status_code = int(getattr(response, "status_code", 0))
            if status_code != 200:
                return [], f"http_status_{status_code}"
            records = _parse_generic_feed(
                _response_bytes(response),
                provider=self.name,
                provider_seen_at=self.now,
                default_publisher=None,
                default_publisher_url=None,
                source_class="aggregated_news_fallback",
                limit=limit or self.limit,
            )
            return records, None
        except Exception as exc:
            return [], str(exc)[:200]

    def fetch(self) -> ProviderResult:
        attempts = []
        records, error = self.fetch_query(self.primary_query, self.limit)
        attempts.append({
            "query": self.primary_query,
            "record_count": len(records),
            "error": error,
        })
        selected_query = self.primary_query
        fallback_used = False
        last_error = error
        if not records:
            for query in self.fallback_queries:
                candidate_records, candidate_error = self.fetch_query(query, self.limit)
                attempts.append({
                    "query": query,
                    "record_count": len(candidate_records),
                    "error": candidate_error,
                })
                if candidate_records:
                    records = candidate_records
                    selected_query = query
                    fallback_used = True
                    last_error = None
                    break
                if candidate_error:
                    last_error = candidate_error
        if records:
            status = "ok"
            result_error = None
        elif last_error:
            status = "error"
            result_error = last_error
        else:
            status = "empty"
            result_error = None
        return ProviderResult(
            self.name,
            status,
            records,
            error=result_error,
            metadata={
                "fetched_at": _utc_z(self.now),
                "primary_query": self.primary_query,
                "query": selected_query,
                "fallback_used": fallback_used,
                "attempts": attempts,
                "result_limit": self.limit,
                "result_limit_reached": any(a.get("record_count", 0) >= self.limit for a in attempts),
            },
        )


__all__ = [
    "ProviderResult",
    "OfficialFeedProvider",
    "FMPNewsProvider",
    "AlphaVantageNewsProvider",
    "GDELTNewsProvider",
    "BatchedGDELTNewsProvider",
    "GoogleNewsProvider",
    "canonicalize_url",
    "normalize_domain",
    "normalize_publisher",
    "classify_source",
    "make_source_record_id",
]
