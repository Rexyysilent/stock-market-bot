"""
OpenInsider Agent - HTML table scraper for insider trading data.

OpenInsider's /rss endpoint currently serves HTML, not valid XML, so the
bot should treat OpenInsider as an HTML table source directly. The primary
parser is pandas.read_html, with a BeautifulSoup fallback for parser issues.
"""
import logging
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("OpenInsiderAgent")


class OpenInsiderAgent:
    """Fetch OpenInsider's screener table with a browser-like user agent."""

    def __init__(self):
        self.base_url = "http://openinsider.com/screener"
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
        self.health = self._empty_health()

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
        """Fetch recent insider trades from the OpenInsider screener."""
        return self._fetch_trades(ticker="", days_back=days_back, limit=limit)

    def get_ticker_trades(self, ticker, days_back=90, limit=50):
        """Fetch insider trades for a specific ticker."""
        return self._fetch_trades(ticker=ticker, days_back=days_back, limit=limit)

    def _fetch_trades(self, ticker="", days_back=30, limit=200):
        params = self._default_params(ticker=ticker, days_back=days_back, limit=limit)
        health = self._empty_health()
        health.update({
            "ticker": ticker.upper() if ticker else "ALL",
            "days_back": days_back,
            "limit": limit,
        })
        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=self.headers,
                timeout=15,
            )
            health["request_status"] = response.status_code
            if response.status_code != 200:
                label = ticker.upper() if ticker else "recent trades"
                logger.warning(f"OpenInsider returned {response.status_code} for {label}")
                health["status"] = "ERROR"
                health["error"] = f"HTTP {response.status_code}"
                self.health = health
                return []

            trades = self._parse_tables_with_pandas(response.text)
            health["parser"] = "pandas" if trades else None
            health["raw_rows"] = len(trades)
            if not trades:
                logger.info("OpenInsider pandas parser found no rows; trying BeautifulSoup fallback")
                trades = self._parse_tables_with_bs4(response.text)
                health["parser"] = "beautifulsoup" if trades else None
                health["fallback_used"] = True
                health["raw_rows"] = len(trades)

            cutoff = datetime.now() - timedelta(days=days_back)
            filtered = []
            for trade in trades:
                filing_date = self._parse_date(trade.get("filing_date"))
                if filing_date and filing_date < cutoff:
                    continue
                filtered.append(trade)

            latest = self._latest_filing_date(filtered)
            if latest:
                age_days = (datetime.now() - latest).total_seconds() / 86400
                health["latest_filing_date"] = latest.strftime("%Y-%m-%d %H:%M:%S")
                health["latest_filing_age_days"] = round(age_days, 1)
            health["filtered_rows"] = len(filtered)
            health["status"] = "OK" if filtered else "WARN"
            if not filtered:
                health["warning"] = "No OpenInsider rows returned after filtering."
            self.health = health

            logger.info(f"OpenInsider parsed {len(filtered)} rows")
            return filtered[:limit]

        except requests.exceptions.Timeout:
            logger.error("OpenInsider request timed out")
            health["status"] = "ERROR"
            health["error"] = "Request timed out"
            self.health = health
            return []
        except Exception as exc:
            logger.error(f"OpenInsider table fetch failed: {exc}")
            health["status"] = "ERROR"
            health["error"] = str(exc)
            self.health = health
            return []

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

    def format_for_whispers(self, days_back=7, limit=5, max_stale_days=3):
        """Format recent trades as whisper-style one-liners for the social feed."""
        trades = self.get_recent_trades(days_back=days_back, limit=limit)
        items = []

        warning = self._freshness_warning(trades, max_stale_days=max_stale_days)
        if warning:
            items.append(warning)

        for trade in trades:
            ticker = trade.get("ticker") or "?"
            trade_type = trade.get("trade_type") or "Trade"
            value = trade.get("value") or "?"
            insider = trade.get("insider_name") or "Unknown insider"
            company = trade.get("company_name") or ""
            filing_date = trade.get("filing_date") or "Unknown date"
            items.append(
                f"[OpenInsider] {ticker}: {trade_type} {value} by {insider} "
                f"{company} (Filed: {filing_date})"
            )
        return items

    def _freshness_warning(self, trades, max_stale_days=3):
        if not trades:
            logger.warning("OpenInsider returned no rows for freshness check")
            warning = "No OpenInsider rows returned; source may be down or stale."
            self.health.update({
                "status": "WARN",
                "stale": True,
                "warning": warning,
                "max_stale_days": max_stale_days,
            })
            return f"[OpenInsider/WARN] {warning}"

        latest = self._latest_filing_date(trades)

        if latest is None:
            logger.warning("OpenInsider rows had no parseable filing dates")
            warning = "Could not parse OpenInsider filing dates; freshness unknown."
            self.health.update({
                "status": "WARN",
                "stale": None,
                "warning": warning,
                "max_stale_days": max_stale_days,
            })
            return f"[OpenInsider/WARN] {warning}"

        age_days = (datetime.now() - latest).total_seconds() / 86400
        latest_text = latest.strftime("%Y-%m-%d %H:%M:%S")
        self.health.update({
            "latest_filing_date": latest_text,
            "latest_filing_age_days": round(age_days, 1),
            "max_stale_days": max_stale_days,
            "stale": age_days > max_stale_days,
        })
        if age_days > max_stale_days:
            logger.warning(
                "OpenInsider latest filing is stale: %s (%.1f calendar days old)",
                latest_text,
                age_days,
            )
            warning = (
                f"Latest OpenInsider filing is {latest_text} "
                f"({age_days:.1f} calendar days old); source may be stale."
            )
            self.health.update({
                "status": "WARN",
                "warning": warning,
            })
            return f"[OpenInsider/WARN] {warning}"

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
