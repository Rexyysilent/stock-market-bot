import requests
import time
import re
import xml.etree.ElementTree as ET
import logging
import os
from html import unescape
from copy import deepcopy
from threading import Lock

from openinsider_agent import OpenInsiderAgent
from timeutil import utc_now_z

from config import (
    SUBREDDITS_NARRATIVE, ALL_TICKERS,
    CONTRARIAN_SUBREDDITS, CONTRARIAN_EUPHORIA_KEYWORDS, CONTRARIAN_EUPHORIA_THRESHOLD,
    APEWISDOM_ENABLED, APEWISDOM_FILTERS, APEWISDOM_LOW_VOLUME_MENTIONS,
    APEWISDOM_NO_UPVOTE_FILTERS, APEWISDOM_UNIVERSE_PAGES
)

logger = logging.getLogger("SocialAgent")

# Try to import PRAW for authenticated Reddit access
try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False
    logger.warning("PRAW not installed — using public JSON API (rate limited). pip install praw")


class SocialAgent:
    def __init__(self, now=None):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.4472.124 Safari/537.36'
        }

        self.openinsider = OpenInsiderAgent(now=now)
        self._health_lock = Lock()
        self.health = self._empty_health()
        # Initialize PRAW if credentials are available
        self.reddit = None
        client_id = os.getenv("REDDIT_CLIENT_ID", "")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")

        if PRAW_AVAILABLE and client_id and client_id != "optional_reddit_id":
            try:
                self.reddit = praw.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    user_agent="MarketIntelBot/2.0 (by /u/MarketIntelBot)"
                )
                # Test the connection
                self.reddit.read_only = True
                logger.info("PRAW authenticated — using Reddit API (better rate limits + comment access)")
            except Exception as e:
                logger.error(f"PRAW auth failed, falling back to JSON API: {e}")
                self.reddit = None
        else:
            logger.info("Using Reddit RSS feeds (no API key needed — set REDDIT_CLIENT_ID/SECRET in .env for full PRAW access)")

        self._last_reddit_rss_time = 0
        self._apewisdom_records = []
        self._apewisdom_top200 = []
        self._set_health_value("reddit_mode", "praw" if self.reddit else "rss")

    def _empty_health(self):
        return {
            "reddit_mode": "unknown",
            "reddit_requests": 0,
            "reddit_status_counts": {},
            "reddit_rss_requests": 0,
            "reddit_failures": [],
            "reddit_failures_omitted": 0,
            "rss_status_counts": {},
            "rss_failures": [],
            "rss_failures_omitted": 0,
            "apewisdom_enabled": APEWISDOM_ENABLED,
            "apewisdom_requests": 0,
            "apewisdom_status_counts": {},
            "apewisdom_failures": [],
            "apewisdom_failures_omitted": 0,
            "apewisdom_items": 0,
        }

    def _set_health_value(self, key, value):
        with self._health_lock:
            self.health[key] = value

    def _bump_health(self, key, amount=1):
        with self._health_lock:
            self.health[key] = self.health.get(key, 0) + amount

    def _record_source_status(self, bucket_name, source, status_code):
        key = f"{source}:{status_code}"
        with self._health_lock:
            bucket = self.health.setdefault(bucket_name, {})
            bucket[key] = bucket.get(key, 0) + 1

    def _record_source_failure(self, bucket_name, source, status, detail=None):
        entry = {
            "source": source,
            "status": str(status),
        }
        if detail:
            entry["detail"] = str(detail)[:180]

        omitted_key = f"{bucket_name}_omitted"
        with self._health_lock:
            bucket = self.health.setdefault(bucket_name, [])
            if len(bucket) < 40:
                bucket.append(entry)
            else:
                self.health[omitted_key] = self.health.get(omitted_key, 0) + 1

    def get_health(self):
        with self._health_lock:
            return deepcopy(self.health)

    def get_whisper(self, limit_per_source=5):
        whispers = []

        # 1. NARRATIVE: niche thesis subs only (rising + hot). Broad ticker
        #    heat comes from ApeWisdom; the old 24-sub sweep just 429'd.
        for sub in SUBREDDITS_NARRATIVE:
            whispers.extend(self._fetch_reddit(sub, "rising", limit=3))
            whispers.extend(self._fetch_reddit(sub, "hot", limit=3))

        # 2. ApeWisdom ticker heat (fills Reddit RSS score/comment metadata gap).
        # Structured records are the data layer (get_apewisdom_attention);
        # whispers only ever carry the rendered string — market-color rows only,
        # so renders don't explode to one line per universe ticker.
        if APEWISDOM_ENABLED:
            _, render_records = self._build_social_attention()
            whispers.extend(self._render_apewisdom_whisper(r) for r in render_records)
        else:
            with self._health_lock:
                self._apewisdom_records = []
                self._apewisdom_top200 = []

        # 3. Scrape Hacker News
        whispers.extend(self._fetch_hacker_news(limit=10))

        # 4. Direct RSS Feeds (ZeroHedge / OpenInsider / Biotech / Layoffs)
        from config import RSS_FEEDS
        for feed in RSS_FEEDS:
            whispers.extend(self._fetch_rss(feed, limit=5))

        # Deduplicate (rising + hot often overlap)
        seen = set()
        unique_whispers = []
        for w in whispers:
            if w not in seen:
                seen.add(w)
                unique_whispers.append(w)
        whispers = unique_whispers

        logger.info(f"Gathered {len(whispers)} whispers from all sources")
        return whispers

    # Universe tickers ApeWisdom can never track: futures ("=") and indices ("^")
    SOCIAL_UNIVERSE = [t for t in ALL_TICKERS if "^" not in t and "=" not in t]

    def _build_social_attention(self):
        """Universe-first social_attention assembly.

        Returns (attention_records, render_records):
        - attention_records: one row per universe ticker (wherever it ranks in
          the paginated all-stocks leaderboard, or mentions=0 /
          in_leaderboard=false if absent), plus the configured top-N
          market-color rows flagged universe_member=false.
        - render_records: the market-color subset only (what the whisper
          strings have always shown).

        Also stores the rank<=200 all-stocks rows for leaderboard-entrance
        detection (get_apewisdom_top200).
        """
        observed_at = utc_now_z()
        all_rows = self._fetch_apewisdom(
            "all-stocks", limit=None, pages=APEWISDOM_UNIVERSE_PAGES
        )
        by_ticker = {row["ticker"]: row for row in all_rows}

        attention_records = []
        emitted_all_stocks = set()
        for ticker in self.SOCIAL_UNIVERSE:
            row = by_ticker.get(ticker)
            if row is not None:
                rec = dict(
                    row,
                    universe_member=True,
                    in_leaderboard=True,
                    observed_at=observed_at,
                )
            else:
                # Not in the scanned leaderboard depth: mentions=0 by spec
                # (velocity/rank unknowable, attention_score unmeasured).
                rec = {
                    "source": "ApeWisdom",
                    "filter": "all-stocks",
                    "ticker": ticker,
                    "name": None,
                    "rank": None,
                    "mentions": 0,
                    "upvotes": 0,
                    "mentions_24h_ago": None,
                    "rank_24h_ago": None,
                    "mention_velocity_24h": None,
                    "rank_delta_24h": None,
                    "attention_score": None,
                    "is_low_volume": True,
                    "universe_member": True,
                    "in_leaderboard": False,
                    "observed_at": observed_at,
                }
            attention_records.append(rec)
            emitted_all_stocks.add(ticker)

        render_records = []
        for filter_name, limit in APEWISDOM_FILTERS:
            if filter_name == "all-stocks":
                rows = all_rows[:limit]  # reuse the paginated fetch
            else:
                rows = self._fetch_apewisdom(filter_name, limit=limit)
            for row in rows:
                render_records.append(row)
                if filter_name == "all-stocks" and row["ticker"] in emitted_all_stocks:
                    continue  # universe row already carries this data
                attention_records.append(
                    dict(row,
                         universe_member=row["ticker"] in self.SOCIAL_UNIVERSE,
                         in_leaderboard=True,
                         observed_at=observed_at)
                )

        top200 = [
            {
                "ticker": r["ticker"],
                "rank": r["rank"],
                "mentions": r["mentions"],
                "observed_at": observed_at,
            }
            for r in all_rows
            if r.get("rank") is not None and r["rank"] <= 200
        ]
        with self._health_lock:
            self._apewisdom_records = deepcopy(attention_records)
            self._apewisdom_top200 = deepcopy(top200)
        return attention_records, render_records

    def _fetch_apewisdom(self, filter_name="all-stocks", limit=15, pages=1):
        """Fetch ApeWisdom aggregate ticker attention data.

        Returns structured records (data layer). Strings for Telegram/Discord
        come from _render_apewisdom_whisper — never store them here.

        attention_score = upvotes / max(mentions, 1) — engagement quality,
        separating "one viral post everyone upvotes" from broad low-effort
        chatter. Null for filters whose source has no upvote mechanic
        (APEWISDOM_NO_UPVOTE_FILTERS): there upvotes=0 means "not applicable",
        not "measured zero". The data layer carries only measurements and
        trivially interpretable ratios; blended/modeled scores belong
        downstream in analysis code where they can be iterated.
        """
        base_url = f"https://apewisdom.io/api/v1.0/filter/{filter_name}"
        records = []
        seen_tickers = set()

        try:
            results = []
            for page in range(1, pages + 1):
                url = base_url if page == 1 else f"{base_url}/page/{page}"
                self._bump_health("apewisdom_requests")
                response = requests.get(url, headers=self.headers, timeout=10)
                self._record_source_status(
                    "apewisdom_status_counts", filter_name, response.status_code
                )
                if response.status_code != 200:
                    logger.warning(f"ApeWisdom returned {response.status_code} for {filter_name}")
                    self._record_source_failure(
                        "apewisdom_failures", filter_name, response.status_code, url
                    )
                    break  # keep rows already fetched; failure is in health

                page_results = response.json().get("results", [])
                if not page_results:
                    break
                results.extend(page_results)

            if limit is not None:
                results = results[:limit]
            for row in results:
                ticker = str(row.get("ticker", "")).upper().strip()
                if not ticker or ticker in seen_tickers:
                    continue  # leaderboard can shift between page fetches
                seen_tickers.add(ticker)

                name = str(row.get("name", "")).strip()
                rank = self._safe_int(row.get("rank"))
                mentions = self._safe_int(row.get("mentions"))
                upvotes = self._safe_int(row.get("upvotes"))
                prev_mentions = self._safe_int(row.get("mentions_24h_ago"))
                prev_rank = self._safe_int(row.get("rank_24h_ago"))

                mention_velocity = None
                if mentions is not None and prev_mentions is not None:
                    mention_velocity = mentions - prev_mentions

                # positive = climbed the leaderboard over 24h
                rank_delta = None
                if rank is not None and prev_rank is not None:
                    rank_delta = prev_rank - rank

                attention_score = None
                if (filter_name not in APEWISDOM_NO_UPVOTE_FILTERS
                        and upvotes is not None and mentions is not None):
                    attention_score = round(upvotes / max(mentions, 1), 3)

                records.append({
                    "source": "ApeWisdom",
                    "filter": filter_name,
                    "ticker": ticker,
                    "name": name or None,
                    "rank": rank,
                    "mentions": mentions,
                    "upvotes": upvotes,
                    "mentions_24h_ago": prev_mentions,
                    "rank_24h_ago": prev_rank,
                    "mention_velocity_24h": mention_velocity,
                    "rank_delta_24h": rank_delta,
                    "attention_score": attention_score,
                    "is_low_volume": (
                        mentions is None or mentions < APEWISDOM_LOW_VOLUME_MENTIONS
                    ),
                })

            self._bump_health("apewisdom_items", len(records))
        except Exception as e:
            logger.error(f"ApeWisdom fetch failed for {filter_name}: {e}")
            self._record_source_failure("apewisdom_failures", filter_name, "exception", e)

        return records

    def _render_apewisdom_whisper(self, rec):
        """Render layer: human-readable string for a structured ApeWisdom record."""
        name_text = f" ({rec['name']})" if rec.get("name") else ""
        rank_text = f"#{rec['rank']}" if rec.get("rank") is not None else "N/A"
        mentions_text = rec["mentions"] if rec.get("mentions") is not None else "?"
        upvotes_text = rec["upvotes"] if rec.get("upvotes") is not None else "?"

        mention_delta = ""
        if rec.get("mention_velocity_24h") is not None:
            mention_delta = f" | Mentions 24h: {rec['mention_velocity_24h']:+d}"

        rank_delta = ""
        if rec.get("rank_delta_24h") is not None:
            rank_delta = f" | Rank move: {rec['rank_delta_24h']:+d}"

        return (
            f"[ApeWisdom/{rec['filter']}] {rec['ticker']}{name_text}: "
            f"rank {rank_text}, mentions {mentions_text}, upvotes {upvotes_text}"
            f"{mention_delta}{rank_delta}"
        )

    def get_apewisdom_attention(self):
        """Structured ApeWisdom records from the most recent get_whisper() run."""
        with self._health_lock:
            return deepcopy(self._apewisdom_records)

    def get_apewisdom_top200(self):
        """rank<=200 all-stocks rows from the most recent get_whisper() run.

        Input for leaderboard-entrance detection (signals.py); not a JSON
        section of its own.
        """
        with self._health_lock:
            return deepcopy(self._apewisdom_top200)

    def _safe_int(self, value):
        try:
            if value in (None, "", "N/A"):
                return None
            return int(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    def get_ticker_whispers(self, ticker):
        """
        Search for whispers specifically about a ticker.
        Uses PRAW search if available, falls back to JSON.
        """
        whispers = []
        search_subs = ["stocks", "investing", "wallstreetbets", "options"]

        if self.reddit:
            # PRAW — better search, includes comment previews
            for sub_name in search_subs:
                try:
                    sub = self.reddit.subreddit(sub_name)
                    for post in sub.search(ticker, sort="new", time_filter="week", limit=5):
                        if not post.stickied and not self._is_bot(post.title):
                            # Grab top comment preview if available
                            comment_preview = ""
                            try:
                                post.comments.replace_more(limit=0)
                                if post.comments:
                                    top_comment = post.comments[0].body[:80]
                                    comment_preview = f" | Top: \"{top_comment}...\""
                            except Exception:
                                pass
                            whispers.append(
                                f"[r/{sub_name}] {post.title} (Score: {post.score} | Cmts: {post.num_comments}{comment_preview})"
                            )
                except Exception as e:
                    logger.error(f"PRAW search r/{sub_name} for {ticker}: {e}")
        else:
            # Fallback: RSS search (no auth needed)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for sub in search_subs:
                try:
                    self._rss_rate_wait()
                    url = f"https://www.reddit.com/r/{sub}/search.rss?q={ticker}&restrict_sr=1&sort=new&limit=5"
                    response = requests.get(url, headers=self.headers, timeout=10)
                    if response.status_code == 200:
                        root = ET.fromstring(response.content)
                        for entry in root.findall('atom:entry', ns):
                            title_el = entry.find('atom:title', ns)
                            title = title_el.text if title_el is not None else ''
                            if title and not self._is_bot(title):
                                whispers.append(f"[r/{sub}] {title}")
                    else:
                        logger.warning(f"Reddit RSS search returned {response.status_code} for r/{sub} q={ticker}")
                except Exception as e:
                    logger.error(f"Error searching r/{sub} for {ticker} via RSS: {e}")

        return whispers

    def _fetch_rss(self, url, limit):
        items = []
        is_openinsider = 'openinsider' in url
        if is_openinsider:
            logger.info("OpenInsider RSS URL serves HTML; using table scraper directly")
            return self.openinsider.format_for_whispers(days_back=7, limit=limit)

        try:
            # Determine source label based on URL
            if 'substack.com' in url:
                # Extract author: "https://doomberg.substack.com/feed" â†’ "doomberg"
                label = f"Substack/{url.split('//')[1].split('.')[0]}"
            elif 'zerohedge' in url:
                label = "ZeroHedge"
            else:
                label = "RSS/External"

            response = requests.get(url, headers=self.headers, timeout=8)
            self._record_source_status("rss_status_counts", label, response.status_code)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('.//item')[:limit]:
                    title = item.find('title')
                    if title is not None and title.text and not self._is_bot(title.text):
                        items.append(f"[{label}] {title.text}")
            else:
                logger.warning(f"RSS feed returned {response.status_code}: {url[:60]}")
                self._record_source_failure("rss_failures", label, response.status_code, url[:120])
        except Exception as e:
            logger.error(f"Error fetching RSS: {url[:60]}... — {e}")
            self._record_source_failure("rss_failures", "RSS/External", "exception", e)
        return items

    def _fetch_reddit(self, subreddit, category, limit, min_score=0, min_comments=0):
        """Fetch posts from a subreddit. Uses PRAW if available, else RSS feeds."""

        if self.reddit:
            return self._fetch_reddit_praw(subreddit, category, limit, min_score, min_comments)
        return self._fetch_reddit_rss(subreddit, category, limit, min_score, min_comments)

    def _fetch_reddit_praw(self, subreddit, category, limit, min_score=0, min_comments=0):
        """PRAW-based Reddit fetcher — authenticated, better rate limits."""
        data_list = []
        try:
            sub = self.reddit.subreddit(subreddit)
            if category == "rising":
                posts = sub.rising(limit=limit)
            elif category == "new":
                posts = sub.new(limit=limit)
            else:
                posts = sub.hot(limit=limit)

            for post in posts:
                if (not post.stickied
                        and not self._is_bot(post.title)
                        and post.score >= min_score
                        and post.num_comments >= min_comments):
                    data_list.append(
                        f"[r/{subreddit}] {post.title} (Score: {post.score} | Cmts: {post.num_comments})"
                    )
        except Exception as e:
            logger.error(f"PRAW error fetching r/{subreddit}/{category}: {e}")
            self._record_source_failure("reddit_failures", f"r/{subreddit}/{category}", "praw_exception", e)
            # Fallback to RSS on PRAW failure
            data_list = self._fetch_reddit_rss(subreddit, category, limit, min_score, min_comments)
        return data_list

    def _rss_rate_wait(self):
        """Simple rate limiter for Reddit RSS — 1 req/sec to avoid 429s."""
        elapsed = time.time() - self._last_reddit_rss_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_reddit_rss_time = time.time()

    def _fetch_reddit_rss(self, subreddit, category, limit, min_score=0, min_comments=0):
        """RSS-based Reddit fetcher - no auth needed, no API key required.

        Reddit RSS does not expose score/comment counts reliably, so keep the
        high-traffic filters strict instead of pretending RSS can satisfy them.
        """
        source = f"r/{subreddit}/{category}"
        rss_source = f"{source}/rss"
        if min_score or min_comments:
            self._record_source_failure(
                "reddit_failures",
                source,
                "rss_skipped_threshold_filter",
                "Reddit RSS lacks score/comment metadata for high-traffic filters.",
            )
            return []

        url = f"https://www.reddit.com/r/{subreddit}/{category}.rss?limit={max(limit * 3, 10)}"
        data_list = []
        try:
            self._rss_rate_wait()
            self._bump_health("reddit_rss_requests")
            response = requests.get(url, headers=self.headers, timeout=10)
            self._record_source_status(
                "reddit_status_counts", rss_source, response.status_code
            )
            if response.status_code == 200:
                # Reddit RSS uses Atom format
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                root = ET.fromstring(response.content)
                entries = root.findall('atom:entry', ns) or root.findall('.//entry')

                for entry in entries:
                    if len(data_list) >= limit:
                        break

                    title_el = entry.find('atom:title', ns)
                    if title_el is None:
                        title_el = entry.find('title')
                    if title_el is None or not title_el.text:
                        continue
                    title = unescape(title_el.text).strip()

                    if not title or self._is_bot(title):
                        continue

                    data_list.append(f"[r/{subreddit} RSS] {title} (via Reddit RSS)")
            elif response.status_code == 429:
                logger.warning(f"Reddit RSS rate limited on r/{subreddit}")
                self._record_source_failure("reddit_failures", rss_source, response.status_code)
            else:
                logger.warning(f"Reddit RSS returned {response.status_code} for r/{subreddit}/{category}")
                self._record_source_failure("reddit_failures", rss_source, response.status_code)
        except Exception as e:
            logger.error(f"Error fetching r/{subreddit}/{category} via RSS: {e}")
            self._record_source_failure("reddit_failures", rss_source, "rss_exception", e)
        return data_list

    def _is_bot(self, text):
        """Filter obvious bot/auto-mod posts."""
        bots = ["AutoModerator", "Daily Discussion", "Weekly Discussion", "Mega Thread",
                "Megathread", "Daily Thread", "Weekend Discussion"]
        return any(b.lower() in text.lower() for b in bots)

    def _fetch_hacker_news(self, limit):
        """
        HN API — great for tech stocks (PLTR, TSLA, CRSP).
        Structural workforce-change context.
        Distinguishes efficiency restructuring (cost cuts plus builder hiring)
        from broader operational stress (core-team cuts or shutdowns).
        """
        hn_list = []
        layoff_keywords = [
            "layoff", "laid off", "firing", "fired", "rif", "headcount",
            "hiring freeze", "workforce reduction", "downsizing", "restructuring",
            "job cuts", "let go", "severance"
        ]
        # Efficiency-restructuring indicators — cost cuts paired with builder hiring
        efficiency_keywords = [
            "ai engineer", "ai hiring", "machine learning", "compute",
            "middle management", "managers", "restructuring to", "efficiency",
            "streamlin", "automat", "cost cutting", "reorganiz"
        ]
        # Operational-stress indicators — cuts to core teams or shutdown language
        liquidation_keywords = [
            "core team", "product engineer", "entire team", "division shut",
            "office clos", "wind down", "bankruptcy", "chapter 11",
            "mass layoff", "all employees", "cease operations"
        ]
        try:
            ids = requests.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty",
                timeout=5
            ).json()
            for story_id in ids[:limit]:
                try:
                    item = requests.get(
                        f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                        timeout=3
                    ).json()
                    if item and 'title' in item:
                        title = item['title']
                        score = item.get('score', 0)
                        title_lower = title.lower()

                        if any(kw in title_lower for kw in layoff_keywords):
                            # Classify workforce-change context
                            is_efficiency = any(kw in title_lower for kw in efficiency_keywords)
                            is_liquidation = any(kw in title_lower for kw in liquidation_keywords)

                            if is_liquidation:
                                hn_list.append(
                                    f"[HackerNews] OPERATIONAL STRESS: {title} (Score: {score})"
                                )
                            elif is_efficiency:
                                hn_list.append(
                                    f"[HackerNews] EFFICIENCY RESTRUCTURING: {title} (Score: {score})"
                                )
                            else:
                                hn_list.append(
                                    f"[HackerNews] WORKFORCE CHANGE NOTE: {title} (Score: {score})"
                                )
                        else:
                            hn_list.append(
                                f"[HackerNews] {title} (Score: {score})"
                            )
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error fetching Hacker News: {e}")
        return hn_list

    # ===============================================================
    # RETAIL LANGUAGE INTENSITY (compatibility method name retained)
    # ===============================================================

    def get_retail_contrarian_index(self):
        """
        Measures retail euphoria in CONTRARIAN_SUBREDDITS.
        The compatibility flag `is_topped` means only that the configured language
        ratio threshold was crossed; it is not a forecast or trading rule.

        Also pulls 4chan /biz/ threads via the official JSON API.
        """
        results = {"subreddits": [], "biz": [], "alerts": []}

        # === Reddit Euphoria Scoring ===
        for sub_name in CONTRARIAN_SUBREDDITS:
            try:
                posts = self._fetch_reddit(sub_name, "hot", limit=25)
                if not posts:
                    continue

                total_posts = len(posts)
                euphoric_posts = []
                for post in posts:
                    post_lower = post.lower()
                    matched_keywords = [kw for kw in CONTRARIAN_EUPHORIA_KEYWORDS if kw in post_lower]
                    if matched_keywords:
                        euphoric_posts.append({"post": post, "keywords": matched_keywords})

                euphoria_ratio = len(euphoric_posts) / total_posts if total_posts > 0 else 0

                sub_result = {
                    "subreddit": sub_name,
                    "total_posts": total_posts,
                    "euphoric_posts": len(euphoric_posts),
                    "euphoria_ratio": round(euphoria_ratio, 2),
                    "is_topped": euphoria_ratio >= CONTRARIAN_EUPHORIA_THRESHOLD,
                    "top_euphoric": [p["post"][:100] for p in euphoric_posts[:3]],
                }
                results["subreddits"].append(sub_result)

                if sub_result["is_topped"]:
                    results["alerts"].append(
                        f"RETAIL LANGUAGE THRESHOLD — r/{sub_name}: "
                        f"{sub_result['euphoria_ratio']:.0%} euphoria ratio "
                        f"({sub_result['euphoric_posts']}/{sub_result['total_posts']} posts). "
                        f"Descriptive language measure only."
                    )

            except Exception as e:
                logger.error(f"Contrarian index error for r/{sub_name}: {e}")

        # === 4chan /biz/ via Official JSON API ===
        try:
            biz_threads = self._fetch_4chan_biz(limit=15)
            results["biz"] = biz_threads

            # Score /biz/ euphoria too
            if biz_threads:
                biz_euphoric = sum(
                    1 for t in biz_threads
                    if any(kw in t.get("subject", "").lower() + t.get("comment", "").lower()
                           for kw in CONTRARIAN_EUPHORIA_KEYWORDS)
                )
                biz_ratio = biz_euphoric / len(biz_threads)
                if biz_ratio >= CONTRARIAN_EUPHORIA_THRESHOLD:
                    results["alerts"].append(
                        f"RETAIL LANGUAGE THRESHOLD — /biz/: "
                        f"{biz_ratio:.0%} euphoria ratio. matching-language ratio."
                    )
        except Exception as e:
            logger.error(f"4chan /biz/ fetch error: {e}")

        print(f"[SocialAgent] Retail language intensity: {len(results['subreddits'])} communities scored, {len(results['alerts'])} thresholds")
        return results

    def _fetch_4chan_biz(self, limit=15):
        """
        Fetch threads from 4chan /biz/ using the official JSON API.
        https://a.4cdn.org/biz/catalog.json

        Returns list of thread summaries (subject + comment preview).
        """
        threads = []
        try:
            response = requests.get(
                "https://a.4cdn.org/biz/catalog.json",
                headers={'User-Agent': 'MarketIntelBot/2.0'},
                timeout=10
            )
            if response.status_code != 200:
                logger.warning(f"4chan /biz/ returned {response.status_code}")
                return threads

            catalog = response.json()
            # catalog is a list of pages, each with a 'threads' list
            all_threads = []
            for page in catalog:
                all_threads.extend(page.get('threads', []))

            # Sort by reply count (most active threads first)
            all_threads.sort(key=lambda t: t.get('replies', 0), reverse=True)

            for thread in all_threads[:limit]:
                subject = thread.get('sub', '')
                comment = thread.get('com', '')
                # Strip HTML tags from comment
                comment_clean = re.sub(r'<[^>]+>', '', comment)[:200] if comment else ""
                replies = thread.get('replies', 0)
                thread_no = thread.get('no', 0)

                threads.append({
                    "source": "/biz/",
                    "subject": subject,
                    "comment": comment_clean,
                    "replies": replies,
                    "thread_url": f"https://boards.4chan.org/biz/thread/{thread_no}",
                })

        except Exception as e:
            logger.error(f"Error fetching 4chan /biz/: {e}")

        return threads
