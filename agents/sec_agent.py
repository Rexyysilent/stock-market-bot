"""
SEC EDGAR Agent — Fetches SEC filings for watchlist tickers.
Uses the free EDGAR full-text search API (no API key needed).
- 8-K filings (material events)
- Form 4 (insider buys/sells)
- 13F (institutional holdings)

Docs: https://efts.sec.gov/LATEST/search-index?q=...
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
        self.base_url = "https://efts.sec.gov/LATEST/search-index"
        self.full_text_url = "https://efts.sec.gov/LATEST/search-index"
        self.edgar_search_url = "https://efts.sec.gov/LATEST/search-index"
        self.headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json"
        }
        # Company ticker -> CIK mapping cache
        self._cik_cache = {}
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

    def _get_cik(self, ticker):
        """Lookup CIK number for a ticker via SEC EDGAR."""
        if ticker in self._cik_cache:
            return self._cik_cache[ticker]
        try:
            url = "https://www.sec.gov/cgi-bin/browse-edgar"
            params = {
                "action": "getcompany",
                "company": ticker,
                "type": "",
                "dateb": "",
                "owner": "include",
                "count": "1",
                "search_text": "",
                "action": "getcompany",
                "output": "atom"
            }
            resp = self._get_with_retries(url, params=params, timeout=10)
            if resp and resp.status_code == 200:
                # Parse atom feed for CIK
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.content)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                entry = root.find(".//atom:entry", ns)
                if entry is not None:
                    cik_elem = entry.find(".//atom:content", ns)
                    if cik_elem is not None and cik_elem.text:
                        cik = cik_elem.text.strip()
                        self._cik_cache[ticker] = cik
                        return cik
        except Exception as e:
            logger.error(f"CIK lookup failed for {ticker}: {e}")
        return None

    def get_recent_filings(self, ticker, form_types=None, days_back=7, limit=5):
        """
        Fetch recent SEC filings for a ticker using EDGAR full-text search.
        
        Args:
            ticker: Stock ticker (e.g., "TSLA")
            form_types: List of form types to filter (e.g., ["8-K", "4"])
            days_back: How many days back to search
            limit: Max results per form type
        """
        if form_types is None:
            form_types = ["8-K", "4", "10-Q", "10-K"]

        filings = []
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        for form_type in form_types:
            try:
                url = "https://efts.sec.gov/LATEST/search-index"
                params = {
                    "q": f'"{ticker}"',
                    "dateRange": "custom",
                    "startdt": start_date,
                    "enddt": end_date,
                    "forms": form_type,
                }
                resp = self._get_with_retries(url, params=params, timeout=15)

                if resp and resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", {}).get("hits", [])

                    for hit in hits[:limit]:
                        source = hit.get("_source", {})
                        filings.append({
                            "ticker": ticker,
                            "form_type": form_type,
                            "title": source.get("display_names", [ticker])[0] if source.get("display_names") else ticker,
                            "description": source.get("file_description", "No description"),
                            "date": source.get("file_date", "Unknown"),
                            "link": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type={form_type}&dateb=&owner=include&count=10&search_text=&action=getcompany",
                        })
                elif resp and resp.status_code == 429:
                    logger.warning(f"SEC rate limited after retries for {ticker}/{form_type}; continuing")
                    self._record_failed_request(ticker, form_type, resp.status_code, "rate limited")
                    continue
                elif resp:
                    logger.warning(f"SEC EDGAR returned {resp.status_code} for {ticker}/{form_type}; continuing")
                    self._record_failed_request(ticker, form_type, resp.status_code)
                    continue
                else:
                    logger.warning(f"SEC EDGAR returned no response for {ticker}/{form_type}; continuing")
                    self._record_failed_request(ticker, form_type, "no_response")
                    continue

            except Exception as e:
                logger.error(f"Error fetching SEC filings for {ticker}/{form_type}: {e}")
                self._record_failed_request(ticker, form_type, "exception", e)

        return filings

    def get_insider_trades(self, ticker, days_back=14):
        """Fetch Form 4 (insider trading) filings for a ticker."""
        return self.get_recent_filings(ticker, form_types=["4"], days_back=days_back, limit=10)

    def get_material_events(self, ticker, days_back=30):
        """Fetch 8-K (material event) filings for a ticker."""
        return self.get_recent_filings(ticker, form_types=["8-K"], days_back=days_back, limit=5)

    def scan_all_watchlist(self, days_back=7):
        """Scan all watchlist tickers for recent important filings."""
        all_filings = []
        for ticker in ALL_TICKERS:
            filings = self.get_recent_filings(
                ticker,
                form_types=["8-K", "4"],
                days_back=days_back,
                limit=3
            )
            all_filings.extend(filings)
        
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
