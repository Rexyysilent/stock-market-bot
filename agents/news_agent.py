"""Diversified market-headline orchestration.

Official sources, FMP, Alpha Vantage News and GDELT form the primary pool.
Google News is fetched lazily only when the primary pool cannot fill the
publisher-diverse section. Provider adapters own transport/parsing; this class
owns relevance, freshness, syndication dedupe and deterministic selection.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import logging
import re
import unicodedata

from config import (
    ALL_TICKERS,
    ALPHA_VANTAGE_API_KEY,
    FMP_API_KEY,
    HEADLINE_GDELT_ENABLED,
    HEADLINE_GOOGLE_FALLBACK_ENABLED,
    HEADLINE_GOOGLE_MAX_SELECTED,
    HEADLINE_MAX_PER_PUBLISHER,
    HEADLINE_OFFICIAL_FEEDS,
    HEADLINE_REQUEST_TIMEOUT_SECONDS,
    HEADLINE_TARGET_COUNT,
    MACRO_KEYWORDS,
    TICKER_ALIASES,
)
from agents.news_providers import (
    AlphaVantageNewsProvider,
    FMPNewsProvider,
    BatchedGDELTNewsProvider,
    GoogleNewsProvider,
    OfficialFeedProvider,
    ProviderResult,
)
from timeutil import to_utc_z

logger = logging.getLogger("NewsAgent")


class NewsAgent:
    MIN_RELEVANCE_SCORE = 1
    HEADLINES_MAX_AGE_DAYS = 3
    PRIMARY_QUERY = "Stock Market OR Global Economy OR Finance"
    FALLBACK_QUERIES = (
        "stock market today OR S&P 500 OR Nasdaq OR Dow",
    )
    TARGET_COUNT = HEADLINE_TARGET_COUNT
    MAX_PER_PUBLISHER = HEADLINE_MAX_PER_PUBLISHER
    GOOGLE_MAX_SELECTED = HEADLINE_GOOGLE_MAX_SELECTED
    SOURCE_PRIORITY = {
        "official_central_bank": 0,
        "official_exchange": 0,
        "official_regulator": 0,
        "official": 0,
        "aggregator": 1,
        "api_news_discovery": 1,
        "global_discovery": 2,
        "press_release": 3,
    }
    TREASURY_PATTERNS = (
        r"\bu\.?s\.?\s+treasur(?:y|ies)\b",
        r"\btreasury\s+(?:department|secretary|yield|yields|bond|bonds|"
        r"note|notes|bill|bills|auction|auctions|curve|market|markets|"
        r"security|securities|debt)\b",
        r"\b(?:yield|yields|bond|bonds|note|notes|bill|bills|auction|"
        r"auctions|curve)\s+(?:on\s+)?treasur(?:y|ies)\b",
        r"\b(?:2|5|10|20|30)[- ]year\s+treasur(?:y|ies)\b",
    )
    LANE_PRIORITY = {"universe": 0, "macro": 1, "discovery": 2}
    EXCHANGE_LISTING_PATTERN = re.compile(
        r"(?i)(?:\(\s*)?\b(?:NASDAQ|NYSE|AMEX)\s*:\s*"
        r"([A-Z][A-Z0-9.=-]{0,11})(?:\s*\))?"
    )
    COMPANY_CONTEXT_ONLY_KEYWORDS = {
        "earnings",
        "layoff",
        "fda",
        "semiconductor",
        "uranium",
    }
    VERTICAL_KEYWORDS = {
        "uranium_nuclear": (
            "uranium", "nuclear", "reactor", "fuel cycle", "enrichment",
        ),
        "semiconductors": (
            "semiconductor", "chipmaker", "chip makers", "foundry",
        ),
        "biotechnology": (
            "biotech", "pharmaceutical", "clinical trial", "drug", "fda",
        ),
        "defense_aerospace": (
            "defense", "aerospace", "missile", "aircraft",
        ),
        "mining_metals": (
            "copper", "mine", "miner", "mining", "critical mineral",
        ),
    }
    AUTHORITY_SCORES = {
        "official_central_bank": 3,
        "official_exchange": 3,
        "official_regulator": 3,
        "official": 3,
        "aggregator": 2,
        "api_news_discovery": 1,
        "global_discovery": 1,
        "press_release": 1,
    }
    IMPACT_PATTERNS = (
        (r"\b(?:bankrupt(?:cy)?|merger|takeover|acquisition)\b", 2),
        (r"\b(?:financing|offering|strategic investor|project equity)\b", 2),
        (r"\b(?:approval|approved|rejection|rejected|clinical hold)\b", 2),
        (r"\b(?:trading halt|systems halt|market-wide halt|recall)\b", 2),
        (r"\b(?:guidance cut|cuts guidance|raises guidance)\b", 2),
        (r"\b(?:surges?|jumps?|rips?|plunges?|tumbles?)\b", 2),
        (r"\b(?:downgrade|upgrade|insider sale|share sale)\b", 1),
        (r"\b(?:strategic shift|earnings preview|quarterly results)\b", 1),
    )

    def __init__(self, now=None, providers=None, google_provider=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self.base_url = (
            "https://news.google.com/rss/search?q={query}"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            )
        }
        self._providers = (
            list(providers) if providers is not None else self._default_providers()
        )
        self._google_provider = google_provider or GoogleNewsProvider(
            primary_query=self.PRIMARY_QUERY,
            fallback_queries=self.FALLBACK_QUERIES,
            now=self.now_utc,
            timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
        )
        self._google_enabled = HEADLINE_GOOGLE_FALLBACK_ENABLED
        self._scored_headlines = []
        self._dropped_headlines = []
        self._pool_diagnostics = self._summarize_raw_pool(
            [], query="multi-provider", now=self.now_utc
        )

    @staticmethod
    def _build_gdelt_queries(max_query_chars=190):
        terms = [
            "stock market",
            "Federal Reserve",
            "inflation",
            "tariff",
            "earnings",
            "semiconductor",
            "uranium",
            "bitcoin",
            "oil",
        ]
        terms += [aliases[0] for aliases in TICKER_ALIASES.values() if aliases]
        unique = list(dict.fromkeys(term.casefold() for term in terms))
        rendered = [
            f'"{term}"' if " " in term else term for term in unique
        ]
        suffix = " sourcelang:english"

        def build_query(batch):
            return f"({' OR '.join(batch)}){suffix}"

        batches = []
        current = []
        for term in rendered:
            candidate = build_query(current + [term])
            if current and len(candidate) > max_query_chars:
                batches.append(build_query(current))
                current = [term]
                if len(build_query(current)) > max_query_chars:
                    raise ValueError("GDELT term exceeds query-size limit")
            else:
                current.append(term)
        if current:
            batches.append(build_query(current))
        if not batches:
            raise ValueError("GDELT query universe is empty")
        return tuple(batches)

    def _default_providers(self):
        feeds = []
        for item in HEADLINE_OFFICIAL_FEEDS:
            spec = dict(item)
            spec["parser"] = (
                "nasdaq_halts"
                if spec.pop("feed_kind", None) == "nasdaq_halts"
                else "generic"
            )
            feeds.append(spec)
        providers = [
            OfficialFeedProvider(
                feeds,
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
            FMPNewsProvider(
                FMP_API_KEY,
                tickers=[
                    ticker for ticker in ALL_TICKERS
                    if not ticker.startswith("^") and not ticker.endswith("=F")
                ],
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
            AlphaVantageNewsProvider(
                ALPHA_VANTAGE_API_KEY,
                topics=("financial_markets",),
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
        ]
        if HEADLINE_GDELT_ENABLED:
            providers.append(
                BatchedGDELTNewsProvider(
                    self._build_gdelt_queries(),
                    now=self.now_utc,
                    timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
                )
            )
        return providers

    @staticmethod
    def _safe_provider_error(provider, exc):
        message = str(exc)
        for candidate in (
            getattr(provider, "api_key", None),
            FMP_API_KEY,
            ALPHA_VANTAGE_API_KEY,
        ):
            if candidate:
                message = message.replace(str(candidate), "[redacted]")
        message = re.sub(
            r"(?i)(apikey|api_key|token|secret)=([^&\s]+)",
            r"\1=[redacted]",
            message,
        )
        return message[:200]
    def _fetch_primary_results(self):

        if not self._providers:
            return []
        indexed = {}
        with ThreadPoolExecutor(max_workers=min(6, len(self._providers))) as pool:
            futures = {
                pool.submit(provider.fetch): idx
                for idx, provider in enumerate(self._providers)
            }
            for future in as_completed(futures):
                idx = futures[future]
                provider = self._providers[idx]
                try:
                    result = future.result()
                    if not isinstance(result, ProviderResult):
                        raise TypeError("provider did not return ProviderResult")
                except Exception as exc:
                    result = ProviderResult(
                        getattr(provider, "name", type(provider).__name__),
                        "error",
                        [],
                        error=(
                            "unhandled_provider_error: "
                            f"{self._safe_provider_error(provider, exc)}"
                        ),
                    )
                indexed[idx] = result
        return [indexed[idx] for idx in sorted(indexed)]

    def get_global_headlines(self):
        primary_results = self._fetch_primary_results()
        results = list(primary_results)
        raw_primary = [
            dict(row)
            for result in primary_results
            for row in result.records
        ]
        primary = self._process_pool(raw_primary)

        google_fallback_used = False
        if (
            self._google_enabled
            and len(primary["selected"]) < self.TARGET_COUNT
        ):
            google_fallback_used = True
            try:
                google_result = self._google_provider.fetch()
                if not isinstance(google_result, ProviderResult):
                    raise TypeError("Google provider did not return ProviderResult")
            except Exception as exc:
                google_result = ProviderResult(
                    "Google News",
                    "error",
                    [],
                    error=(
                        "unhandled_provider_error: "
                        f"{self._safe_provider_error(self._google_provider, exc)}"
                    ),
                )
            results.append(google_result)

        raw = [
            dict(row)
            for result in results
            for row in result.records
        ]
        processed = self._process_pool(raw)
        if (
            google_fallback_used
            and len(processed["selected"]) < self.TARGET_COUNT
            and results
            and results[-1].name == "Google News"
            and not results[-1].metadata.get("fallback_used")
        ):
            attempts = list(results[-1].metadata.get("attempts") or [])
            extra_records = []
            for query in self.FALLBACK_QUERIES:
                rows, error = self._google_provider.fetch_query(query, 40)
                attempts.append({
                    "query": query,
                    "record_count": len(rows),
                    "error": error,
                })
                extra_records.extend(rows)
                if rows:
                    break
            if extra_records:
                previous = results[-1]
                results[-1] = ProviderResult(
                    previous.name,
                    "ok",
                    previous.records + extra_records,
                    metadata={
                        **previous.metadata,
                        "fallback_used": True,
                        "query": query,
                        "attempts": attempts,
                        "eligibility_fallback_used": True,
                    },
                )
                raw = [dict(row) for result in results for row in result.records]
                processed = self._process_pool(raw)
            else:
                results[-1].metadata["attempts"] = attempts
                results[-1].metadata["eligibility_fallback_used"] = True
        self._scored_headlines = processed["selected"]
        self._dropped_headlines = (
            processed["relevance_dropped"]
            + processed["stale_dropped"]
            + processed["duplicate_dropped"]
            + processed["cap_dropped"]
        )

        provider_health = [result.health() for result in results]
        partial_failures = [
            row["name"]
            for row in provider_health
            if row["configured"] and (
                row["status"] == "error" or row.get("partial_failures", 0)
            )
        ]
        configured = [row for row in provider_health if row["configured"]]
        total_failure = bool(configured) and all(
            row["status"] == "error" for row in configured
        )
        fetch_error = (
            "all configured headline providers failed"
            if total_failure else None
        )
        self._pool_diagnostics = self._summarize_raw_pool(
            raw,
            query="multi-provider",
            now=self.now_utc,
            fetch_error=fetch_error,
        )
        google_health = next(
            (row for row in provider_health if row["name"] == "Google News"),
            {},
        )
        self._pool_diagnostics.update({
            "primary_query": self.PRIMARY_QUERY,
            # Backward-compatible meaning: Google's alternate RSS query fired.
            "fallback_used": bool(google_health.get("fallback_used")),
            "attempts": [
                {
                    **row,
                    "fetched_count": row.get(
                        "fetched_count", row.get("record_count", 0)
                    ),
                }
                for row in (google_health.get("attempts") or [])
            ],
            "google_fallback_used": google_fallback_used,
            "providers": provider_health,
            "provider_failures": partial_failures,
            "provider_counts": dict(Counter(
                row.get("provider") or "unknown" for row in raw
            )),
            "publisher_counts": dict(Counter(
                self._publisher_key(row) for row in raw
            )),
            "selected_provider_counts": dict(Counter(
                row.get("provider") or "unknown"
                for row in self._scored_headlines
            )),
            "selected_publisher_counts": dict(Counter(
                self._publisher_key(row) for row in self._scored_headlines
            )),
            "relevant_before_recency": len(processed["relevant"]),
            "fresh_relevance_zero_count": len(processed["fresh_zero"]),
            "fresh_relevance_zero_examples": processed["fresh_zero"][:5],
            "fresh_relevant_count": len(processed["fresh"]),
            "dedupe_input_count": len(processed["fresh"]),
            "dedupe_dropped_count": len(processed["duplicate_dropped"]),
            "eligible_before_caps": len(processed["deduped"]),
            "cap_dropped_count": len(processed["cap_dropped"]),
            "selected_count": len(self._scored_headlines),
            "no_approved_lane_count": len(
                processed["relevance_dropped"]
            ),
            "accounted_candidate_count": sum(
                len(processed[key])
                for key in (
                    "selected",
                    "relevance_dropped",
                    "stale_dropped",
                    "duplicate_dropped",
                    "cap_dropped",
                )
            ),
            "relevant_lane_counts": dict(Counter(
                row.get("lane") for row in processed["relevant"]
            )),
            "eligible_lane_counts": dict(Counter(
                row.get("lane") for row in processed["deduped"]
            )),
            "selected_lane_counts": dict(Counter(
                row.get("lane") for row in self._scored_headlines
            )),
            "unique_selected_publishers": len({
                self._publisher_key(row) for row in self._scored_headlines
            }),
            "selected_publisher_max_share": self._max_publisher_share(
                self._scored_headlines
            ),
            "google_selected_count": sum(
                row.get("provider") == "Google News"
                for row in self._scored_headlines
            ),
        })
        logger.info(
            "Headline pool: fetched=%s fresh=%s selected=%s providers=%s "
            "google_fill=%s",
            self._pool_diagnostics["fetched_count"],
            self._pool_diagnostics["fresh_before_relevance"],
            len(self._scored_headlines),
            self._pool_diagnostics["provider_counts"],
            google_fallback_used,
        )
        return [row["text"] for row in self._scored_headlines]

    def get_scored_headlines(self):
        return [dict(row) for row in self._scored_headlines]

    def get_dropped_headlines(self):
        return [
            {
                **row,
                "as_of": row.get("as_of") or to_utc_z(self._record_time(row)),
            }
            for row in self._dropped_headlines
        ]

    def get_pool_diagnostics(self):
        diagnostics = dict(self._pool_diagnostics)
        for key in (
            "fresh_relevance_zero_examples",
            "attempts",
            "providers",
        ):
            diagnostics[key] = [
                dict(row) for row in diagnostics.get(key, [])
            ]
        return diagnostics

    @classmethod
    def _record_time(cls, item):
        if item.get("published"):
            return item.get("published")
        if item.get("source_time_kind") == "provider_seen":
            return item.get("provider_seen_at")
        return None

    @classmethod
    def _summarize_raw_pool(cls, items, query, now=None, fetch_error=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = to_utc_z(
            now.astimezone(timezone.utc)
            - timedelta(days=cls.HEADLINES_MAX_AGE_DAYS)
        )
        stamps = [to_utc_z(cls._record_time(item)) for item in items]
        dated = [stamp for stamp in stamps if stamp is not None]
        return {
            "query": query,
            "fetched_count": len(items),
            "dated_count": len(dated),
            "undated_count": len(items) - len(dated),
            "newest_as_of": max(dated, default=None),
            "oldest_as_of": min(dated, default=None),
            "recency_cutoff": cutoff,
            "fresh_before_relevance": sum(
                1 for stamp in dated if stamp >= cutoff
            ),
            "relevant_before_recency": 0,
            "fresh_relevance_zero_count": 0,
            "fresh_relevance_zero_examples": [],
            "fresh_relevant_count": 0,
            "fetch_error": fetch_error,
            "primary_query": cls.PRIMARY_QUERY,
            "fallback_used": False,
            "attempts": [],
            "google_fallback_used": False,
            "providers": [],
            "provider_failures": [],
            "provider_counts": {},
            "publisher_counts": {},
            "selected_provider_counts": {},
            "selected_publisher_counts": {},
            "dedupe_input_count": 0,
            "dedupe_dropped_count": 0,
            "eligible_before_caps": 0,
            "cap_dropped_count": 0,
            "selected_count": 0,
            "no_approved_lane_count": 0,
            "relevant_lane_counts": {},
            "eligible_lane_counts": {},
            "selected_lane_counts": {},
            "unique_selected_publishers": 0,
            "selected_publisher_max_share": 0.0,
            "google_selected_count": 0,
        }


    @classmethod
    def _drop_stale(cls, records, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = to_utc_z(now - timedelta(days=cls.HEADLINES_MAX_AGE_DAYS))
        fresh, dropped = [], []
        for rec in records:
            stamp = to_utc_z(cls._record_time(rec))
            if stamp is None:
                dropped.append({
                    **rec, "as_of": None, "drop_reason": "undated"
                })
            elif stamp < cutoff:
                dropped.append({
                    **rec, "as_of": stamp, "drop_reason": "stale"
                })
            else:
                fresh.append(rec)
        return fresh, dropped

    @classmethod
    def _listing_symbols(cls, title):
        return [
            match.group(1).upper()
            for match in cls.EXCHANGE_LISTING_PATTERN.finditer(title or "")
        ]

    @classmethod
    def _strip_exchange_listing_metadata(cls, title):
        return re.sub(
            r"\s+", " ", cls.EXCHANGE_LISTING_PATTERN.sub(" ", title or "")
        ).strip()

    @staticmethod
    def _has_keyword(text, keyword):
        return bool(re.search(
            rf"\b{re.escape(keyword)}(?:e?s)?\b", text
        ))

    @classmethod
    def _matched_universe_tickers(cls, item):
        title = item.get("title") or ""
        normalized = cls._strip_exchange_listing_metadata(title)
        normalized_lower = normalized.casefold()
        universe = set(ALL_TICKERS)
        raw_tickers = item.get("tickers") or []
        if isinstance(raw_tickers, str):
            raw_tickers = re.split(r"[,\s]+", raw_tickers)
        matched = {
            str(ticker).upper()
            for ticker in raw_tickers
            if str(ticker).upper() in universe
        }
        matched.update(
            symbol for symbol in cls._listing_symbols(title)
            if symbol in universe
        )
        for ticker, aliases in TICKER_ALIASES.items():
            if re.search(
                rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])",
                normalized,
            ) or any(
                re.search(rf"\b{re.escape(alias)}\b", normalized_lower)
                for alias in aliases
            ):
                matched.add(ticker)
        return sorted(matched)

    @classmethod
    def _score_components(cls, item):
        title = item.get("title") or ""
        normalized = cls._strip_exchange_listing_metadata(title)
        normalized_lower = normalized.casefold()
        universe_tickers = cls._matched_universe_tickers(item)
        outside_listing = bool(
            set(cls._listing_symbols(title)) - set(ALL_TICKERS)
        ) and not universe_tickers

        macro_hits = 0
        for keyword in MACRO_KEYWORDS:
            if (
                keyword == "treasury"
                or keyword in cls.COMPANY_CONTEXT_ONLY_KEYWORDS
            ):
                continue
            if cls._has_keyword(normalized_lower, keyword):
                macro_hits += 1
        if any(
            re.search(pattern, normalized_lower)
            for pattern in cls.TREASURY_PATTERNS
        ):
            macro_hits += 1
        # A listing label is metadata, not a macro subject. If the title is
        # explicitly about an out-of-universe listing, broad words elsewhere
        # cannot silently promote the issuer into the macro lane.
        if outside_listing:
            macro_hits = 0

        vertical_hits = sum(
            1
            for terms in cls.VERTICAL_KEYWORDS.values()
            if any(cls._has_keyword(normalized_lower, term) for term in terms)
        )
        impact = min(3, sum(
            weight
            for pattern, weight in cls.IMPACT_PATTERNS
            if re.search(pattern, normalized_lower)
        ))
        return {
            "issuer_relevance": 2 * len(universe_tickers),
            "macro_relevance": macro_hits,
            "vertical_relevance": vertical_hits,
            "authority": cls.AUTHORITY_SCORES.get(
                item.get("source_class") or "aggregator", 0
            ),
            # Longitudinal novelty needs event history. PR 2 intentionally
            # reports zero instead of fabricating novelty from one raw pool.
            "novelty": 0,
            "impact": impact,
        }

    @classmethod
    def _assign_lane(cls, item, components=None):
        components = components or cls._score_components(item)
        if components["issuer_relevance"] > 0:
            return "universe"
        if components["macro_relevance"] > 0:
            return "macro"
        if (
            components["impact"] >= 2
            and (
                components["vertical_relevance"] > 0
                or components["authority"] >= 3
            )
        ):
            return "discovery"
        return None

    @staticmethod
    def _compatibility_relevance(components):
        return (
            components["issuer_relevance"]
            + components["macro_relevance"]
            + components["vertical_relevance"]
        )

    @classmethod
    def _selection_key(cls, row, stable_index=0):
        components = row.get("score_components") or {}
        return (
            cls.LANE_PRIORITY.get(row.get("lane"), 9),
            -components.get("issuer_relevance", 0),
            -components.get("macro_relevance", 0),
            -components.get("vertical_relevance", 0),
            -components.get("impact", 0),
            -components.get("authority", 0),
            -components.get("novelty", 0),
            -cls._sort_stamp(row),
            row.get("source_record_id") or "",
            stable_index,
        )

    @classmethod
    def _select_relevant(cls, items, top_n=5):
        scored = []
        for idx, item in enumerate(items):
            title = item.get("title") or ""
            components = cls._score_components(item)
            lane = cls._assign_lane(item, components)
            if lane is None:
                continue
            link = item.get("link")
            record = dict(item)
            record.update({
                "text": f"{title} ({link})" if link else title,
                "title": title,
                "link": link,
                "published": item.get("published"),
                "relevance": cls._compatibility_relevance(components),
                "lane": lane,
                "universe_tickers": cls._matched_universe_tickers(item),
                "score_components": components,
            })
            scored.append((cls._selection_key(record, idx), record))
        scored.sort(key=lambda row: row[0])
        if top_n is not None:
            scored = scored[:top_n]
        return [row[1] for row in scored]

    @staticmethod
    def _sort_stamp(record):
        stamp = to_utc_z(NewsAgent._record_time(record))
        if not stamp:
            return 0
        try:
            return int(datetime.fromisoformat(
                stamp.replace("Z", "+00:00")
            ).timestamp())
        except ValueError:
            return 0

    @classmethod
    def _score_title(cls, title):
        return cls._compatibility_relevance(
            cls._score_components({"title": title})
        )

    @classmethod
    def _normalized_title(cls, row):
        text = unicodedata.normalize("NFKC", row.get("title") or "").casefold()
        publisher = (row.get("publisher") or "").casefold().strip()
        domain = (row.get("publisher_domain") or "").casefold().strip()
        suffix = text.rsplit(" - ", 1)[-1] if " - " in text else ""
        if suffix and suffix in {publisher, domain, domain.split(".", 1)[0]}:
            text = text.rsplit(" - ", 1)[0]
        text = re.sub(r"\b(?:breaking|update|exclusive)\b", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    @classmethod
    def _title_tokens(cls, row):
        return set(cls._normalized_title(row).split())

    @classmethod
    def _same_story(cls, left, right):
        left_url = left.get("canonical_url")
        right_url = right.get("canonical_url")
        if left_url and right_url and left_url == right_url:
            return True
        left_title = cls._normalized_title(left)
        right_title = cls._normalized_title(right)
        if left_title and left_title == right_title:
            return True
        left_tokens = cls._title_tokens(left)
        right_tokens = cls._title_tokens(right)
        if min(len(left_tokens), len(right_tokens)) < 6:
            return False
        union = left_tokens | right_tokens
        similarity = len(left_tokens & right_tokens) / len(union)
        return similarity >= 0.85

    @classmethod
    def _representative_key(cls, row):
        source_class = row.get("source_class") or "aggregator"
        return (
            cls.SOURCE_PRIORITY.get(source_class, 4),
            1 if row.get("provider") == "Google News" else 0,
            -row.get("relevance", 0),
            -cls._sort_stamp(row),
            row.get("provider") or "",
            row.get("canonical_url") or "",
        )

    @classmethod
    def _deduplicate(cls, rows):
        groups = []
        for row in sorted(rows, key=cls._representative_key):
            group = next(
                (
                    candidate for candidate in groups
                    if cls._same_story(row, candidate[0])
                ),
                None,
            )
            if group is None:
                groups.append([row])
            else:
                group.append(row)
        kept, dropped = [], []
        for group in groups:
            group.sort(key=cls._representative_key)
            representative = dict(group[0])
            if len(group) > 1:
                representative["duplicate_providers"] = sorted({
                    row.get("provider") for row in group
                    if row.get("provider")
                })
                representative["duplicate_publishers"] = sorted({
                    row.get("publisher") for row in group
                    if row.get("publisher")
                })
            kept.append(representative)
            for row in group[1:]:
                dropped.append({
                    **row,
                    "drop_reason": "duplicate",
                    "kept_source_record_id": representative.get(
                        "source_record_id"
                    ),
                })
        kept.sort(key=cls._selection_key)
        return kept, dropped

    @classmethod
    def _publisher_key(cls, row):
        return (
            row.get("publisher_domain")
            or row.get("publisher")
            or row.get("provider")
            or "unknown"
        ).casefold()

    @classmethod
    def _select_diverse(cls, rows):
        selected, dropped = [], []
        publisher_counts = Counter()
        google_count = 0
        press_release_count = 0
        for row in rows:
            if len(selected) >= cls.TARGET_COUNT:
                dropped.append({
                    **row, "drop_reason": "selection_limit"
                })
                continue
            publisher = cls._publisher_key(row)
            source_class = row.get("source_class")
            if publisher_counts[publisher] >= cls.MAX_PER_PUBLISHER:
                dropped.append({
                    **row, "drop_reason": "publisher_cap"
                })
                continue
            if (
                row.get("provider") == "Google News"
                and google_count >= cls.GOOGLE_MAX_SELECTED
            ):
                dropped.append({
                    **row, "drop_reason": "google_provider_cap"
                })
                continue
            if source_class == "press_release" and press_release_count >= 1:
                dropped.append({
                    **row, "drop_reason": "press_release_cap"
                })
                continue
            selected.append(row)
            publisher_counts[publisher] += 1
            google_count += row.get("provider") == "Google News"
            press_release_count += source_class == "press_release"
        return selected, dropped

    @classmethod
    def _max_publisher_share(cls, rows):
        if not rows:
            return 0.0
        counts = Counter(cls._publisher_key(row) for row in rows)
        return round(max(counts.values()) / len(rows), 4)

    def get_specific_news(self, query):
        return self._fetch_rss(query)

    def get_ticker_news(self, ticker):
        return self._fetch_rss(
            f"{ticker} stock price OR earnings OR analysis", limit=5
        )

    def _fetch_rss_records(self, query, limit=5):
        return self._google_provider.fetch_query(query, limit)

    def _fetch_rss(self, query, limit=5):
        records, error = self._fetch_rss_records(query, limit)
        if error:
            return [f"Error connecting to News Stream: {error}"]
        return [
            f"{row['title']} ({row['link']})"
            if row.get("link") else row["title"]
            for row in records
        ]

    def _process_pool(self, raw):
        relevant = self._select_relevant(raw, top_n=None)
        cutoff = to_utc_z(
            self.now_utc - timedelta(days=self.HEADLINES_MAX_AGE_DAYS)
        )
        relevance_dropped = []
        fresh_zero = []
        for item in raw:
            stamp = to_utc_z(self._record_time(item))
            components = self._score_components(item)
            if self._assign_lane(item, components) is None:
                dropped = {
                    **item,
                    "as_of": stamp,
                    "relevance": self._compatibility_relevance(components),
                    "lane": None,
                    "universe_tickers": self._matched_universe_tickers(item),
                    "score_components": components,
                    "drop_reason": "no_approved_lane",
                }
                relevance_dropped.append(dropped)
                if stamp is not None and stamp >= cutoff:
                    fresh_zero.append({
                        "title": item.get("title"),
                        "as_of": stamp,
                        "provider": item.get("provider"),
                        "publisher": item.get("publisher"),
                        "reason": dropped["drop_reason"],
                    })
        fresh, stale_dropped = self._drop_stale(
            relevant, now=self.now_utc
        )
        deduped, duplicate_dropped = self._deduplicate(fresh)
        selected, cap_dropped = self._select_diverse(deduped)
        return {
            "relevant": relevant,
            "fresh": fresh,
            "fresh_zero": fresh_zero,
            "relevance_dropped": relevance_dropped,
            "stale_dropped": stale_dropped,
            "deduped": deduped,
            "duplicate_dropped": duplicate_dropped,
            "selected": selected,
            "cap_dropped": cap_dropped,
        }
