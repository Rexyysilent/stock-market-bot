"""
SEC EDGAR Agent — Fetches SEC filings for watchlist tickers.
Uses the free EDGAR submissions API (no API key needed).
- 8-K filings (material events)
- Form 4 (insider buys/sells)
- 13F (institutional holdings)

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
import requests
import logging
import re
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock
from config import SEC_USER_AGENT, SIGNAL_ELIGIBLE_TICKERS
from openinsider_agent import OpenInsiderAgent
from timeutil import to_utc_z, newest

# Compatibility seam for focused tests; acquisition remains the core 29 only.
ALL_TICKERS = list(SIGNAL_ELIGIBLE_TICKERS)

logger = logging.getLogger("SECAgent")

# Form 4 "Code" column, verbatim from the filing: P open-market purchase,
# S open-market sale, A award/grant, M option exercise, F tax withholding,
# G gift. code_counts records whatever codes appear — no interpretation.
FORM4_CODE_RE = re.compile(r"<transactionCode>\s*([A-Za-z0-9])\s*</transactionCode>")
# Reporting owner identity — a cluster is distinct filers, not distinct documents
FORM4_OWNER_RE = re.compile(r"<rptOwnerCik>\s*0*(\d+)\s*</rptOwnerCik>")
# OpenInsider's Trade Type column prefixes the same code: "P - Purchase", "S - Sale+OE"
OPENINSIDER_CODE_RE = re.compile(r"^\s*([A-Za-z])\s*[-–]")


class SECAgent:
    def __init__(self, now=None, openinsider=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self.headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json"
        }
        # Company ticker -> {"cik", "title"} mapping cache
        self._cik_cache = {}
        # CIK -> trimmed submissions JSON (shared by watchlist scan + insider fallback)
        self._submissions_cache = {}
        self._cache_lock = Lock()
        self.openinsider = (
            openinsider
            if openinsider is not None
            else OpenInsiderAgent(now=self.now_utc)
        )
        self._health_lock = Lock()
        self.health = self._empty_health()

    def _empty_health(self):
        return {
            "requests": 0,
            "retries": 0,
            "rate_limits": 0,
            "transient_errors": 0,
            "exceptions": 0,
            "status_counts": {},
            "failed_requests": [],
            "failed_requests_omitted": 0,
            "no_cik_tickers": [],
            "insider_cluster_source": "unknown",
            "insider_cluster_fallback_used": None,
            "openinsider_cluster_count": None,
            "insider_cluster_count": None,
            "form4_code_fetch_failures": 0,
            "openinsider_cluster_rows_considered": 0,
            "openinsider_cluster_rows_eligible": 0,
            "openinsider_cluster_rows_dropped": {
                "not_mapping": 0,
                "stale": 0,
                "insecure_transport": 0,
                "off_watchlist": 0,
                "undated": 0,
                "future": 0,
                "out_of_window": 0,
            },
            "insider_cluster_fallback_reason": None,
        }

    def _bump_health(self, key, amount=1):
        with self._health_lock:
            self.health[key] = self.health.get(key, 0) + amount

    def _record_status(self, status_code):
        status_key = str(status_code)
        with self._health_lock:
            counts = self.health.setdefault("status_counts", {})
            counts[status_key] = counts.get(status_key, 0) + 1

    def _record_failed_request(self, ticker, form_type, status, detail=None):
        entry = {
            "ticker": ticker,
            "form_type": form_type,
            "status": str(status),
        }
        if detail:
            entry["detail"] = str(detail)[:180]

        with self._health_lock:
            failed = self.health.setdefault("failed_requests", [])
            if len(failed) < 30:
                failed.append(entry)
            else:
                self.health["failed_requests_omitted"] = (
                    self.health.get("failed_requests_omitted", 0) + 1
                )

    def _record_no_cik(self, ticker):
        """Track tickers with no SEC registrant (ETFs, indexes, futures, foreign OTC).

        Kept separate from failed_requests: these are expected non-filers,
        not EDGAR errors. Returns True the first time a ticker is recorded.
        """
        with self._health_lock:
            no_cik = self.health.setdefault("no_cik_tickers", [])
            if ticker in no_cik:
                return False
            no_cik.append(ticker)
            return True

    def _set_health_value(self, key, value):
        with self._health_lock:
            self.health[key] = value

    def get_health(self):
        with self._health_lock:
            return deepcopy(self.health)

    def _get_with_retries(self, url, params=None, timeout=15, max_attempts=3):
        retry_statuses = {429, 500, 502, 503, 504}
        last_response = None

        for attempt in range(1, max_attempts + 1):
            try:
                self._bump_health("requests")
                response = requests.get(url, params=params, headers=self.headers, timeout=timeout)
                last_response = response
                self._record_status(response.status_code)
                if response.status_code in retry_statuses and attempt < max_attempts:
                    wait_seconds = 2 ** (attempt - 1)
                    self._bump_health("retries")
                    self._bump_health("transient_errors")
                    if response.status_code == 429:
                        self._bump_health("rate_limits")
                    logger.warning(
                        f"SEC EDGAR returned {response.status_code}; retrying in {wait_seconds}s "
                        f"(attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(wait_seconds)
                    continue
                return response
            except requests.exceptions.RequestException as exc:
                self._bump_health("exceptions")
                if attempt >= max_attempts:
                    logger.error(f"SEC EDGAR request failed after {max_attempts} attempts: {exc}")
                    return last_response
                self._bump_health("retries")
                wait_seconds = 2 ** (attempt - 1)
                logger.warning(
                    f"SEC EDGAR request error; retrying in {wait_seconds}s "
                    f"(attempt {attempt}/{max_attempts}): {exc}"
                )
                time.sleep(wait_seconds)

        return last_response

    def _load_cik_map(self):
        """Load the SEC's full ticker -> CIK mapping (one request covers all tickers)."""
        with self._cache_lock:
            if self._cik_cache:
                return self._cik_cache
        resp = self._get_with_retries("https://www.sec.gov/files/company_tickers.json", timeout=15)
        if resp and resp.status_code == 200:
            try:
                mapping = {}
                for entry in resp.json().values():
                    ticker = str(entry.get("ticker", "")).upper()
                    if ticker:
                        mapping[ticker] = {
                            "cik": int(entry["cik_str"]),
                            "title": entry.get("title", ticker),
                        }
                with self._cache_lock:
                    if not self._cik_cache:
                        self._cik_cache = mapping
            except Exception as e:
                logger.error(f"Failed to parse company_tickers.json: {e}")
        return self._cik_cache

    def _get_cik(self, ticker):
        """Lookup CIK for a ticker. Returns {"cik": int, "title": str} or None."""
        return self._load_cik_map().get(ticker.upper())

    def _get_submissions(self, cik):
        """Fetch (and cache) the EDGAR submissions JSON for a CIK."""
        with self._cache_lock:
            cached = self._submissions_cache.get(cik)
        if cached is not None:
            return cached
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        resp = self._get_with_retries(url, timeout=15)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                trimmed = {
                    "name": data.get("name", ""),
                    "recent": data.get("filings", {}).get("recent", {}),
                }
                with self._cache_lock:
                    self._submissions_cache[cik] = trimmed
                return trimmed
            except Exception as e:
                logger.error(f"Failed to parse submissions JSON for CIK {cik}: {e}")
        return None

    def get_recent_filings(self, ticker, form_types=None, days_back=7, limit=5):
        """
        Fetch recent SEC filings for a ticker via the EDGAR submissions API.

        Each record carries its accession number, acceptance datetime and a
        direct link to the primary filing document (not the company search page).

        Args:
            ticker: Stock ticker (e.g., "TSLA")
            form_types: List of form types to filter (e.g., ["8-K", "4"])
            days_back: How many days back to search
            limit: Max results per form type
        """
        if form_types is None:
            form_types = ["8-K", "4", "10-Q", "10-K"]

        company = self._get_cik(ticker)
        if not company:
            if self._record_no_cik(ticker):
                logger.info(f"No CIK found for {ticker}; not an SEC registrant, skipping EDGAR scan")
            return []

        cik = company["cik"]
        submissions = self._get_submissions(cik)
        if not submissions:
            logger.warning(f"SEC EDGAR returned no submissions for {ticker} (CIK {cik}); continuing")
            self._record_failed_request(ticker, ",".join(form_types), "no_response")
            return []

        recent = submissions["recent"]
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        acceptance_times = recent.get("acceptanceDateTime", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])

        start_date = (
            self.now_utc - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")
        per_form_counts = {ft: 0 for ft in form_types}
        filings = []

        for i, form in enumerate(forms):
            if form not in per_form_counts or per_form_counts[form] >= limit:
                continue
            filing_date = filing_dates[i] if i < len(filing_dates) else ""
            if not filing_date or filing_date < start_date:
                continue

            accession = accessions[i] if i < len(accessions) else ""
            accession_nodash = accession.replace("-", "")
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            if primary_doc:
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{primary_doc}"
            else:
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession}-index.htm"
            description = (primary_descs[i] if i < len(primary_descs) else "") or form

            per_form_counts[form] += 1
            filings.append({
                "ticker": ticker,
                "form_type": form,
                "title": submissions.get("name") or ticker,
                "description": description,
                "date": filing_date,
                "filed_at": (acceptance_times[i] if i < len(acceptance_times) else None) or None,
                "accession_number": accession,
                "primary_doc_url": doc_url,
                "link": doc_url,
            })

        return filings

    def get_insider_trades(self, ticker, days_back=14):
        """Fetch Form 4 (insider trading) filings for a ticker."""
        return self.get_recent_filings(ticker, form_types=["4"], days_back=days_back, limit=10)

    def get_material_events(self, ticker, days_back=30):
        """Fetch 8-K (material event) filings for a ticker."""
        return self.get_recent_filings(ticker, form_types=["8-K"], days_back=days_back, limit=5)

    def scan_all_watchlist(self, days_back=7):
        """Scan all watchlist tickers for recent important filings (deduped on accession number)."""
        all_filings = []
        seen_accessions = set()
        for ticker in ALL_TICKERS:
            filings = self.get_recent_filings(
                ticker,
                form_types=["8-K", "4"],
                days_back=days_back,
                limit=3
            )
            for filing in filings:
                accession = filing.get("accession_number")
                if accession and accession in seen_accessions:
                    continue
                if accession:
                    seen_accessions.add(accession)
                all_filings.append(filing)

        logger.info(f"Scanned {len(ALL_TICKERS)} tickers, found {len(all_filings)} filings")
        return all_filings

    def format_filings(self, filings):
        """Format filings for Discord embed display."""
        lines = []
        for f in filings:
            emoji = "📋" if f["form_type"] == "8-K" else "👤" if f["form_type"] == "4" else "📄"
            lines.append(f"{emoji} **[{f['ticker']}]** {f['form_type']} — {f['description'][:80]}")
            lines.append(f"   Filed: {f['date']} | [View]({f['link']})")
        return "\n".join(lines)

    def detect_insider_clusters(self, days_back=30):
        """
        Detect insider clusters — >=2 DISTINCT filers transacting the same
        open-market direction (P or S) on one ticker inside the window.
        One insider filing many tranches is not a cluster. Identity comes from
        rptOwnerCik (EDGAR Form 4 XML) or the insider name (OpenInsider).

        PURCHASE clusters are the headline signal (Cohen-Malloy-Pomorski):
        multiple insiders buying open-market together is a strong positive
        configured predictor. SALE clusters are kept
        as LOW-level context only: sales are contaminated by diversification,
        liquidity needs, and scheduled 10b5-1 plans, and carry weak predictive
        content. Results are ordered buy > mixed > sell.

        insider_count = distinct filers in the cluster direction; filing_count
        = documents; code_counts = transaction codes as filed.
        Tries OpenInsider's parsed trade table first, then falls back to SEC EDGAR.
        Returns list of {ticker, insider_count, buyers, sellers, filing_count,
        code_counts, cluster_direction, source, filings, alert_level}
        """
        openinsider_clusters = self._detect_openinsider_clusters(days_back=days_back)
        if openinsider_clusters:
            logger.info(
                f"Insider cluster scan: {len(openinsider_clusters)} tickers via OpenInsider"
            )
            self._set_health_value("insider_cluster_source", "openinsider")
            self._set_health_value("insider_cluster_fallback_used", False)
            self._set_health_value("insider_cluster_fallback_reason", None)
            self._set_health_value("openinsider_cluster_count", len(openinsider_clusters))
            self._set_health_value("insider_cluster_count", len(openinsider_clusters))
            return openinsider_clusters

        fallback_reason = self.get_health().get("insider_cluster_fallback_reason")
        if not fallback_reason:
            fallback_reason = "no_qualifying_clusters"
            self._set_health_value("insider_cluster_fallback_reason", fallback_reason)
        logger.warning(
            "OpenInsider produced no usable clusters (%s); falling back to SEC EDGAR Form 4 scan",
            fallback_reason,
        )
        self._set_health_value("insider_cluster_source", "edgar_fallback")
        self._set_health_value("insider_cluster_fallback_used", True)
        self._set_health_value("openinsider_cluster_count", 0)
        clusters = []
        
        for ticker in ALL_TICKERS:
            try:
                # Fetch Form 4 filings (insider trades)
                filings = self.get_recent_filings(
                    ticker, 
                    form_types=["4"], 
                    days_back=days_back, 
                    limit=10
                )
                
                if len(filings) < 2:
                    continue

                filing_dates = [f["date"] for f in filings if f.get("date")]
                unique_dates = set(filing_dates)

                # A cluster needs distinct filers, not filing volume: one CFO
                # exercising options in ten tranches is ten documents, one human.
                code_counts = Counter()
                owner_codes = {}
                for filing in filings:
                    details = self._fetch_form4_details(filing)
                    if details is None:
                        self._bump_health("form4_code_fetch_failures")
                        continue
                    codes = [code.upper() for code in details["codes"]]
                    code_counts.update(codes)
                    for owner in details["owners"]:
                        owner_codes.setdefault(owner, set()).update(codes)

                buyers = sum(1 for codes in owner_codes.values() if "P" in codes)
                sellers = sum(1 for codes in owner_codes.values() if "S" in codes)
                cluster = self._build_cluster(
                    ticker, buyers, sellers, len(filings), owner_codes,
                    code_counts, days_back, "edgar_fallback",
                )
                if not cluster:
                    continue
                cluster["unique_dates"] = len(unique_dates)
                cluster["filings"] = filings[:5]  # Top 5 for display
                # the cluster became true when its newest filing landed
                cluster["as_of"] = newest(
                    to_utc_z(f.get("filed_at") or f.get("date")) for f in filings
                )
                clusters.append(cluster)
                
            except Exception as e:
                logger.error(f"Error detecting insider clusters for {ticker}: {e}")
                continue
        
        self._sort_clusters(clusters)
        logger.info(f"Insider cluster scan: {len(clusters)} tickers with filer clusters")
        self._set_health_value("insider_cluster_count", len(clusters))
        return clusters


    def _openinsider_source_health(self):
        get_health = getattr(self.openinsider, "get_health", None)
        if not callable(get_health):
            return {}
        try:
            health = get_health()
        except Exception as exc:
            logger.warning("Could not read OpenInsider health: %s", exc)
            return {}
        return health if isinstance(health, dict) else {}

    @staticmethod
    def _empty_openinsider_cluster_drops():
        return {
            "not_mapping": 0,
            "stale": 0,
            "insecure_transport": 0,
            "off_watchlist": 0,
            "undated": 0,
            "future": 0,
            "out_of_window": 0,
        }

    def _openinsider_empty_reason(self, source_health):
        if (
            getattr(self.openinsider, "run_uses_stale_cache", False)
            or (
                source_health.get("cache_used")
                and source_health.get("cache_stale")
            )
        ):
            return "stale_cache_disallowed"
        if (
            getattr(self.openinsider, "run_uses_insecure_http", False)
            or source_health.get("run_origin") == "insecure_http"
            or source_health.get("http_fallback_used") is True
        ):
            return "insecure_http_disallowed"
        acquisition_status = str(
            source_health.get("acquisition_status") or ""
        ).strip().lower()
        if (
            source_health.get("failure_reason")
            or source_health.get("error")
            or acquisition_status in {"error", "failed", "unavailable"}
        ):
            return "source_unavailable"
        return "no_qualifying_clusters"

    def _openinsider_source_is_stale(self, source_health):
        return bool(
            getattr(self.openinsider, "run_uses_stale_cache", False)
            or source_health.get("run_origin") == "stale_cache"
            or (
                source_health.get("cache_used")
                and source_health.get("live") is not True
            )
        )

    def _openinsider_source_is_insecure(self, source_health):
        return bool(
            getattr(self.openinsider, "run_uses_insecure_http", False)
            or source_health.get("run_origin") == "insecure_http"
            or source_health.get("transport_secure") is False
            or source_health.get("http_fallback_used") is True
        )

    def _detect_openinsider_clusters(self, days_back=30):
        drop_counts = self._empty_openinsider_cluster_drops()
        self._set_health_value("insider_cluster_fallback_reason", None)
        try:
            trades = self.openinsider.get_run_trades(
                days_back=days_back,
                limit=500,
                allow_stale=False,
            )
        except Exception as exc:
            logger.error("OpenInsider canonical run view failed: %s", exc)
            trades = []
            self._set_health_value(
                "insider_cluster_fallback_reason", "source_unavailable"
            )

        trades = trades if isinstance(trades, list) else []
        self._set_health_value(
            "openinsider_cluster_rows_considered", len(trades)
        )
        self._set_health_value("openinsider_cluster_rows_eligible", 0)
        self._set_health_value(
            "openinsider_cluster_rows_dropped", drop_counts
        )
        source_health = self._openinsider_source_health()
        if not trades:
            if self.get_health().get("insider_cluster_fallback_reason") is None:
                self._set_health_value(
                    "insider_cluster_fallback_reason",
                    self._openinsider_empty_reason(
                        source_health
                    ),
                )
            return []

        # Fail closed even if a future adapter violates allow_stale=False and
        # returns cache-backed or cleartext rows. Source-level and per-row
        # provenance are checked before any constituent can enter a cluster.
        if self._openinsider_source_is_stale(source_health):
            drop_counts["stale"] = len(trades)
            self._set_health_value(
                "openinsider_cluster_rows_dropped", drop_counts
            )
            self._set_health_value(
                "insider_cluster_fallback_reason", "stale_cache_disallowed"
            )
            return []

        if self._openinsider_source_is_insecure(source_health):
            drop_counts["insecure_transport"] = len(trades)
            self._set_health_value(
                "openinsider_cluster_rows_dropped", drop_counts
            )
            self._set_health_value(
                "insider_cluster_fallback_reason",
                "insecure_http_disallowed",
            )
            return []

        watchlist = {t.upper() for t in ALL_TICKERS}
        by_ticker = {}
        temporally_eligible_count = 0
        cutoff = self.now_utc - timedelta(days=days_back)

        for trade in trades:
            if not isinstance(trade, dict):
                drop_counts["not_mapping"] += 1
                continue
            if trade.get("source_stale") not in (None, False):
                drop_counts["stale"] += 1
                continue
            if (
                trade.get("source_transport_secure") is False
                or str(trade.get("source_transport_scheme") or "").lower()
                == "http"
                or trade.get("signal_eligible") is False
            ):
                drop_counts["insecure_transport"] += 1
                continue
            filing_stamp = to_utc_z(trade.get("filing_date"))
            if filing_stamp is None:
                drop_counts["undated"] += 1
                continue
            try:
                filing_at = datetime.fromisoformat(
                    filing_stamp.replace("Z", "+00:00")
                )
            except ValueError:
                drop_counts["undated"] += 1
                continue
            if filing_at > self.now_utc:
                drop_counts["future"] += 1
                continue
            if filing_at < cutoff:
                drop_counts["out_of_window"] += 1
                continue
            temporally_eligible_count += 1
            ticker = str(trade.get("ticker") or "").strip().upper()
            if ticker not in watchlist:
                drop_counts["off_watchlist"] += 1
                continue
            by_ticker.setdefault(ticker, []).append(trade)

        eligible_count = sum(len(rows) for rows in by_ticker.values())
        self._set_health_value(
            "openinsider_cluster_rows_eligible", eligible_count
        )
        self._set_health_value(
            "openinsider_cluster_rows_dropped", drop_counts
        )
        if not eligible_count:
            reason = (
                "insecure_http_disallowed"
                if drop_counts["insecure_transport"]
                else "stale_cache_disallowed"
                if drop_counts["stale"] and temporally_eligible_count == 0
                else "no_temporally_eligible_rows"
                if temporally_eligible_count == 0
                else "no_watchlist_rows"
            )
            self._set_health_value("insider_cluster_fallback_reason", reason)
            return []

        clusters = []
        for ticker, filings in by_ticker.items():
            if len(filings) < 2:
                continue

            filing_dates = {
                f.get("filing_date") for f in filings
                if f.get("filing_date")
            }

            code_counts = Counter()
            owner_codes = {}
            for f in filings:
                code = self._openinsider_code(f.get("trade_type"))
                if code:
                    code_counts[code] += 1
                name = (f.get("insider_name") or "").strip().lower()
                # unnamed rows can't prove a distinct filer
                if name and code:
                    owner_codes.setdefault(name, set()).add(code)

            buyers = sum(1 for codes in owner_codes.values() if "P" in codes)
            sellers = sum(1 for codes in owner_codes.values() if "S" in codes)
            cluster = self._build_cluster(
                ticker, buyers, sellers, len(filings), owner_codes,
                code_counts, days_back, "openinsider",
            )
            if not cluster:
                continue
            cluster["unique_dates"] = len(filing_dates)
            cluster["filings"] = filings[:5]
            cluster["as_of"] = newest(
                to_utc_z(f.get("filing_date")) for f in filings
            )
            clusters.append(cluster)

        clusters = self._sort_clusters(clusters)
        self._set_health_value(
            "insider_cluster_fallback_reason",
            None if clusters else "no_qualifying_clusters",
        )
        return clusters

    @staticmethod
    def _build_cluster(ticker, buyers, sellers, filing_count, owner_codes,
                       code_counts, period_days, source):
        """Cluster gate + record. >=2 distinct filers must share an open-market
        direction. Buy clusters alert HIGH at >=3 buyers, MEDIUM at 2; mixed
        (equal buyers and sellers) is a contested buy cluster, MEDIUM; sell
        clusters are context only and never rise above LOW.
        Returns None when no direction qualifies."""
        qualifying = max(buyers, sellers)
        if qualifying < 2:
            return None
        direction = SECAgent._cluster_direction(buyers, sellers)
        if direction == "buy":
            alert_level = "HIGH" if buyers >= 3 else "MEDIUM"
        elif direction == "mixed":
            alert_level = "MEDIUM"
        else:
            alert_level = "LOW"
        return {
            "ticker": ticker,
            "insider_count": qualifying,
            "buyers": buyers,
            "sellers": sellers,
            "filing_count": filing_count,
            "unique_insiders": len(owner_codes),
            "code_counts": dict(code_counts),
            "cluster_direction": direction,
            "alert_level": alert_level,
            "period_days": period_days,
            "source": source,
        }

    @staticmethod
    def _sort_clusters(clusters):
        """Headline signal first: buy > mixed > sell, then by filer count."""
        priority = {"buy": 0, "mixed": 1, "sell": 2}
        clusters.sort(key=lambda c: (priority.get(c.get("cluster_direction"), 3),
                                     -c["insider_count"]))
        return clusters

    @staticmethod
    def _openinsider_code(trade_type):
        match = OPENINSIDER_CODE_RE.match(str(trade_type or ""))
        return match.group(1).upper() if match else None

    @staticmethod
    def _cluster_direction(buyers, sellers):
        """buy/sell by distinct-filer majority; tie = mixed; null when no
        filer transacted open-market at all (grants/exercises carry no direction)."""
        if buyers == 0 and sellers == 0:
            return None
        if buyers > sellers:
            return "buy"
        if sellers > buyers:
            return "sell"
        return "mixed"

    def _fetch_form4_details(self, filing):
        """Pull transaction codes and reporting-owner CIKs out of a Form 4's raw XML.

        Returns {"codes": [...], "owners": [...]}, or None when the document
        could not be fetched or is not the ownership XML."""
        url = filing.get("primary_doc_url") or ""
        if not url:
            return None
        # submissions API points at the XSL-rendered view; the raw XML is the
        # same filename one path segment up
        url = re.sub(r"/xslF345X\d+/", "/", url)
        if not url.lower().endswith(".xml"):
            return None
        resp = self._get_with_retries(url, timeout=10, max_attempts=2)
        if not resp or resp.status_code != 200:
            return None
        return {
            "codes": FORM4_CODE_RE.findall(resp.text),
            "owners": FORM4_OWNER_RE.findall(resp.text),
        }

    def get_full_sec_dump(self):
        """Returns all SEC data as formatted strings for the daily dump."""
        output = []
        output.append("=" * 50)
        output.append("SEC EDGAR FILINGS (8-K, Form 4, 10-Q)")
        output.append(f"Tickers: {', '.join(ALL_TICKERS)}")
        output.append("=" * 50)

        filings = self.scan_all_watchlist(days_back=7)

        if filings:
            # Group by form type
            by_type = {}
            for f in filings:
                by_type.setdefault(f["form_type"], []).append(f)

            for form_type, items in by_type.items():
                output.append(f"\n--- {form_type} FILINGS ---")
                for f in items:
                    output.append(f"[{f['ticker']}] {f['description'][:100]}")
                    output.append(f"   Filed: {f['date']} | Link: {f['link']}")
        else:
            output.append("No recent filings found.")

        return output


# Quick test
if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    agent = SECAgent()

    print("\n" + "=" * 60)
    print("SEC AGENT - LIVE DUMP")
    print("=" * 60)

    # Test with a single ticker first
    print("\nTesting TSLA filings (last 7 days):")
    filings = agent.get_recent_filings("TSLA", days_back=7)
    for f in filings:
        print(f"  [{f['form_type']}] {f['description'][:80]}")
        print(f"    Filed: {f['date']}")

    print(f"\nTotal: {len(filings)} filings")
