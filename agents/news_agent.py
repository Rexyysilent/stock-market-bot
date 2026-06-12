import requests
import re
import xml.etree.ElementTree as ET
import logging
from config import TICKER_ALIASES, MACRO_KEYWORDS

logger = logging.getLogger("NewsAgent")


class NewsAgent:
    def __init__(self):
        self.base_url = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def get_global_headlines(self):
        """Fetch broad market headlines, keep the most relevant (not the most recent).

        Pulls a wide net from Google News RSS, scores each headline against the
        watchlist universe (tickers + company aliases) and macro themes, and
        returns the top 5 by relevance. Ties fall back to chronological order.
        """
        raw = self._fetch_rss("Stock Market OR Global Economy OR Finance", limit=20)
        if not raw or raw[0].startswith("Error"):
            return raw
        return self._select_relevant(raw, top_n=5)

    @staticmethod
    def _select_relevant(headlines, top_n=5):
        scored = []
        for idx, headline in enumerate(headlines):
            text_lower = headline.lower()
            score = 0
            for ticker, aliases in TICKER_ALIASES.items():
                if re.search(rf"(?<![A-Za-z0-9]){ticker}(?![A-Za-z0-9])", headline):
                    score += 2
                elif any(alias in text_lower for alias in aliases):
                    score += 2
            score += sum(1 for kw in MACRO_KEYWORDS if kw in text_lower)
            scored.append((score, idx, headline))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [headline for _, _, headline in scored[:top_n]]

    def get_specific_news(self, query):
        """Fetch news for a specific topic/ticker."""
        return self._fetch_rss(query)

    def get_ticker_news(self, ticker):
        """Fetch news specifically about a stock ticker."""
        return self._fetch_rss(f"{ticker} stock price OR earnings OR analysis", limit=5)

    def _fetch_rss(self, query, limit=5):
        try:
            url = self.base_url.format(query=requests.utils.quote(query))
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                headlines = []
                for item in root.findall('./channel/item')[:limit]:
                    title = item.find('title').text
                    link = item.find('link').text
                    pub_date = item.find('pubDate')
                    date_str = pub_date.text[:16] if pub_date is not None else ""
                    headlines.append(f"{title} ({link})")
                return headlines
            else:
                logger.warning(f"News RSS returned status {response.status_code}")
                return [f"Error fetching news (Status {response.status_code})"]
        except Exception as e:
            logger.error(f"Error connecting to News Stream: {e}")
            return [f"Error connecting to News Stream: {e}"]
