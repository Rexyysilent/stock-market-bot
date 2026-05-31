import requests
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger("NewsAgent")


class NewsAgent:
    def __init__(self):
        self.base_url = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def get_global_headlines(self):
        """Fetch broad market headlines from Google News RSS."""
        return self._fetch_rss("Stock Market OR Global Economy OR Finance")

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
