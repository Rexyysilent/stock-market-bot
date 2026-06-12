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
import time
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Lock
from config import SEC_USER_AGENT, ALL_TICKERS
from openinsider_agent import OpenInsiderAgent

logger = logging.getLogger("SECAgent")


class SECAgent:
    def __init__(self):
        self.headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json"
        }
        # Company ticker -> {"cik", "title"} mapping cache
        self._cik_cache = {}
        # CIK -> trimmed submissions JSON (shared by watchlist scan + insider fallback)
        self._submissions_cache = {}
        self._cache_lock = Lock()
        self.openinsider = OpenInsiderAgent()
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

        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
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
        Detect insider selling clusters when multiple insiders sell
        within a 14-day window. Treat as a risk indicator for review.
        Tries OpenInsider's parsed trade table first, then falls back to SEC EDGAR.
        Returns list of {ticker, insider_count, filings, alert_level}
        """
        openinsider_clusters = self._detect_openinsider_clusters(days_back=days_back)
        if openinsider_clusters:
            logger.info(
                f"Insider cluster scan: {len(openinsider_clusters)} tickers via OpenInsider"
            )
            self._set_health_value("insider_cluster_source", "OpenInsider")
            self._set_health_value("insider_cluster_fallback_used", False)
            self._set_health_value("openinsider_cluster_count", len(openinsider_clusters))
            self._set_health_value("insider_cluster_count", len(openinsider_clusters))
            return openinsider_clusters

        logger.warning("OpenInsider produced no clusters; falling back to SEC EDGAR Form 4 scan")
        self._set_health_value("insider_cluster_source", "SEC EDGAR fallback")
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
                
                # Count unique filing dates as proxy for unique insiders
                # (each Form 4 = one insider transaction)
                filing_dates = [f["date"] for f in filings if f.get("date")]
                unique_dates = set(filing_dates)
                
                # Multiple Form 4s in a short window = cluster selling
                insider_count = len(filings)
                
                if insider_count >= 3:
                    alert_level = "HIGH"
                elif insider_count >= 2:
                    alert_level = "MEDIUM"
                else:
                    continue
                
                clusters.append({
                    "ticker": ticker,
                    "insider_count": insider_count,
                    "unique_dates": len(unique_dates),
                    "alert_level": alert_level,
                    "filings": filings[:5],  # Top 5 for display
                    "period_days": days_back,
                })
                
            except Exception as e:
                logger.error(f"Error detecting insider clusters for {ticker}: {e}")
                continue
        
        # Sort by insider count descending
        clusters.sort(key=lambda x: x["insider_count"], reverse=True)
        logger.info(f"Insider cluster scan: {len(clusters)} tickers with cluster selling")
        self._set_health_value("insider_cluster_count", len(clusters))
        return clusters


    def _detect_openinsider_clusters(self, days_back=30):
        trades = self.openinsider.get_recent_trades(days_back=days_back, limit=500)
        watchlist = {t.upper() for t in ALL_TICKERS}
        by_ticker = {}

        for trade in trades:
            ticker = trade.get("ticker", "").upper()
            trade_type = trade.get("trade_type", "").lower()
            if ticker not in watchlist:
                continue
            if not self._is_openinsider_sale(trade_type):
                continue
            by_ticker.setdefault(ticker, []).append(trade)

        clusters = []
        for ticker, filings in by_ticker.items():
            if len(filings) < 2:
                continue

            filing_dates = {
                f.get("filing_date") for f in filings
                if f.get("filing_date")
            }
            insider_names = {
                f.get("insider_name") for f in filings
                if f.get("insider_name")
            }

            clusters.append({
                "ticker": ticker,
                "insider_count": len(filings),
                "unique_dates": len(filing_dates),
                "unique_insiders": len(insider_names),
                "alert_level": "HIGH" if len(filings) >= 3 else "MEDIUM",
                "filings": filings[:5],
                "period_days": days_back,
                "source": "OpenInsider",
            })

        clusters.sort(key=lambda x: x["insider_count"], reverse=True)
        return clusters

    def _is_openinsider_sale(self, trade_type):
        return (
            trade_type.startswith("s")
            or "sale" in trade_type
            or "sell" in trade_type
        )

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
