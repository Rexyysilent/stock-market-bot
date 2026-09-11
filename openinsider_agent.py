"""
OpenInsider Agent - HTML table scraper for insider trading data.

OpenInsider's /rss endpoint currently serves HTML, not valid XML, so the
bot should treat OpenInsider as an HTML table source directly. The primary
parser is pandas.read_html, with a BeautifulSoup fallback for parser issues.
"""
import logging
import os
import random
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from threading import Condition, Lock, RLock
from urllib.parse import urlsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup

from stateutil import atomic_write_json, load_json_state

logger = logging.getLogger("OpenInsiderAgent")


class OpenInsiderAgent:
    """One run-scoped OpenInsider acquisition with locally filtered views.

    A canonical, all-ticker screener request is acquired at most once during an
    instance's lifetime. Multiple consumers may ask for narrower windows and
    limits without issuing another network request. Instances are deliberately
    run-scoped: long-lived applications should create a fresh instance for each
    report rather than carrying an in-memory snapshot into a later run.
    """

    TRUSTED_HOSTS = frozenset({"openinsider.com", "www.openinsider.com"})
    SECURE_BASE_URL = "https://openinsider.com/screener"
    INSECURE_FALLBACK_URL = "http://openinsider.com/screener"
    CACHE_SCHEMA_VERSION = 1
    DEFAULT_CACHE_PATH = os.path.join("state", "openinsider_last_good.json")
    RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)
    _SOURCE_REQUEST_GATE_LOCK = Lock()
    _SOURCE_LAST_REQUEST_STARTED = None

    def __init__(
        self,
        now=None,
        *,
        run_days_back=30,
        run_limit=500,
        session=None,
        sleep=None,
        clock=None,
        jitter=None,
        cache_path=None,
        min_request_interval=1.0,
        request_timeout=15,
        allow_insecure_http_fallback=True,
    ):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self.now_naive_utc = self.now_utc.replace(tzinfo=None)
        self.run_days_back = max(1, int(run_days_back))
        self.run_limit = max(1, int(run_limit))
        self.base_url = self.SECURE_BASE_URL
        self.insecure_fallback_url = self.INSECURE_FALLBACK_URL
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._jitter = jitter or (lambda: random.uniform(0.0, 1.0))
        if cache_path is None:
            self.cache_path = self._default_cache_path_for_scope()
        else:
            self.cache_path = Path(cache_path)
        self.min_request_interval = max(0.0, float(min_request_interval))
        self.request_timeout = request_timeout
        self.allow_insecure_http_fallback = bool(
            allow_insecure_http_fallback
        )

        self._health_lock = RLock()
        self._run_condition = Condition(Lock())
        self._run_state = "not_started"
        self._run_rows = []
        self._run_origin = "none"
        self._run_cache_fetched_at = None
        self.health = self._empty_health()

    def close(self):
        """Close our Session; an injected Session remains caller-owned."""
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()

    @property
    def run_uses_stale_cache(self):
        with self._run_condition:
            return self._run_state == "complete" and self._run_origin == "stale_cache"

    @property
    def run_uses_insecure_http(self):
        with self._run_condition:
            return (
                self._run_state == "complete"
                and self._run_origin == "insecure_http"
            )

    @property
    def has_live_run_data(self):
        with self._run_condition:
            return (
                self._run_state == "complete"
                and self._run_origin == "live"
                and bool(self._run_rows)
            )

    def get_health(self):
        with self._health_lock:
            return deepcopy(self.health)

    @classmethod
    def _trusted_response_url(cls, value, expected_scheme="https"):
        try:
            parsed = urlsplit(str(value))
            port = parsed.port
        except (TypeError, ValueError):
            return False
        expected_port = 443 if expected_scheme == "https" else 80
        return (
            parsed.scheme == expected_scheme
            and parsed.hostname in cls.TRUSTED_HOSTS
            and port in (None, expected_port)
        )

    def _default_cache_path_for_scope(self):
        """Resolve a default cache file unique to the canonical run scope."""
        default_path = Path(self.DEFAULT_CACHE_PATH)
        if self.run_days_back == 30 and self.run_limit == 500:
            return default_path
        return default_path.with_name(
            f"{default_path.stem}_{self.run_days_back}d_"
            f"{self.run_limit}{default_path.suffix}"
        )

    def _empty_health(self):
        return {
            "status": "UNKNOWN",
            "request_status": None,
            "parser": None,
            "fallback_used": False,
            "raw_rows": 0,
            "filtered_rows": 0,
            "latest_filing_date": None,
            "latest_filing_age_days": None,
            "stale": None,
            "warning": None,
            "error": None,
            "failure_reason": None,
            "attempts": 0,
            "retries": 0,
            "retry_delays_seconds": [],
            "transport_scheme": None,
            "transport_secure": None,
            "https_failure_reason": None,
            "https_error": None,
            "http_fallback_attempted": False,
            "http_fallback_used": False,
            "http_fallback_attempts": 0,
            "live": False,
            "run_origin": "none",
            "cache_used": False,
            "cache_status": "not_checked",
            "cache_path": str(self.cache_path),
            "cache_fetched_at": None,
            "cache_age_days": None,
            "cache_error": None,
            "scope": {
                "ticker": "ALL",
                "days_back": self.run_days_back,
                "limit": self.run_limit,
            },
            "ticker": "ALL",
            "days_back": self.run_days_back,
            "limit": self.run_limit,
            "undated_rows": 0,
            "future_rows_dropped": 0,
            "out_of_window_rows_dropped": 0,
        }

    def _default_params(self, ticker="", days_back=30, limit=200):
        return {
            "s": ticker.upper(),
            "o": "",
            "pl": "",
            "ph": "",
            "ll": "",
            "lh": "",
            "fd": str(days_back),
            "fdr": "",
            "td": "0",
            "tdr": "",
            "fdlyl": "",
            "fdlyh": "",
            "daysago": "",
            "xp": "1",
            "xs": "1",
            "vl": "",
            "vh": "",
            "ocl": "",
            "och": "",
            "sic1": "-1",
            "sicl": "100",
            "sich": "9999",
            "isofficer": "1",
            "iscob": "1",
            "isdirector": "1",
            "istenpercent": "1",
            "isother": "1",
            "sortcol": "0",
            "cnt": str(limit),
            "page": "1",
        }

    def get_recent_trades(self, days_back=30, limit=200):
        """Compatibility live-only view over the canonical run snapshot."""
        return self.get_run_trades(
            days_back=days_back, limit=limit, allow_stale=False
        )

    def get_ticker_trades(self, ticker, days_back=None, limit=50):
        """Return a ticker view bounded by this agent's canonical run scope.

        Construct the agent with a wider ``run_days_back`` when needed.
        """
        return self._get_run_view(
            days_back=self.run_days_back if days_back is None else days_back,
            limit=limit,
            allow_stale=False,
            ticker=ticker,
        )

    def _fetch_trades(self, ticker="", days_back=30, limit=200):
        """Compatibility shim; all public views share one canonical request."""
        return self._get_run_view(
            days_back=days_back,
            limit=limit,
            allow_stale=False,
            ticker=ticker,
        )

    def acquire_run_trades(self):
        """Acquire the canonical ALL-ticker snapshot once, even across threads.

        Returns a deep copy. Cache-backed rows carry explicit stale provenance;
        the internal normalized rows and their source timestamps are never
        rewritten.
        """
        with self._run_condition:
            while self._run_state == "acquiring":
                self._run_condition.wait()
            if self._run_state == "complete":
                return self._copy_rows(
                    self._run_rows,
                    stale=self._run_origin == "stale_cache",
                    insecure_http=self._run_origin == "insecure_http",
                    cache_fetched_at=self._run_cache_fetched_at,
                )
            self._run_state = "acquiring"

        try:
            rows, origin = self._acquire_live_or_cache()
            health_snapshot = self.get_health()
        except BaseException:
            with self._run_condition:
                self._run_rows = []
                self._run_origin = "none"
                self._run_cache_fetched_at = None
                self._run_state = "complete"
                self._run_condition.notify_all()
            raise

        with self._run_condition:
            self._run_rows = deepcopy(rows)
            self._run_origin = origin
            self._run_cache_fetched_at = health_snapshot.get("cache_fetched_at")
            self._run_state = "complete"
            self._run_condition.notify_all()
            return self._copy_rows(
                self._run_rows,
                stale=self._run_origin == "stale_cache",
                insecure_http=self._run_origin == "insecure_http",
                cache_fetched_at=self._run_cache_fetched_at,
            )

    def get_run_trades(self, days_back=30, limit=500, allow_stale=True):
        """Return a deep-copied local view of the one run snapshot.

        When the requested window equals the configured canonical window, all
        normalized rows are preserved, including undated, future-dated, or
        server-returned out-of-window rows. Signal consumers can therefore
        account for and reject temporal outliers explicitly. Every noncanonical
        view excludes parseable filing dates outside its requested [cutoff,
        now] interval; undated rows remain visible for consumer diagnostics.
        ``allow_stale=False`` also rejects plaintext narrative-only rows.
        """
        return self._get_run_view(
            days_back=days_back,
            limit=limit,
            allow_stale=allow_stale,
            ticker="",
        )

    def _get_run_view(self, days_back, limit, allow_stale, ticker=""):
        requested_days = max(1, int(days_back))
        requested_limit = max(0, int(limit))
        if requested_days > self.run_days_back:
            raise ValueError(
                f"days_back {requested_days} exceeds canonical "
                f"run_days_back {self.run_days_back}"
            )
        if requested_limit > self.run_limit:
            raise ValueError(
                f"limit {requested_limit} exceeds canonical "
                f"run_limit {self.run_limit}"
            )
        ticker = str(ticker or "").strip().upper()
        self.acquire_run_trades()

        with self._run_condition:
            stale = self._run_origin == "stale_cache"
            insecure_http = self._run_origin == "insecure_http"
            if (stale or insecure_http) and not allow_stale:
                return []
            rows = deepcopy(self._run_rows)
            cache_fetched_at = self._run_cache_fetched_at

        if ticker:
            rows = [
                row for row in rows
                if str(row.get("ticker") or "").upper() == ticker
            ]

        if requested_days != self.run_days_back:
            cutoff = self.now_naive_utc - timedelta(days=requested_days)
            filtered_rows = []
            for row in rows:
                filing_date = self._parse_date(row.get("filing_date"))
                if (
                    filing_date is None
                    or cutoff <= filing_date <= self.now_naive_utc
                ):
                    filtered_rows.append(row)
            rows = filtered_rows

        rows = rows[:requested_limit]
        return self._copy_rows(
            rows,
            stale=stale,
            insecure_http=insecure_http,
            cache_fetched_at=cache_fetched_at,
        )

    def _copy_rows(
        self,
        rows,
        stale=False,
        insecure_http=False,
        cache_fetched_at=None,
    ):
        copied = deepcopy(list(rows or []))
        if stale:
            for row in copied:
                row["source_stale"] = True
                row["source_cache_fetched_at"] = cache_fetched_at
        if insecure_http:
            for row in copied:
                row["link"] = self.insecure_fallback_url
                row["source_transport_scheme"] = "http"
                row["source_transport_secure"] = False
                row["signal_eligible"] = False
        return copied

    def _acquire_live_or_cache(self):
        health = self._empty_health()
        params = self._default_params(
            ticker="",
            days_back=self.run_days_back,
            limit=self.run_limit,
        )
        response = None
        transport_scheme = "https"

        for attempt_index in range(len(self.RETRY_DELAYS_SECONDS) + 1):
            health["attempts"] += 1
            try:
                response = self._session_get_with_gate(
                    self.base_url,
                    params=params,
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
            except requests.exceptions.Timeout as exc:
                health["failure_reason"] = "timeout"
                health["error"] = self._error_text(exc)
            except requests.exceptions.ConnectionError as exc:
                health["failure_reason"] = self._connection_failure_reason(exc)
                health["error"] = self._error_text(exc)
            except Exception as exc:
                health["failure_reason"] = "unexpected_error"
                health["error"] = self._error_text(exc)
                break
            else:
                break

            if attempt_index >= len(self.RETRY_DELAYS_SECONDS):
                break
            delay = self._retry_delay(attempt_index)
            health["retries"] += 1
            health["retry_delays_seconds"].append(delay)
            logger.warning(
                "OpenInsider %s; retrying in %.3fs (attempt %s/%s)",
                health["failure_reason"],
                delay,
                attempt_index + 1,
                len(self.RETRY_DELAYS_SECONDS) + 1,
            )
            self._sleep(delay)

        if (
            response is None
            and health.get("failure_reason") == "connection_refused"
            and self.allow_insecure_http_fallback
        ):
            health["https_failure_reason"] = health.get("failure_reason")
            health["https_error"] = health.get("error")
            health["http_fallback_attempted"] = True
            health["http_fallback_attempts"] = 1
            health["attempts"] += 1
            health["transport_scheme"] = "http"
            health["transport_secure"] = False
            try:
                response = self._session_get_with_gate(
                    self.insecure_fallback_url,
                    params=params,
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
            except requests.exceptions.Timeout as exc:
                health["failure_reason"] = "http_fallback_timeout"
                health["error"] = self._error_text(exc)
            except requests.exceptions.ConnectionError as exc:
                reason = self._connection_failure_reason(exc)
                health["failure_reason"] = f"http_fallback_{reason}"
                health["error"] = self._error_text(exc)
            except Exception as exc:
                health["failure_reason"] = "http_fallback_unexpected_error"
                health["error"] = self._error_text(exc)
            else:
                transport_scheme = "http"

        if response is None:
            health["status"] = "ERROR"
            logger.error(
                "OpenInsider request failed after %s attempt(s): %s",
                health["attempts"],
                health["error"],
            )
            return self._failure_with_cache(health)

        if transport_scheme == "https":
            health["transport_scheme"] = "https"
            health["transport_secure"] = True
        health["request_status"] = getattr(response, "status_code", None)
        final_url = getattr(response, "url", "")
        if not self._trusted_response_url(
            final_url, expected_scheme=transport_scheme
        ):
            health["status"] = "ERROR"
            health["failure_reason"] = (
                "http_fallback_untrusted_response_url"
                if transport_scheme == "http"
                else "untrusted_response_url"
            )
            health["error"] = "Untrusted OpenInsider response URL"
            logger.warning(
                "OpenInsider rejected untrusted response URL: %r", final_url
            )
            return self._failure_with_cache(health)

        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            health["status"] = "ERROR"
            reason = self._http_failure_reason(status_code)
            health["failure_reason"] = (
                f"http_fallback_{reason}"
                if transport_scheme == "http"
                else reason
            )
            health["error"] = f"HTTP {status_code}"
            logger.warning("OpenInsider returned %s", status_code)
            return self._failure_with_cache(health)

        try:
            trades = self._parse_tables_with_pandas(response.text)
            health["parser"] = "pandas" if trades else None
            if not trades:
                logger.info(
                    "OpenInsider pandas parser found no rows; "
                    "trying BeautifulSoup fallback"
                )
                trades = self._parse_tables_with_bs4(response.text)
                health["parser"] = "beautifulsoup" if trades else None
                health["fallback_used"] = True
        except Exception as exc:
            trades = []
            health["error"] = self._error_text(exc)

        if not trades:
            health["status"] = "ERROR"
            health["failure_reason"] = (
                "http_fallback_parser_failed"
                if transport_scheme == "http"
                else "parser_failed"
            )
            health["error"] = (
                health["error"]
                or "OpenInsider response contained no parseable trade rows"
            )
            return self._failure_with_cache(health)

        health["raw_rows"] = len(trades)
        rows = deepcopy(trades[:self.run_limit])
        health["filtered_rows"] = len(rows)
        health["undated_rows"] = sum(
            1 for row in rows
            if self._parse_date(row.get("filing_date")) is None
        )
        latest = self._latest_filing_date(rows)
        if latest:
            age_days = (
                self.now_naive_utc - latest
            ).total_seconds() / 86400
            health["latest_filing_date"] = latest.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            health["latest_filing_age_days"] = round(age_days, 1)

        if transport_scheme == "http":
            warning = (
                "HTTPS connection was refused; using unauthenticated HTTP "
                "OpenInsider rows for narrative context only. HTTP rows are "
                "excluded from clusters, signals, confluence, state, and "
                "the ledger; SEC EDGAR remains the cluster fallback."
            )
            health.update({
                "status": "WARN",
                "live": False,
                "run_origin": "insecure_http",
                "stale": False,
                "warning": warning,
                "error": None,
                "failure_reason": (
                    health.get("https_failure_reason")
                    or "connection_refused"
                ),
                "transport_scheme": "http",
                "transport_secure": False,
                "http_fallback_used": True,
                "cache_status": "not_written_insecure_transport",
            })
            self._replace_health(health)
            logger.warning(
                "OpenInsider HTTP fallback parsed %s narrative-only rows",
                len(rows),
            )
            return rows, "insecure_http"

        health.update({
            "status": "OK",
            "live": True,
            "run_origin": "live",
            "stale": False,
            "warning": None,
            "error": None,
            "failure_reason": None,
            "transport_scheme": "https",
            "transport_secure": True,
        })
        self._save_cache(rows, health)
        self._replace_health(health)
        logger.info(
            "OpenInsider canonical snapshot parsed %s rows", len(rows)
        )
        return rows, "live"

    def _failure_with_cache(self, health):
        rows = self._load_cache(health)
        if rows:
            health.update({
                "live": False,
                "run_origin": "stale_cache",
                "cache_used": True,
                "stale": True,
                "warning": (
                    "Using stale cached OpenInsider rows from "
                    f"{health.get('cache_fetched_at')} after "
                    f"{health.get('failure_reason')}."
                ),
            })
            origin = "stale_cache"
        else:
            health.update({
                "live": False,
                "run_origin": "none",
                "cache_used": False,
            })
            origin = "none"
        self._replace_health(health)
        return rows, origin

    def _save_cache(self, rows, health):
        payload = {
            "schema_version": self.CACHE_SCHEMA_VERSION,
            "source": "OpenInsider",
            "source_url": self.base_url,
            "fetched_at": self.now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scope": deepcopy(health["scope"]),
            "rows": deepcopy(rows),
        }
        try:
            atomic_write_json(self.cache_path, payload, indent=1)
        except Exception as exc:
            health["cache_status"] = "write_error"
            health["cache_error"] = self._error_text(exc)
            logger.warning("OpenInsider cache write failed: %s", exc)
        else:
            health["cache_status"] = "saved"
            health["cache_fetched_at"] = payload["fetched_at"]
            health["cache_age_days"] = 0.0

    def _load_cache(self, health):
        try:
            if not self.cache_path.exists():
                health["cache_status"] = "missing"
                return []
            payload = load_json_state(self.cache_path, {})
        except Exception as exc:
            health["cache_status"] = "read_error"
            health["cache_error"] = self._error_text(exc)
            return []

        if not isinstance(payload, dict):
            health["cache_status"] = "invalid"
            health["cache_error"] = "cache root is not an object"
            return []
        if payload.get("schema_version") != self.CACHE_SCHEMA_VERSION:
            health["cache_status"] = "invalid"
            health["cache_error"] = "unsupported cache schema"
            return []
        if payload.get("source") != "OpenInsider":
            health["cache_status"] = "invalid"
            health["cache_error"] = "cached source identity is invalid"
            return []
        if payload.get("scope") != health["scope"]:
            health["cache_status"] = "scope_mismatch"
            health["cache_error"] = "cached acquisition scope does not match run"
            return []
        if not self._trusted_response_url(payload.get("source_url")):
            health["cache_status"] = "invalid"
            health["cache_error"] = "cached source URL is untrusted"
            return []

        rows = payload.get("rows")
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            health["cache_status"] = "invalid"
            health["cache_error"] = "cached rows are not a list of objects"
            return []
        if not rows:
            health["cache_status"] = "expired"
            health["cache_error"] = "cached snapshot contains no rows"
            return []

        fetched_at = payload.get("fetched_at")
        fetched_dt = self._parse_cache_timestamp(fetched_at)
        if fetched_dt is None:
            health["cache_status"] = "invalid"
            health["cache_error"] = "cached fetched_at is invalid"
            return []
        age_days = (
            self.now_utc - fetched_dt
        ).total_seconds() / 86400
        if age_days < -1 / 24:
            health["cache_status"] = "invalid"
            health["cache_error"] = "cached fetched_at is in the future"
            return []

        health["cache_status"] = "hit"
        health["cache_fetched_at"] = fetched_dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        health["cache_age_days"] = round(max(0.0, age_days), 1)
        return deepcopy(rows[:self.run_limit])

    def _replace_health(self, health):
        with self._health_lock:
            self.health = deepcopy(health)

    def _session_get_with_gate(self, url, **kwargs):
        """Space and serialize physical OpenInsider requests process-wide."""
        with OpenInsiderAgent._SOURCE_REQUEST_GATE_LOCK:
            self._wait_for_request_slot_locked()
            # Redirect targets must be evaluated before any follow-up request.
            # Requests otherwise follows Location automatically, bypassing the
            # final response URL guard for the first off-host hop.
            kwargs["allow_redirects"] = False
            return self.session.get(url, **kwargs)

    def _wait_for_request_slot(self):
        """Reserve a process-wide request slot without performing I/O."""
        with OpenInsiderAgent._SOURCE_REQUEST_GATE_LOCK:
            self._wait_for_request_slot_locked()

    def _wait_for_request_slot_locked(self):
        now = float(self._clock())
        last_started = OpenInsiderAgent._SOURCE_LAST_REQUEST_STARTED
        if last_started is not None:
            remaining = self.min_request_interval - (now - last_started)
            if remaining > 0:
                self._sleep(remaining)
                after_sleep = float(self._clock())
                now = max(
                    after_sleep,
                    last_started + self.min_request_interval,
                )
        OpenInsiderAgent._SOURCE_LAST_REQUEST_STARTED = now

    @classmethod
    def _reset_source_request_gate_for_tests(cls):
        """Reset process-global timing state for deterministic isolated tests."""
        with cls._SOURCE_REQUEST_GATE_LOCK:
            cls._SOURCE_LAST_REQUEST_STARTED = None

    def _retry_delay(self, attempt_index):
        base = self.RETRY_DELAYS_SECONDS[attempt_index]
        try:
            try:
                jitter = float(self._jitter(base))
            except TypeError:
                jitter = float(self._jitter())
        except (TypeError, ValueError):
            jitter = 0.0
        return round(max(0.0, base + max(0.0, jitter)), 3)

    @staticmethod
    def _http_failure_reason(status_code):
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return "http_unknown"
        return f"http_{code}"

    @classmethod
    def _connection_failure_reason(cls, exc):
        text = cls._error_text(exc).lower()
        if (
            "winerror 10061" in text
            or "wsaeconnrefused" in text
            or "actively refused" in text
            or "connection refused" in text
        ):
            return "connection_refused"
        return "connection_error"

    @staticmethod
    def _error_text(exc):
        text = f"{type(exc).__name__}: {exc}"
        return text[:800]

    @staticmethod
    def _parse_cache_timestamp(value):
        try:
            parsed = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    def _parse_tables_with_pandas(self, html):
        try:
            tables = pd.read_html(StringIO(html))
        except Exception as exc:
            logger.warning(f"OpenInsider pandas.read_html failed: {exc}")
            return []

        trades = []
        for df in tables:
            columns = [self._normalize_header(c) for c in df.columns]
            if "ticker" not in columns:
                continue

            df = df.copy()
            df.columns = columns
            for _, row in df.iterrows():
                row_data = {col: row.get(col, "") for col in columns}
                trade = self._normalize_row(row_data)
                if trade:
                    trades.append(trade)
            if trades:
                break
        return trades

    def _parse_tables_with_bs4(self, html):
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="tinytable")
        if not table:
            logger.warning("OpenInsider: no tinytable found in response")
            return []

        header_row = table.find("tr")
        if not header_row:
            logger.warning("OpenInsider: no header row found")
            return []

        headers = [
            self._normalize_header(cell.get_text(strip=True))
            for cell in header_row.find_all(["th", "td"])
        ]

        trades = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < len(headers):
                continue
            row_data = {}
            for index, header in enumerate(headers):
                row_data[header] = cells[index].get_text(strip=True)
            trade = self._normalize_row(row_data)
            if trade:
                trades.append(trade)
        return trades

    def format_for_whispers(
        self,
        days_back=7,
        limit=5,
        max_stale_days=3,
        allow_stale=True,
    ):
        """Format the run snapshot for social context with honest provenance."""
        trades = self.get_run_trades(
            days_back=days_back,
            limit=limit,
            allow_stale=allow_stale,
        )
        items = []

        warning = self._freshness_warning(
            trades, max_stale_days=max_stale_days
        )
        if warning:
            items.append(warning)

        for trade in trades:
            ticker = trade.get("ticker") or "?"
            trade_type = trade.get("trade_type") or "Trade"
            value = trade.get("value") or "?"
            insider = trade.get("insider_name") or "Unknown insider"
            company = trade.get("company_name") or ""
            filing_date = trade.get("filing_date") or "Unknown date"
            source_label = (
                "OpenInsider/STALE"
                if trade.get("source_stale")
                else "OpenInsider/HTTP-INSECURE"
                if trade.get("source_transport_secure") is False
                else "OpenInsider"
            )
            items.append(
                f"[{source_label}] {ticker}: {trade_type} {value} by {insider} "
                f"{company} (Filed: {filing_date})"
            )
        return items

    def _freshness_warning(self, trades, max_stale_days=3):
        uses_stale_cache = self.run_uses_stale_cache
        uses_insecure_http = self.run_uses_insecure_http
        with self._health_lock:
            if uses_insecure_http:
                warning = self.health.get("warning") or (
                    "Using unauthenticated HTTP OpenInsider rows for "
                    "narrative context only."
                )
                self.health.update({
                    "status": "WARN",
                    "stale": False,
                    "warning": warning,
                    "max_stale_days": max_stale_days,
                })
                return f"[OpenInsider/WARN] {warning}"

            if uses_stale_cache:
                warning = self.health.get("warning") or (
                    "Using stale cached OpenInsider rows because live "
                    "acquisition failed."
                )
                self.health.update({
                    "stale": True,
                    "warning": warning,
                    "max_stale_days": max_stale_days,
                })
                return f"[OpenInsider/WARN] {warning}"

            if not trades:
                logger.warning(
                    "OpenInsider returned no rows for freshness check"
                )
                warning = (
                    "No OpenInsider rows returned; source may be down or stale."
                )
                update = {
                    "stale": True,
                    "warning": warning,
                    "max_stale_days": max_stale_days,
                }
                if self.health.get("status") != "ERROR":
                    update["status"] = "WARN"
                self.health.update(update)
                return f"[OpenInsider/WARN] {warning}"

            latest = self._latest_filing_date(trades)
            if latest is None:
                logger.warning(
                    "OpenInsider rows had no parseable filing dates"
                )
                warning = (
                    "Could not parse OpenInsider filing dates; "
                    "freshness unknown."
                )
                update = {
                    "stale": None,
                    "warning": warning,
                    "max_stale_days": max_stale_days,
                }
                if self.health.get("status") != "ERROR":
                    update["status"] = "WARN"
                self.health.update(update)
                return f"[OpenInsider/WARN] {warning}"

            age_days = (
                self.now_naive_utc - latest
            ).total_seconds() / 86400
            latest_text = latest.strftime("%Y-%m-%d %H:%M:%S")
            self.health.update({
                "latest_filing_date": latest_text,
                "latest_filing_age_days": round(age_days, 1),
                "max_stale_days": max_stale_days,
                "stale": age_days > max_stale_days,
            })
            if age_days > max_stale_days:
                logger.warning(
                    "OpenInsider latest filing is stale: %s "
                    "(%.1f calendar days old)",
                    latest_text,
                    age_days,
                )
                warning = (
                    f"Latest OpenInsider filing is {latest_text} "
                    f"({age_days:.1f} calendar days old); source may be stale."
                )
                update = {"warning": warning}
                if self.health.get("status") != "ERROR":
                    update["status"] = "WARN"
                self.health.update(update)
                return f"[OpenInsider/WARN] {warning}"

            if self.health.get("status") != "ERROR":
                self.health.update({
                    "status": "OK",
                    "warning": None,
                })
            return None

    def _latest_filing_date(self, trades):
        latest = None
        for trade in trades:
            filing_date = self._parse_date(trade.get("filing_date"))
            if filing_date and (latest is None or filing_date > latest):
                latest = filing_date
        return latest

    def format_for_export(self, days_back=30, limit=50):
        """Format trades as structured data for the daily export."""
        trades = self.get_recent_trades(days_back=days_back, limit=limit)
        export = []
        for trade in trades:
            export.append({
                "ticker": trade.get("ticker", ""),
                "filing_date": trade.get("filing_date", ""),
                "trade_date": trade.get("trade_date", ""),
                "insider": trade.get("insider_name", ""),
                "title": trade.get("title", ""),
                "trade_type": trade.get("trade_type", ""),
                "price": trade.get("price", ""),
                "qty": trade.get("qty", ""),
                "value": trade.get("value", ""),
                "delta_own": trade.get("delta_own", ""),
                "source": "OpenInsider",
            })
        return export

    def _normalize_row(self, row_data):
        """Normalize a row dict from the HTML table into our standard format."""
        ticker = self._clean(row_data.get("ticker"))
        if not ticker or ticker.lower() == "ticker":
            return None

        return {
            "ticker": ticker.upper(),
            "filing_date": self._clean(row_data.get("filing date")),
            "trade_date": self._clean(row_data.get("trade date")),
            "company_name": self._clean(row_data.get("company name")),
            "insider_name": self._clean(row_data.get("insider name")),
            "title": self._clean(row_data.get("title")),
            "trade_type": self._clean(row_data.get("trade type")),
            "price": self._clean(row_data.get("price")),
            "qty": self._clean(row_data.get("qty")),
            "owned": self._clean(row_data.get("owned")),
            "delta_own": self._clean(row_data.get("delta own", row_data.get("down", ""))),
            "value": self._clean(row_data.get("value")),
            "source": "OpenInsider",
            "link": self.base_url,
        }

    def _normalize_header(self, value):
        text = self._clean(value).replace("\xa0", " ").replace("%", "")
        text = " ".join(text.split()).lower()
        if text in ("delta own", "deltaown", "down", "delta own"):
            return "delta own"
        return text

    def _clean(self, value):
        """Clean a cell value - handle None, NaN, and whitespace."""
        if value is None:
            return ""
        s = str(value).strip()
        if s.lower() in ("nan", "none", ""):
            return ""
        return s

    def _parse_date(self, value):
        """Parse a date string in various OpenInsider formats."""
        if not value:
            return None
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None
