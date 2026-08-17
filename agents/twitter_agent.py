"""
Twitter Agent - Multi-strategy X/Twitter intel gatherer
Strategy 1: Nitter RSS feeds for specific financial accounts (primary)
Strategy 2: Google News RSS fallback for topic-based searches

Note: The old Playwright headless approach has been removed — it was dead code
that never ran (CAPTCHA blocked, use_headless=False by default).
"""
import requests
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timezone
from config import NITTER_INSTANCES, TWITTER_ACCOUNTS, TWITTER_MAX_AGE_DAYS
from timeutil import split_fresh_records

logger = logging.getLogger("TwitterAgent")


class TwitterAgent:
    def __init__(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self._dropped_twitter_signals = []

        # Topic-based fallback queries (Google News RSS).
        # site:x.com, NOT site:twitter.com — Google News stopped indexing the
        # old domain after the X migration (checked 2026-07-06: twitter.com
        # queries return HTTP 200 with zero items; x.com returns 70-100).
        self.search_queries = [
            "site:x.com TSLA Tesla stock",
            "site:x.com uranium UUUU CCJ",
            "site:x.com CRISPR Vertex",
            "site:x.com silver gold squeeze",
            "site:x.com PLTR Palantir",
        ]

        # Track which Nitter instance is working
        self._working_instance = None
        self.health = self._empty_health()

    def _empty_health(self):
        return {
            "status": "UNKNOWN",
            "source": None,
            "prefer_nitter": True,
            "nitter_available": False,
            "nitter_instance": None,
            "fallback_used": False,
            "signals": 0,
            "status_counts": {},
            "errors": [],
            "nitter_accounts_expected": 0,
            "nitter_accounts_http_ok": 0,
            "nitter_accounts_with_items": 0,
            "nitter_account_failures": [],
        }

    def _record_status(self, source, status_code):
        key = f"{source}:{status_code}"
        counts = self.health.setdefault("status_counts", {})
        counts[key] = counts.get(key, 0) + 1

    def _record_error(self, source, error):
        errors = self.health.setdefault("errors", [])
        if len(errors) < 20:
            errors.append({
                "source": source,
                "error": str(error)[:180],
            })
        else:
            self.health["errors_omitted"] = self.health.get("errors_omitted", 0) + 1

    def _find_working_nitter(self):
        """Try each Nitter instance until one responds."""
        if self._working_instance:
            # Test if cached instance still works
            try:
                resp = requests.get(
                    f"{self._working_instance}/{TWITTER_ACCOUNTS[0]}/rss",
                    headers=self.headers, timeout=5
                )
                self._record_status("nitter_cached", resp.status_code)
                if resp.status_code == 200:
                    self.health["nitter_available"] = True
                    self.health["nitter_instance"] = self._working_instance
                    self.health["source"] = "Nitter RSS"
                    return self._working_instance
            except Exception as exc:
                self._record_error("nitter_cached", exc)
                pass

        # Try all instances
        for instance in NITTER_INSTANCES:
            try:
                resp = requests.get(
                    f"{instance}/{TWITTER_ACCOUNTS[0]}/rss",
                    headers=self.headers, timeout=5
                )
                self._record_status("nitter_probe", resp.status_code)
                if resp.status_code == 200:
                    self._working_instance = instance
                    self.health["nitter_available"] = True
                    self.health["nitter_instance"] = instance
                    self.health["source"] = "Nitter RSS"
                    logger.info(f"Using Nitter instance: {instance}")
                    return instance
            except Exception as exc:
                self._record_error("nitter_probe", exc)
                continue

        logger.warning("No Nitter instances available, falling back to Google News RSS")
        self.health["nitter_available"] = False
        return None

    def get_twitter_via_nitter(self, max_accounts=5):
        """
        Primary: Fetch tweets from financial accounts via Nitter RSS.
        Returns list of tweet dicts.
        """
        instance = self._find_working_nitter()
        if not instance:
            return []

        tweets = []
        accounts = TWITTER_ACCOUNTS[:max_accounts]
        self.health["nitter_accounts_expected"] = len(accounts)
        for account in accounts:
            try:
                url = f"{instance}/{account}/rss"
                resp = requests.get(url, headers=self.headers, timeout=10)
                self._record_status(f"nitter_{account}", resp.status_code)

                if resp.status_code == 200:
                    self.health["nitter_accounts_http_ok"] += 1
                    root = ET.fromstring(resp.content)
                    items = root.findall('.//item')[:3]
                    if items:
                        self.health["nitter_accounts_with_items"] += 1
                    else:
                        self.health["nitter_account_failures"].append({
                            "account": account,
                            "reason": "empty_feed",
                        })

                    for item in items:  # Top 3 per account
                        title = item.find('title')
                        link = item.find('link')
                        pub_date = item.find('pubDate')

                        if title is not None and title.text:
                            tweets.append({
                                'source': 'X/Twitter',
                                'account': f"@{account}",
                                'title': title.text[:150],
                                'link': link.text if link is not None else "",
                                # full RFC 2822 pubDate — truncating loses the tz
                                'date': pub_date.text if pub_date is not None else "",
                                'query': account
                            })
                else:
                    logger.warning(f"Nitter returned {resp.status_code} for @{account}")
                    self.health["nitter_account_failures"].append({
                        "account": account,
                        "reason": f"http_{resp.status_code}",
                    })

            except Exception as exc:
                logger.error(f"Error fetching @{account} via Nitter: {exc}")
                self._record_error(f"nitter_{account}", exc)
                self.health["nitter_account_failures"].append({
                    "account": account,
                    "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                })
                continue

        return tweets

    def get_twitter_via_rss(self, max_queries=3):
        """
        Fallback: Use Google News RSS to find Twitter-related discussions.
        Less direct but more reliable when Nitter is down.
        """
        tweets = []
        self.health["fallback_used"] = True
        self.health["source"] = "Google News RSS"

        for query in self.search_queries[:max_queries]:
            try:
                encoded_query = requests.utils.quote(query)
                url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"

                response = requests.get(url, headers=self.headers, timeout=10)
                self._record_status("google_news_rss", response.status_code)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)

                    for item in root.findall('.//item')[:3]:
                        title = item.find('title').text
                        link = item.find('link').text
                        pub_date = item.find('pubDate')

                        topic = query.split("site:x.com ")[-1].split()[0] if "site:x.com" in query else "X"

                        tweets.append({
                            'source': 'X/Twitter',
                            'account': 'GoogleNews',
                            'title': title[:120] if title else "Twitter post",
                            'link': link,
                            'date': pub_date.text if pub_date is not None else "",
                            'query': topic
                        })
            except Exception as exc:
                logger.error(f"RSS error for '{query[:30]}...': {exc}")
                self._record_error("google_news_rss", exc)
                continue

        return tweets

    def get_twitter_intel(self, max_queries=5, prefer_nitter=True):
        """
        Main method — tries Nitter first, falls back to Google News RSS.
        """
        self.health = self._empty_health()
        self.health["prefer_nitter"] = prefer_nitter
        tweets = []

        if prefer_nitter:
            logger.info("Trying Nitter RSS for Twitter intel...")
            tweets = self.get_twitter_via_nitter(max_accounts=max_queries)

        if not tweets:
            logger.info("Using Google News RSS fallback for Twitter intel...")
            tweets = self.get_twitter_via_rss(max_queries)

        # Deduplicate by link
        seen = set()
        unique_tweets = []
        for t in tweets:
            key = t.get('link', t.get('title', ''))
            if key and key not in seen:
                seen.add(key)
                unique_tweets.append(t)

        unique_tweets, self._dropped_twitter_signals = split_fresh_records(
            unique_tweets, "date", TWITTER_MAX_AGE_DAYS, now=self.now_utc
        )
        self.health["signals"] = len(unique_tweets)
        if not unique_tweets:
            self.health["status"] = "WARN"
            self.health["source"] = self.health.get("source") or "none"
        elif self.health.get("fallback_used"):
            self.health["status"] = "WARN"
        elif (
            self.health.get("nitter_accounts_expected", 0)
            and self.health.get("nitter_accounts_with_items", 0)
            < self.health.get("nitter_accounts_expected", 0)
        ):
            self.health["status"] = "WARN"
        else:
            self.health["status"] = "OK"

        logger.info(f"Gathered {len(unique_tweets)} X/Twitter indicators")
        return unique_tweets

    def get_dropped_twitter_signals(self):
        """Stale/undated rows removed by the seven-day freshness gate."""
        return [dict(row) for row in self._dropped_twitter_signals]

    def format_for_context(self, tweets):
        """Formats tweets for LLM context injection."""
        output = []
        for t in tweets[:10]:
            account = t.get('account', t.get('query', 'X'))
            output.append(f"[X/{account}] {t['title']}")
        return "\n".join(output)


# Quick test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    agent = TwitterAgent()

    print("\n" + "=" * 60)
    print("TWITTER AGENT TEST")
    print("=" * 60 + "\n")

    # Test Nitter first
    print("Testing Nitter RSS approach:")
    tweets = agent.get_twitter_intel(max_queries=5, prefer_nitter=True)

    if tweets:
        for t in tweets[:8]:
            print(f"  [{t['source']}] {t.get('account', '')} — {t['title'][:60]}...")
    else:
        print("  No tweets found (Nitter may be down)")

    print(f"\nTotal: {len(tweets)} indicators")
