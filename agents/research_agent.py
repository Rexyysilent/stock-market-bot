"""
Research Agent - Deep source scraper for specialized targets
- CEO.ca (own public JSON API; Google News proxy kept as fallback)
- ClinicalTrials.gov (public API v2)
- FDA PDUFA catalyst scanner (ClinicalTrials.gov Phase 3 + Google News RSS)
- Cash runway measurement (yfinance financials)
"""
import requests
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
import logging

from bs4 import BeautifulSoup

from config import CEO_CA_MAX_AGE_DAYS
from stateutil import atomic_write_json, load_json_state
from timeutil import split_fresh_records

logger = logging.getLogger("ResearchAgent")

# Try importing yfinance for cash runway checks
try:
    import yfinance as yf
    from yfinance_util import configure_yfinance_cache
    configure_yfinance_cache(yf)
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False
    logger.warning("yfinance not installed — cash runway checks disabled")


class ResearchAgent:
    def __init__(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self.now_naive_utc = self.now_utc.replace(tzinfo=None)
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        self._dropped_ceo_signals = []
        self._dropped_ceo_quality = []
        self.ceo_ca_health = {}
        
        # Uranium tickers to monitor on CEO.ca
        self.uranium_tickers = ["UUUU", "CCJ", "NXE", "DNN"]
        
        # CRISPR/Biotech sponsors to track
        self.biotech_sponsors = ["Vertex", "CRISPR Therapeutics", "Editas", "Intellia"]

        # Biotech tickers from watchlist (PRIORITY for PDUFA scanning)
        self.priority_biotech = ["CRSP", "NTLA", "RGNX"]

        # Set by get_adcom_calendar; surfaced as an export health warning.
        self.adcom_parse_failed = False
        self.adcom_detail_failures = []

    # Sponsor/title substring -> ticker (shared by financials pre-check and
    # the exporter's clinical_catalysts ticker attribution).
    SPONSOR_TICKER_MAP = {
        'vertex': 'VRTX',
        'crispr therapeutics': 'CRSP',
        'intellia': 'NTLA',
        'editas': 'EDIT',
        'regenxbio': 'RGNX',
        'bluebird': 'BLUE',
        'sarepta': 'SRPT',
        'biomarin': 'BMRN',
        'ultragenyx': 'RARE',
        'beam': 'BEAM',
        'verve': 'VERV',
        'prime medicine': 'PRME',
        'caribou': 'CRBU',
        'capricor': 'CAPR',
        'replimune': 'REPL',
    }

    @classmethod
    def resolve_ticker(cls, sponsor, title=""):
        """Map a trial sponsor (or title mention) to a ticker; None if unknown."""
        sponsor_lower = (sponsor or "").lower()
        title_lower = (title or "").lower()
        for name, tick in cls.SPONSOR_TICKER_MAP.items():
            if name in sponsor_lower or name in title_lower:
                return tick
        return None

    # ═══════════════════════════════════════════════════════════════════
    # FDA ADVISORY COMMITTEE CALENDAR (degradable)
    # ═══════════════════════════════════════════════════════════════════

    # The calendar table is hydrated client-side and its raw HTML is often an
    # empty shell. The FDA landing page server-renders upcoming meetings.
    ADCOM_LANDING_URL = "https://www.fda.gov/advisory-committees"
    ADCOM_CALENDAR_URL = (
        "https://www.fda.gov/advisory-committees/advisory-committee-calendar"
    )
    ADCOM_CACHE_FILE = os.path.join("state", "adcom_cache.json")
    ADCOM_CACHE_MAX_AGE_DAYS = 7

    def get_adcom_calendar(self):
        """FDA Advisory Committee meeting calendar; degradable by design.

        Cached in state/adcom_cache.json, refetched only when the cache is
        older than 7 days. The page format WILL change eventually: any
        fetch/parse failure sets self.adcom_parse_failed (export health
        warning `adcom_calendar: parse_failed`) and falls back to the stale
        cache (or []) — it never crashes the pipeline.
        """
        self.adcom_parse_failed = False
        self.adcom_detail_failures = []
        try:
            cache = load_json_state(self.ADCOM_CACHE_FILE, {})
        except (OSError, ValueError):
            cache = {}

        fetched_at = cache.get("fetched_at")
        if fetched_at:
            try:
                fetched = datetime.fromisoformat(
                    str(fetched_at).replace("Z", "+00:00")
                )
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                age_days = (
                    self.now_utc - fetched.astimezone(timezone.utc)
                ).days
                cached_meetings = cache.get("meetings", [])
                cache_has_details = cached_meetings and all(
                    row.get("detail_status") == "ok"
                    for row in cached_meetings
                )
                if (age_days <= self.ADCOM_CACHE_MAX_AGE_DAYS
                        and cache_has_details):
                    return cached_meetings
            except (TypeError, ValueError):
                pass

        try:
            resp = requests.get(self.ADCOM_LANDING_URL, headers=self.headers,
                                timeout=15)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            meetings = self._parse_adcom_page(resp.text)
        except Exception as e:
            logger.warning(f"AdCom calendar fetch/parse failed: {e}")
            self.adcom_parse_failed = True
            return cache.get("meetings", [])

        for meeting in meetings:
            try:
                detail_resp = requests.get(
                    meeting["link"], headers=self.headers, timeout=15
                )
                if detail_resp.status_code != 200:
                    raise RuntimeError(f"HTTP {detail_resp.status_code}")
                meeting.update(self._parse_adcom_detail(detail_resp.text))
                meeting["detail_status"] = "ok"
            except Exception as exc:
                failure = {
                    "date": meeting.get("date"),
                    "link": meeting.get("link"),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
                self.adcom_detail_failures.append(failure)
                meeting["detail_status"] = "error"
                meeting["detail_error"] = failure["error"]
                logger.warning(
                    "AdCom detail enrichment failed for %s: %s",
                    meeting.get("date"),
                    failure["error"],
                )

        atomic_write_json(
            self.ADCOM_CACHE_FILE,
            {
                "fetched_at": self.now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_url": self.ADCOM_LANDING_URL,
                "meetings": meetings,
            },
            indent=1,
        )
        logger.info(f"AdCom calendar: {len(meetings)} meetings parsed")
        return meetings

    def _parse_adcom_page(self, page_html):
        """Extract upcoming meeting announcements from server-rendered HTML."""
        meetings = []
        seen = set()
        anchor_re = re.compile(
            r'<a\b[^>]*href="(?P<href>/advisory-committees/[^"]+)"'
            r"[^>]*>(?P<title>.*?)</a>",
            re.IGNORECASE | re.DOTALL,
        )
        month_re = re.compile(
            r"\b(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{1,2}),\s*(\d{4})\b",
            re.IGNORECASE,
        )
        updated_re = re.compile(
            r"\bas\s+of\s+(\d{1,2})/(\d{1,2})/(\d{4})\b",
            re.IGNORECASE,
        )

        for match in anchor_re.finditer(page_html):
            title = unescape(re.sub(r"<[^>]+>", " ", match.group("title")))
            title = re.sub(r"\s+", " ", title).strip()
            if "meeting" not in title.lower():
                continue
            date_match = month_re.search(title)
            if date_match:
                meeting_dt = datetime.strptime(
                    " ".join(date_match.groups()), "%B %d %Y"
                )
            else:
                # Older Drupal markup keeps the ISO date in a nearby <time>.
                nearby = page_html[max(0, match.start() - 350):match.start()]
                iso_match = re.search(
                    r'datetime="(\d{4}-\d{2}-\d{2})', nearby, re.IGNORECASE
                )
                if not iso_match:
                    continue
                meeting_dt = datetime.strptime(iso_match.group(1), "%Y-%m-%d")

            source_as_of = None
            updated_match = updated_re.search(title)
            if updated_match:
                month, day, year = map(int, updated_match.groups())
                try:
                    source_as_of = datetime(year, month, day).strftime(
                        "%Y-%m-%dT00:00:00Z"
                    )
                except ValueError:
                    source_as_of = None

            href = match.group("href")
            key = (meeting_dt.date().isoformat(), title, href)
            if key in seen:
                continue
            seen.add(key)
            meetings.append(
                {
                    "date": meeting_dt.date().isoformat(),
                    "title": title[:250],
                    "link": "https://www.fda.gov" + href,
                    "as_of": source_as_of,
                    "observed_at": self.now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source": "FDA Advisory Committees",
                }
            )
        if not meetings:
            raise ValueError("no meeting rows parsed (page format changed?)")
        return sorted(meetings, key=lambda row: (row["date"], row["title"]))

    @classmethod
    def _parse_adcom_detail(cls, page_html):
        """Extract the agenda, sponsor, product, application, and ticker."""
        text = BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", unescape(text))
        agenda_matches = re.finditer(
            r"\bAgenda\b(?P<agenda>.*?)(?:\bMeeting Materials\b|"
            r"\bPublic Participation Information\b)",
            text,
            re.IGNORECASE,
        )
        agenda_candidates = [
            match.group("agenda").strip()
            for match in agenda_matches
            if len(match.group("agenda").strip()) >= 40
        ]
        if not agenda_candidates:
            raise ValueError("agenda section not found")
        # Updated FDA pages mention "Agenda" several times in change-summary
        # prose before the authoritative agenda. Prefer the candidate carrying
        # an actual application number, then fall back to the most substantive
        # block for non-product policy meetings.
        application_candidates = [
            candidate
            for candidate in agenda_candidates
            if re.search(r"\b(?:BLA|NDA)\)?\s+\d{4,}\b", candidate, re.I)
        ]
        agenda = max(
            application_candidates or agenda_candidates,
            key=len,
        )

        application_match = re.search(
            r"\b(BLA|NDA)\)?\s+(\d{4,})\b",
            agenda,
            re.IGNORECASE,
        )
        application = None
        if application_match:
            application = (
                f"{application_match.group(1).upper()} "
                f"{application_match.group(2)}"
            )

        sponsor = None
        product = None
        sponsor_product = re.search(
            r"\bfrom\s+(?P<sponsor>.{2,100}?)\s+for\s+"
            r"(?P<product>[A-Za-z][A-Za-z0-9 -]{1,80}?)"
            r"(?=\s*\(|[.,])",
            agenda,
            re.IGNORECASE,
        )
        if sponsor_product:
            sponsor = sponsor_product.group("sponsor").strip(" ,")
            product = sponsor_product.group("product").strip()

        return {
            "agenda": agenda[:1200],
            "application": application,
            "sponsor": sponsor,
            "product": product,
            "ticker": cls.resolve_ticker(sponsor, agenda),
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # CEO.CA — direct fetch via the site's own public JSON API
    # (new-api.ceo.ca/api/get_spiels, the same endpoint the site loads;
    # read-only, no auth, 1 request per channel per run).
    # Google News RSS proxy kept only as per-channel fallback.
    # Screening filter: regex extraction for geological context
    # ═══════════════════════════════════════════════════════════════════

    # Regex patterns for actual geological data (not promotional fluff)
    GRADE_PATTERN = re.compile(
        r'(\d+\.?\d*)\s*%\s*U3O8'             # Uranium grade: "1.5% U3O8"
        r'|(\d+\.?\d*)\s*g/t\s*(?:Au|Ag)'     # Gold/Silver grade: "10.2 g/t Au"
        r'|(\d+\.?\d*)\s*m\s*@'               # Intercept length: "5.4 m @"
        r'|(\d+\.?\d*)\s*(?:metres?|meters?)\s*(?:of|@|grading)'  # "12 metres of"
    , re.IGNORECASE)

    # A direct channel post can be authentic CEO.ca data while still being
    # irrelevant banter. Require a substantive domain anchor before contextual
    # chatter is allowed into the brief.
    CEO_CONTEXT_PATTERN = re.compile(
        r"\b(?:"
        r"uranium|u3o8|nuclear|reactor|fuel\s+cycle|enrichment|conversion|"
        r"mine|mining|miner|drill(?:ing)?|assay|intercept|core\s+sample|"
        r"resource\s+estimate|feasibility|permitting|permit|production|"
        r"athabasca|cigar\s+lake|mcarthur\s+river|wheeler\s+river|"
        r"rook\s+i|arrow\s+deposit|patterson\s+lake"
        r")\b",
        re.IGNORECASE,
    )

    # Minimum thresholds for structural alpha (not promotional mud)
    URANIUM_GRADE_MIN = 1.0     # ≥ 1.0% U3O8
    INTERCEPT_LENGTH_MIN = 5.0  # > 5m intercept

    # Threshold-qualified signals are always kept; context-only rows are
    # capped at the N newest per source (channel/feed).
    MAX_UNVERIFIED_PER_SOURCE = 2

    def _extract_grades(self, text):
        """
        Extract geological grade values from text using regex.
        Returns dict with extracted values or None if no match.
        """
        if not text:
            return None

        matches = self.GRADE_PATTERN.findall(text)
        if not matches:
            return None

        grades = {"uranium_pct": [], "gold_gpt": [], "intercept_m": []}
        for m in matches:
            if m[0]:  # U3O8 %
                grades["uranium_pct"].append(float(m[0]))
            if m[1]:  # g/t Au/Ag
                grades["gold_gpt"].append(float(m[1]))
            if m[2]:  # m @ intercept
                grades["intercept_m"].append(float(m[2]))
            if m[3]:  # metres of/grading
                grades["intercept_m"].append(float(m[3]))

        return grades if any(grades.values()) else None

    def _passes_drill_filter(self, grades):
        """
        Check if extracted grades meet minimum thresholds.
        Returns True if the signal has structural alpha.
        """
        if not grades:
            return False

        # Any uranium grade ≥ 1.0% U3O8 passes
        if any(g >= self.URANIUM_GRADE_MIN for g in grades.get("uranium_pct", [])):
            return True

        # Any intercept > 5m passes
        if any(g >= self.INTERCEPT_LENGTH_MIN for g in grades.get("intercept_m", [])):
            return True

        # Any gold grade > 5 g/t passes (significant)
        if any(g >= 5.0 for g in grades.get("gold_gpt", [])):
            return True

        return False

    def _format_grade_tag(self, grades):
        """Compact tag string from extracted grades, e.g. '1.5% U3O8 | 8.0m intercept'.
        Deduped: the same value often appears in both title and description."""
        if not grades:
            return ""
        tags = []
        for g in dict.fromkeys(grades.get("uranium_pct", [])):
            tags.append(f"{g}% U3O8")
        for g in dict.fromkeys(grades.get("intercept_m", [])):
            tags.append(f"{g}m intercept")
        for g in dict.fromkeys(grades.get("gold_gpt", [])):
            tags.append(f"{g} g/t Au")
        return " | ".join(tags)

    def _build_ceo_signal(
        self,
        ticker,
        source,
        via,
        title,
        link,
        date_str,
        text_for_grades,
        source_record_id=None,
    ):
        """Assemble one signal record. Data layer: no emoji/render markup here —
        `verified` is a compatibility alias for `threshold_qualified`; source
        provenance is carried separately and never verifies a user's claim."""
        grades = self._extract_grades(text_for_grades)
        passes_filter = self._passes_drill_filter(grades)
        source_record_verified = bool(
            via == "ceo.ca_api" and source_record_id and date_str
        )
        if via == "ceo.ca_api":
            provenance_status = (
                "direct_api_record"
                if source_record_verified
                else "direct_api_incomplete"
            )
        elif ticker == "SECTOR":
            provenance_status = "external_news_record"
        else:
            provenance_status = "news_proxy_record"
        context_relevant = bool(
            passes_filter or self.CEO_CONTEXT_PATTERN.search(text_for_grades or "")
        )
        content_class = (
            "geology_threshold"
            if passes_filter
            else "context_only"
            if context_relevant
            else "irrelevant_chatter"
        )
        return {
            'source': source,
            'via': via,
            'title': title,
            'link': link,
            'date': date_str,
            'ticker': ticker,
            'grades': grades,
            'source_record_id': source_record_id,
            'source_record_verified': source_record_verified,
            'provenance_status': provenance_status,
            'threshold_qualified': passes_filter,
            'context_relevant': context_relevant,
            'content_class': content_class,
            # Backward-compatible alias; do not use for source health.
            'verified': passes_filter,
            'grade_tag': self._format_grade_tag(grades) if passes_filter else "",
        }

    def _fetch_ceo_ca_channel(self, ticker, limit=5):
        """Fetch recent posts from a ticker's CEO.ca channel via the public
        JSON API. Full post text (not just a headline) feeds the grade regex,
        so `verified` can actually fire when real drill results are posted.
        Raises on transport/HTTP failure so the caller can fall back."""
        channel = ticker.lower()
        url = f"https://new-api.ceo.ca/api/get_spiels?channel={channel}"
        response = requests.get(url, headers=self.headers, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"CEO.ca API returned {response.status_code} for {channel}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"CEO.ca API returned malformed payload for {channel}")
        spiels = payload.get("spiels") or []
        if not isinstance(spiels, list):
            raise RuntimeError(f"CEO.ca API returned malformed spiels for {channel}")
        spiels.sort(key=lambda s: s.get("timestamp") or 0, reverse=True)

        signals = []
        for sp in spiels:
            if len(signals) >= limit:
                break
            text = re.sub(r"\s+", " ", str(sp.get("spiel") or "")).strip()
            if not text:
                continue

            ts = sp.get("timestamp")
            try:
                date_str = (
                    datetime.fromtimestamp(
                        float(ts) / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if ts
                    else ""
                )
            except (TypeError, ValueError, OverflowError, OSError):
                date_str = ""
            spiel_id = str(sp.get("spiel_id") or "").strip()
            link = f"https://ceo.ca/{channel}?{spiel_id}" if spiel_id else f"https://ceo.ca/{channel}"

            signal = self._build_ceo_signal(
                ticker, f'CEO.ca/{ticker}', 'ceo.ca_api',
                text[:250], link, date_str, text,
                source_record_id=spiel_id,
            )
            signal['author'] = sp.get('name')
            signal['votes'] = sp.get('votes')
            signals.append(signal)
        return signals

    def _fetch_ceo_ca_proxy(self, ticker):
        """Fallback: CEO.ca posts surfaced through Google News RSS (title-only,
        so grades rarely verify). Used only when the direct API call fails."""
        query = f'site:ceo.ca {ticker} ("% U3O8" OR "g/t Au" OR "intercept" OR "assay" OR "drill" OR "core sample")'
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        signals = []
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('.//item')[:5]:
                    title = item.find('title').text or ""
                    link = item.find('link').text or ""
                    pub_date = item.find('pubDate')
                    date_str = pub_date.text if pub_date is not None else ""
                    desc_el = item.find('description')
                    desc = desc_el.text if desc_el is not None else ""
                    signals.append(self._build_ceo_signal(
                        ticker, f'CEO.ca/{ticker}', 'google_news_proxy',
                        title, link, date_str, f"{title} {desc}",
                    ))
        except Exception as e:
            logger.error(f"Error fetching CEO.ca proxy for {ticker}: {e}")
        return signals

    def _fetch_uranium_sector_news(self):
        """General uranium geology/permit news via Google News (sector-wide,
        not CEO.ca — kept as its own source label)."""
        geology_query = 'uranium mine ("% U3O8" OR "intercept" OR "assay results" OR "drill results" OR "core sample" OR "permit delay")'
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(geology_query)}&hl=en-US&gl=US&ceid=US:en"
        signals = []
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('.//item')[:5]:
                    title = item.find('title').text or ""
                    link = item.find('link').text or ""
                    desc_el = item.find('description')
                    desc = desc_el.text if desc_el is not None else ""
                    pub_date = item.find('pubDate')
                    date_str = pub_date.text if pub_date is not None else ""
                    signals.append(self._build_ceo_signal(
                        'SECTOR', 'Uranium/Mining', 'google_news_proxy',
                        title, link, date_str, f"{title} {desc}",
                    ))
        except Exception as e:
            logger.error(f"Error fetching uranium geology news: {e}")
        return signals

    def get_ceo_ca_signals(self):
        """
        CEO.ca channel posts for the uranium watchlist, fetched from CEO.ca's
        own public JSON API (the endpoint the site itself loads). Falls back
        to the old Google News proxy per channel if the API call fails —
        check `via` per signal ('ceo.ca_api' vs 'google_news_proxy').

        Screening rules:
        - Regex extraction for real decimal grade values on full post text
        - Threshold filter: ≥1% U3O8, >5m intercept, >5 g/t Au
        - `threshold_qualified` = passed the drill filter
        - `source_record_verified` = direct API post ID + timestamp present
        - `verified` remains a compatibility alias for threshold qualification
        - Irrelevant channel banter is excluded from downstream rendering
        - All threshold-qualified rows kept; context-only rows capped at the
          MAX_UNVERIFIED_PER_SOURCE newest per channel/feed
        """
        raw_signals = []
        self._dropped_ceo_signals = []
        self._dropped_ceo_quality = []
        self.ceo_ca_health = {
            "expected_channels": list(self.uranium_tickers),
            "api_channels": [],
            "fallback_channels": [],
        }

        for ticker in self.uranium_tickers:
            try:
                channel_signals = self._fetch_ceo_ca_channel(ticker, limit=5)
                self.ceo_ca_health["api_channels"].append(ticker)
            except Exception as e:
                logger.error(f"CEO.ca API failed for {ticker}; using Google News proxy fallback: {e}")
                self.ceo_ca_health["fallback_channels"].append({
                    "ticker": ticker,
                    "error": str(e)[:240],
                })
                channel_signals = self._fetch_ceo_ca_proxy(ticker)
            raw_signals.extend(channel_signals)

        raw_signals.extend(self._fetch_uranium_sector_news())

        signals, self._dropped_ceo_signals = split_fresh_records(
            raw_signals, "date", CEO_CA_MAX_AGE_DAYS, now=self.now_utc
        )
        fresh_count = len(signals)
        quality_signals = []
        for signal in signals:
            if signal.get("context_relevant"):
                quality_signals.append(signal)
            else:
                self._dropped_ceo_quality.append({
                    **signal,
                    "as_of": signal.get("date"),
                    "drop_reason": "irrelevant_chatter",
                })
        signals = []
        context_counts = {}
        for signal in quality_signals:
            source = signal.get("source")
            if signal.get("threshold_qualified"):
                signals.append(signal)
                continue
            kept_for_source = context_counts.get(source, 0)
            if kept_for_source < self.MAX_UNVERIFIED_PER_SOURCE:
                signals.append(signal)
                context_counts[source] = kept_for_source + 1
            else:
                self._dropped_ceo_quality.append({
                    **signal,
                    "as_of": signal.get("date"),
                    "drop_reason": "context_cap",
                })

        # Sort: verified signals first
        signals.sort(
            key=lambda s: (
                0 if s.get("threshold_qualified") else 1,
                0 if s.get("source_record_verified") else 1,
            )
        )

        direct_records = sum(
            1 for s in signals if s.get("source_record_verified")
        )
        qualified = sum(
            1 for s in signals if s.get("threshold_qualified")
        )
        self.ceo_ca_health.update({
            "records_raw": len(raw_signals),
            "records_fresh": fresh_count,
            "records_rendered": len(signals),
            "direct_api_records_seen": sum(
                1 for signal in raw_signals
                if signal.get("via") == "ceo.ca_api"
            ),
            "direct_api_records_verified": sum(
                1 for signal in raw_signals
                if signal.get("source_record_verified")
            ),
            "incomplete_direct_api_records": sum(
                1 for signal in raw_signals
                if signal.get("via") == "ceo.ca_api"
                and not signal.get("source_record_verified")
            ),
            "rendered_direct_api_records_verified": direct_records,
            "threshold_qualified_records": qualified,
            "irrelevant_chatter_dropped": sum(
                1 for row in self._dropped_ceo_quality
                if row.get("drop_reason") == "irrelevant_chatter"
            ),
            "context_cap_dropped": sum(
                1 for row in self._dropped_ceo_quality
                if row.get("drop_reason") == "context_cap"
            ),
            "freshness_dropped": len(self._dropped_ceo_signals),
        })
        print(f"[ResearchAgent] Gathered {len(signals)} CEO.ca/Uranium indicators "
              f"({qualified} geology-qualified, "
              f"{direct_records} verified direct-API records)")
        return signals

    def get_dropped_ceo_signals(self):
        """Stale/undated rows removed by the seven-day freshness gate."""
        return [dict(row) for row in self._dropped_ceo_signals]

    def get_dropped_ceo_quality(self):
        """Fresh CEO.ca rows excluded as non-substantive channel chatter."""
        return [dict(row) for row in self._dropped_ceo_quality]

    def get_ceo_ca_health(self):
        """Bounded provenance/coverage diagnostics for export health."""
        return {
            **self.ceo_ca_health,
            "expected_channels": list(
                self.ceo_ca_health.get("expected_channels", [])
            ),
            "api_channels": list(
                self.ceo_ca_health.get("api_channels", [])
            ),
            "fallback_channels": [
                dict(row)
                for row in self.ceo_ca_health.get("fallback_channels", [])
            ],
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # CLINICALTRIALS.GOV (Public API v2)
    # ═══════════════════════════════════════════════════════════════════
    def get_clinical_trials(self):
        """
        Fetches CRISPR and gene therapy clinical trials from ClinicalTrials.gov API.
        Focus: Vertex/CRISPR Therapeutics joint trials, gene editing therapies.
        """
        trials = []
        
        # Search queries for biotech trials
        queries = [
            ("CRISPR", "Vertex"),           # Vertex + CRISPR joint
            ("CRISPR", ""),                  # All CRISPR trials
            ("gene editing", ""),           # Gene editing trials
            ("sickle cell", "CRISPR"),      # Casgevy-related
        ]
        
        for term, sponsor in queries:
            try:
                url = "https://clinicaltrials.gov/api/v2/studies"
                params = {
                    'query.term': term,
                    'pageSize': 10,
                    'sort': 'LastUpdatePostDate:desc'  # Most recently updated
                }
                if sponsor:
                    params['query.spons'] = sponsor
                
                response = requests.get(url, params=params, headers=self.headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    studies = data.get('studies', [])
                    
                    for study in studies[:5]:  # Top 5 per query
                        protocol = study.get('protocolSection', {})
                        id_module = protocol.get('identificationModule', {})
                        status_module = protocol.get('statusModule', {})
                        sponsor_module = protocol.get('sponsorCollaboratorsModule', {})
                        
                        nct_id = id_module.get('nctId', 'N/A')
                        title = id_module.get('briefTitle', 'No title')
                        status = status_module.get('overallStatus', 'Unknown')
                        
                        # Get lead sponsor
                        lead_sponsor = sponsor_module.get('leadSponsor', {}).get('name', 'Unknown')
                        
                        # Get phase
                        design_module = protocol.get('designModule', {})
                        phases = design_module.get('phases', [])
                        phase = phases[0] if phases else 'N/A'

                        trials.append({
                            'nct_id': nct_id,
                            'title': title[:150],
                            'status': status,
                            'sponsor': lead_sponsor,
                            'phase': phase,
                            'search_term': term,
                            # Registry-diff inputs (Sniper 3a)
                            'primary_completion_date': status_module.get(
                                'primaryCompletionDateStruct', {}).get('date'),
                            'enrollment': design_module.get(
                                'enrollmentInfo', {}).get('count'),
                            # Source time for the registry record: when CT.gov
                            # last posted a change. Not the trial's dates —
                            # those are the event schedule, not the as_of.
                            'last_update_posted': status_module.get(
                                'lastUpdatePostDateStruct', {}).get('date'),
                            'link': f"https://clinicaltrials.gov/study/{nct_id}"
                        })
                        
            except Exception as e:
                logger.error(f"Error fetching trials for '{term}': {e}")
        
        # Deduplicate by NCT ID
        seen = set()
        unique_trials = []
        for t in trials:
            if t['nct_id'] not in seen:
                seen.add(t['nct_id'])
                unique_trials.append(t)
        
        print(f"[ResearchAgent] Gathered {len(unique_trials)} clinical trials")
        return unique_trials

    # ═══════════════════════════════════════════════════════════════════
    # FDA PDUFA CATALYST SCANNER
    # ═══════════════════════════════════════════════════════════════════
    def get_pdufa_catalysts(self, days_ahead=60):
        """
        Scans for upcoming PDUFA dates / FDA catalysts via two sources:
        1. ClinicalTrials.gov — Phase 3 trials completing in the next N days
        2. Google News RSS — "PDUFA" / "FDA approval date" announcements
        
        Priority tickers (CRSP, NTLA, RGNX) get extra search queries.
        Returns list of catalysts sorted by: watchlist first, then date.
        """
        catalysts = []
        today = self.now_naive_utc
        cutoff = today + timedelta(days=days_ahead)
        
        # ── Source 1: ClinicalTrials.gov Phase 3 nearing completion ──
        phase3_queries = [
            "PDUFA",
            "FDA approval",
            "new drug application",
            "biologics license application",
            "gene therapy",
            "CRISPR",
            "gene editing",
        ]
        
        seen_ncts = set()
        
        for query in phase3_queries:
            try:
                url = "https://clinicaltrials.gov/api/v2/studies"
                params = {
                    'query.term': query,
                    'filter.advanced': 'AREA[Phase](PHASE3)',
                    'pageSize': 20,
                    'sort': 'LastUpdatePostDate:desc',
                }
                response = requests.get(url, params=params, headers=self.headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    for study in data.get('studies', []):
                        protocol = study.get('protocolSection', {})
                        id_mod = protocol.get('identificationModule', {})
                        status_mod = protocol.get('statusModule', {})
                        sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
                        design_mod = protocol.get('designModule', {})
                        
                        nct_id = id_mod.get('nctId', '')
                        if nct_id in seen_ncts:
                            continue
                        seen_ncts.add(nct_id)
                        
                        title = id_mod.get('briefTitle', 'No title')
                        status = status_mod.get('overallStatus', 'Unknown')
                        sponsor = sponsor_mod.get('leadSponsor', {}).get('name', 'Unknown')
                        phases = design_mod.get('phases', [])
                        phase = phases[0] if phases else 'N/A'
                        
                        # Get primary completion date
                        completion_info = status_mod.get('completionDateStruct', {})
                        completion_date_str = completion_info.get('date', '')
                        
                        # Also check primary completion date
                        primary_info = status_mod.get('primaryCompletionDateStruct', {})
                        primary_date_str = primary_info.get('date', '')
                        
                        # Use whichever is sooner
                        target_date = primary_date_str or completion_date_str
                        
                        # Parse the date (format: "YYYY-MM-DD" or "YYYY-MM" or "YYYY")
                        parsed_date = None
                        if target_date:
                            for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
                                try:
                                    parsed_date = datetime.strptime(target_date, fmt)
                                    break
                                except ValueError:
                                    continue
                        
                        # Check if within our window
                        days_until = None
                        in_window = False
                        if parsed_date:
                            delta = (parsed_date - today).days
                            days_until = delta
                            if -30 <= delta <= days_ahead:  # Include recently passed (last 30 days)
                                in_window = True
                        
                        # Check if this is a priority ticker
                        is_priority = any(t.lower() in sponsor.lower() or t.lower() in title.lower() 
                                        for t in self.priority_biotech + self.biotech_sponsors)
                        
                        # Include if: in window OR priority ticker OR actively completing
                        if in_window or is_priority or status in ('COMPLETED', 'ACTIVE_NOT_RECRUITING'):
                            catalysts.append({
                                'nct_id': nct_id,
                                'title': title[:150],
                                'sponsor': sponsor,
                                'status': status,
                                'phase': phase,
                                'target_date': target_date or 'TBD',
                                'days_until': days_until,
                                'is_priority': is_priority,
                                # Registry-diff inputs (Sniper 3a)
                                'primary_completion_date': primary_date_str or None,
                                'enrollment': design_mod.get(
                                    'enrollmentInfo', {}).get('count'),
                                'last_update_posted': status_mod.get(
                                    'lastUpdatePostDateStruct', {}).get('date'),
                                'source': 'ClinicalTrials.gov',
                                'link': f"https://clinicaltrials.gov/study/{nct_id}",
                            })
            except Exception as e:
                logger.error(f"Error in PDUFA trial scan for '{query}': {e}")

        # ── Source 2: Google News RSS for PDUFA announcements ──
        pdufa_queries = [
            '"PDUFA" FDA approval date 2026',
            '"FDA decision" biotech drug approval',
            '"NDA" OR "BLA" FDA 2026 approval',
        ]
        
        # Priority tickers get dedicated queries
        for ticker in self.priority_biotech:
            pdufa_queries.append(f'{ticker} FDA approval OR PDUFA OR "NDA" OR "BLA"')
        
        for query in pdufa_queries:
            try:
                url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
                response = requests.get(url, headers=self.headers, timeout=10)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    for item in root.findall('.//item')[:5]:
                        title = item.find('title').text or ''
                        link = item.find('link').text or ''
                        pub_date = item.find('pubDate')
                        date_str = pub_date.text[:16] if pub_date is not None else ''
                        
                        # Check if this mentions a priority ticker
                        is_priority = any(t.lower() in title.lower() for t in self.priority_biotech)
                        
                        catalysts.append({
                            'nct_id': '',
                            'title': title[:150],
                            'sponsor': '',
                            'status': 'NEWS',
                            'phase': '',
                            'target_date': date_str,
                            'days_until': None,
                            'is_priority': is_priority,
                            'source': 'Google News/FDA',
                            'link': link,
                        })
            except Exception as e:
                logger.error(f"Error fetching PDUFA news for '{query}': {e}")
        
        # Sort: priority first, then by days_until (soonest first)
        catalysts.sort(key=lambda c: (
            0 if c['is_priority'] else 1,
            c['days_until'] if c['days_until'] is not None else 9999,
        ))
        
        # Deduplicate by title similarity (exact match)
        seen_titles = set()
        unique = []
        for c in catalysts:
            key = c['title'][:80].lower()
            if key not in seen_titles:
                seen_titles.add(key)
                unique.append(c)
        
        print(f"[ResearchAgent] Gathered {len(unique)} PDUFA catalysts")
        return unique

    # ═══════════════════════════════════════════════════════════════════
    # CASH RUNWAY MEASUREMENT
    # ═══════════════════════════════════════════════════════════════════
    def check_cash_runway(self, ticker):
        """
        Checks a company's financial health using yfinance quarterly data.
        
        Returns dict with:
          - cash_and_equivalents: Total cash + short-term investments
          - total_debt: Total debt obligations
          - quarterly_burn: Avg quarterly cash burn (operating cash flow)
          - runway_quarters: Estimated quarters of cash remaining
          - risk_level: GREEN (>6Q), YELLOW (4-6Q), RED (<4Q)
          - market_cap: Current market cap
        """
        if not HAS_YFINANCE:
            return {'error': 'yfinance not installed', 'ticker': ticker}
        
        try:
            stock = yf.Ticker(ticker)
            
            # Get quarterly balance sheet
            bs = stock.quarterly_balance_sheet
            if bs is None or bs.empty:
                return {'error': 'No balance sheet data', 'ticker': ticker}
            
            # Get quarterly cash flow
            cf = stock.quarterly_cashflow
            
            # Get the most recent quarter
            latest = bs.iloc[:, 0]  # Most recent column
            
            # Extract cash position (try multiple field names)
            cash = 0
            cash_fields = [
                'Cash And Cash Equivalents',
                'Cash Cash Equivalents And Short Term Investments',
                'Cash Equivalents',
                'Cash And Short Term Investments',
                'Cash Financial',
            ]
            for field in cash_fields:
                if field in latest.index and latest[field] is not None:
                    try:
                        val = float(latest[field])
                        if val > cash:
                            cash = val
                    except (ValueError, TypeError):
                        continue
            
            # Extract total debt
            total_debt = 0
            debt_fields = ['Total Debt', 'Long Term Debt', 'Total Non Current Liabilities Net Minority Interest']
            for field in debt_fields:
                if field in latest.index and latest[field] is not None:
                    try:
                        total_debt = float(latest[field])
                        break
                    except (ValueError, TypeError):
                        continue
            
            # Calculate quarterly burn rate from operating cash flow
            quarterly_burn = 0
            if cf is not None and not cf.empty:
                ocf_row = None
                for field in ['Operating Cash Flow', 'Free Cash Flow', 'Cash Flow From Continuing Operating Activities']:
                    if field in cf.index:
                        ocf_row = cf.loc[field]
                        break
                
                if ocf_row is not None:
                    # Average of last 4 quarters (or however many we have)
                    valid_vals = [float(v) for v in ocf_row.values[:4] if v is not None]
                    if valid_vals:
                        quarterly_burn = sum(valid_vals) / len(valid_vals)
            
            # Calculate runway
            runway_quarters = None
            if quarterly_burn < 0 and cash > 0:
                # Company is burning cash — calculate quarters until empty
                runway_quarters = round(cash / abs(quarterly_burn), 1)
            elif quarterly_burn >= 0:
                # Company is cash-flow positive — not at risk
                runway_quarters = float('inf')
            
            # Risk level
            if runway_quarters is None:
                risk_level = "UNKNOWN"
            elif runway_quarters == float('inf'):
                risk_level = "GREEN"  # Cash flow positive
            elif runway_quarters > 6:
                risk_level = "GREEN"
            elif runway_quarters >= 4:
                risk_level = "YELLOW"  # Caution zone
            else:
                risk_level = "RED"  # Short estimated runway
            
            # Market cap
            info = stock.info or {}
            market_cap = info.get('marketCap', 0)
            
            # Format large numbers
            def fmt_money(val):
                if val is None or val != val:  # None or NaN
                    return "N/A"
                if abs(val) >= 1e9:
                    return f"${val/1e9:.1f}B"
                elif abs(val) >= 1e6:
                    return f"${val/1e6:.1f}M"
                else:
                    return f"${val:,.0f}"
            
            result = {
                'ticker': ticker,
                'cash_and_equivalents': cash,
                'cash_formatted': fmt_money(cash),
                'total_debt': total_debt,
                'debt_formatted': fmt_money(total_debt),
                'quarterly_burn': quarterly_burn,
                'burn_formatted': fmt_money(quarterly_burn),
                'runway_quarters': runway_quarters if runway_quarters != float('inf') else 999,
                'risk_level': risk_level,
                'market_cap': market_cap,
                'mcap_formatted': fmt_money(market_cap),
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking cash runway for {ticker}: {e}")
            return {'error': str(e), 'ticker': ticker}

    # ═══════════════════════════════════════════════════════════════════
    # PDUFA + CASH RUNWAY CROSS-REFERENCE
    # ═══════════════════════════════════════════════════════════════════
    def get_pdufa_with_financials(self, days_ahead=60):
        """
        Master method: finds PDUFA catalysts, then checks each company's
        cash runway. Flags companies with upcoming catalysts but low cash.

        Returns:
          - catalysts: list of PDUFA catalysts
          - financials: dict of {ticker: cash_runway_data} for checked companies
          - alerts: list of RED/YELLOW flagged companies
        """
        catalysts = self.get_pdufa_catalysts(days_ahead)
        
        # Build a unique list of tickers to check financials for
        # Priority tickers always get checked
        tickers_to_check = set(self.priority_biotech)
        
        # Also extract tickers mentioned in catalyst sponsor names
        # (shared SPONSOR_TICKER_MAP — same attribution the exporter uses).
        # All matches get a financials check (joint trials name two sponsors);
        # mapped_ticker keeps the first match, same rule as resolve_ticker.
        for catalyst in catalysts:
            sponsor_lower = catalyst.get('sponsor', '').lower()
            title_lower = catalyst.get('title', '').lower()
            for name, tick in self.SPONSOR_TICKER_MAP.items():
                if name in sponsor_lower or name in title_lower:
                    tickers_to_check.add(tick)
                    catalyst.setdefault('mapped_ticker', tick)
        
        # Check cash runway for each unique ticker
        financials = {}
        for ticker in tickers_to_check:
            print(f"[ResearchAgent] Checking cash runway for {ticker}...")
            result = self.check_cash_runway(ticker)
            if 'error' not in result:
                financials[ticker] = result
        
        # Build compatibility `alerts` list of cash-runway flags
        alerts = []
        for ticker, data in financials.items():
            if data['risk_level'] in ('RED', 'YELLOW'):
                risk_emoji = "🔴" if data['risk_level'] == 'RED' else "🟡"
                runway_str = f"{data['runway_quarters']}Q" if data['runway_quarters'] < 999 else "N/A"
                alerts.append({
                    'ticker': ticker,
                    'risk_level': data['risk_level'],
                    'risk_emoji': risk_emoji,
                    'cash': data['cash_formatted'],
                    'burn': data['burn_formatted'],
                    'runway': runway_str,
                    'debt': data['debt_formatted'],
                    'message': f"{risk_emoji} {ticker}: {data['risk_level']} RUNWAY FLAG — Cash: {data['cash_formatted']}, "
                              f"Burn: {data['burn_formatted']}/Q, Runway: {runway_str}, Debt: {data['debt_formatted']}"
                })
        
        print(f"[ResearchAgent] Cash runway checked for {len(financials)} tickers, {len(alerts)} runway flags")
        return {
            'catalysts': catalysts,
            'financials': financials,
            'alerts': alerts,
        }

    # ═══════════════════════════════════════════════════════════════════
    # COMBINED DUMP
    # ═══════════════════════════════════════════════════════════════════
    def get_full_research_dump(self):
        """Returns all research data as formatted strings for the daily dump."""
        output = []
        
        # CEO.ca / Uranium
        output.append("=" * 50)
        output.append("CEO.CA / URANIUM INTELLIGENCE")
        output.append("Tickers: UUUU, CCJ, NXE, DNN")
        output.append("=" * 50)
        
        ceo_signals = self.get_ceo_ca_signals()
        for s in ceo_signals:
            provenance = (
                "DIRECT_API"
                if s.get("source_record_verified")
                else s.get("provenance_status", "UNKNOWN_PROVENANCE").upper()
            )
            classification = (
                "GEOLOGY_THRESHOLD"
                if s.get("threshold_qualified")
                else "CONTEXT_ONLY"
            )
            output.append(
                f"[{s['source']}] [{provenance}] "
                f"[{classification}] {s['title']}"
            )
            if s.get('grade_tag'):
                output.append(f"   GRADE: {s['grade_tag']}")
            output.append(f"   Link: {s['link']}")
        
        # Clinical Trials
        output.append("")
        output.append("=" * 50)
        output.append("CLINICALTRIALS.GOV / CRISPR & GENE THERAPY")
        output.append("Focus: Vertex, CRISPR Therapeutics, Gene Editing")
        output.append("=" * 50)
        
        trials = self.get_clinical_trials()
        for t in trials:
            status_emoji = "🟢" if t['status'] == "RECRUITING" else "🟡" if "ACTIVE" in t['status'] else "⚪"
            output.append(f"{status_emoji} [{t['nct_id']}] {t['title']}")
            output.append(f"   Status: {t['status']} | Phase: {t['phase']} | Sponsor: {t['sponsor']}")
            output.append(f"   Link: {t['link']}")
        
        # PDUFA Catalysts + Cash Runway
        output.append("")
        output.append("=" * 50)
        output.append("FDA PDUFA CATALYSTS + CASH RUNWAY")
        output.append("Priority: CRSP, NTLA, RGNX | Window: 60 days")
        output.append("Risk: GREEN (>6Q) | YELLOW (4-6Q) | RED (<4Q)")
        output.append("=" * 50)
        
        pdufa_data = self.get_pdufa_with_financials()
        
        # Print runway flags first
        if pdufa_data['alerts']:
            output.append("")
            output.append("--- CASH RUNWAY FLAGS ---")
            for alert in pdufa_data['alerts']:
                output.append(alert['message'])
        
        # Print financial summaries
        if pdufa_data['financials']:
            output.append("")
            output.append("--- CASH RUNWAY SUMMARY ---")
            for ticker, data in sorted(pdufa_data['financials'].items()):
                risk_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(data['risk_level'], "⚪")
                runway = f"{data['runway_quarters']}Q" if data['runway_quarters'] < 999 else "CF+"
                output.append(
                    f"{risk_emoji} {ticker}: Cash {data['cash_formatted']} | "
                    f"Burn {data['burn_formatted']}/Q | Runway {runway} | "
                    f"Debt {data['debt_formatted']} | MCap {data['mcap_formatted']}"
                )
        
        # Print catalysts
        if pdufa_data['catalysts']:
            output.append("")
            output.append("--- UPCOMING CATALYSTS ---")
            for c in pdufa_data['catalysts'][:30]:  # Cap at 30
                priority_tag = " [WATCHLIST]" if c['is_priority'] else ""
                days_str = f"({c['days_until']}d)" if c['days_until'] is not None else ""
                output.append(
                    f"[{c['source']}] {c['title']}{priority_tag} {days_str}"
                )
                if c.get('sponsor'):
                    output.append(f"   Sponsor: {c['sponsor']} | Status: {c['status']} | Phase: {c['phase']}")
                if c.get('link'):
                    output.append(f"   Link: {c['link']}")
        
        return output


# Quick test
if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    agent = ResearchAgent()
    
    print("\n" + "=" * 60)
    print("RESEARCH AGENT - LIVE DUMP")
    print("=" * 60)
    
    dump = agent.get_full_research_dump()
    for line in dump:
        print(line)
