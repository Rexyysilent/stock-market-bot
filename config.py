import os
from types import MappingProxyType
from dotenv import load_dotenv

load_dotenv()

def _positive_int_env(name, default, minimum=1, maximum=120):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


def _optional_positive_int_env(name):
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _choice_env(name, default, choices):
    value = str(os.getenv(name, default) or "").strip().casefold()
    return value if value in choices else default


# Release identity. schema_version describes the public JSON contract.
# Bump pipeline_version when signal, event, or ledger eligibility/populations
# change; editorial-only headline ranking does not start a new era.
PIPELINE_VERSION = "2.6.3"
SCHEMA_VERSION = "2.8"
BRIEF_ARCHIVE_DIR = os.getenv("BRIEF_ARCHIVE_DIR", os.path.join("archive", "briefs"))
BRIEF_ARCHIVE_MIRROR_DIR = os.getenv("BRIEF_ARCHIVE_MIRROR_DIR") or None

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = _optional_positive_int_env("DISCORD_GUILD_ID")
REPORT_CHANNEL_ID = _optional_positive_int_env("REPORT_CHANNEL_ID")
DISCORD_REQUEST_COOLDOWN_SECONDS = _positive_int_env(
    "DISCORD_REQUEST_COOLDOWN_SECONDS", 30, maximum=3600
)
DISCORD_MAX_TRACKED_USERS = _positive_int_env(
    "DISCORD_MAX_TRACKED_USERS", 2048, maximum=100000
)
DISCORD_MAX_HISTORY_CHANNELS = _positive_int_env(
    "DISCORD_MAX_HISTORY_CHANNELS", 100, maximum=10000
)
DISCORD_HISTORY_ENTRIES_PER_CHANNEL = _positive_int_env(
    "DISCORD_HISTORY_ENTRIES_PER_CHANNEL", 20, maximum=200
)

# Agents
OLLAMA_MODEL = "gemma4:26b"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_NUM_CTX = 4096  # Cap context window — 256K default would cook 8GB VRAM

# SEC EDGAR (public API — requires identifying User-Agent)
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "StockMarketBot contact@example.com")

# Diversified daily-headline providers. Official feeds and GDELT are keyless;
# FMP and Alpha Vantage News are enabled only when their keys are configured.
# Google News is intentionally a fill source, not the primary corpus.
FMP_API_KEY = os.getenv("FMP_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
HEADLINE_REQUEST_TIMEOUT_SECONDS = _positive_int_env(
    "HEADLINE_REQUEST_TIMEOUT_SECONDS", 12
)
HEADLINE_TARGET_COUNT = 5
EVIDENCE_INTAKE_MODE = _choice_env("EVIDENCE_INTAKE_MODE", "shadow", ("off", "shadow"))
HEADLINE_MAX_PER_PUBLISHER = 1
HEADLINE_GOOGLE_MAX_SELECTED = 2
HEADLINE_GDELT_ENABLED = os.getenv(
    "HEADLINE_GDELT_ENABLED", "true"
).lower() not in ("0", "false", "no")
HEADLINE_GOOGLE_FALLBACK_ENABLED = os.getenv(
    "HEADLINE_GOOGLE_FALLBACK_ENABLED", "true"
).lower() not in ("0", "false", "no")
HEADLINE_OFFICIAL_FEEDS = (
    {
        "name": "sec",
        "publisher": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "source_class": "official_regulator",
        "headers": {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
    },
    {
        "name": "federal_reserve",
        "publisher": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "source_class": "official_central_bank",
    },
    {
        "name": "fda",
        "publisher": "U.S. Food and Drug Administration",
        "url": (
            "https://www.fda.gov/about-fda/contact-fda/stay-informed/"
            "rss-feeds/press-releases/rss.xml"
        ),
        "source_class": "official_regulator",
    },
    {
        "name": "nasdaq_trader",
        "publisher": "Nasdaq Trader",
        "url": "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
        "source_class": "official_exchange",
        "feed_kind": "nasdaq_halts",
    },
)

# Watchlists
# Commodities / ETFs
# SI=F (Silver Futures), URA (Global Uranium ETF), USO (Oil), GLD (Gold)
WATCHLIST_COMMODITIES = ["SI=F", "URA", "USO", "GLD", "PSLV"]
WATCHLIST_STOCKS = ["TSLA", "ROKU", "PLTR", "STTDF", "CRSP", "ACHR", "SHOP", "SCCO", "NTLA", "FCX", "VRT", "COIN", "RIVN", "AMAT", "VNDA"]

# Uranium Miners (CEO.ca focus)
WATCHLIST_URANIUM = ["UUUU", "CCJ", "NXE", "DNN"]

# Legacy drop-monitor identifier retained for compatibility with existing
# consumers. It marks unusually large completed-session declines; it is not a
# buy list or recommendation.
WATCHLIST_VULTURE = ["RGNX", "CCJ"]
VULTURE_DROP_PCT = 8.0

# Defense / Aerospace
WATCHLIST_DEFENSE = ["LMT", "NOC", "ITA"]

# Market Gauges
WATCHLIST_GAUGES = ["^VIX"]  # Fear gauge

# Optional editorial-focus override. An unset value enables deterministic
# evidence-backed selection; a configured ticker must pass the same gate.
FOCUS_TICKER = (os.getenv("FOCUS_TICKER") or "").strip().upper() or None

# The editorial expansion is presentation-only until explicitly activated after the
# live shadow audit. Invalid values fail closed to shadow rather than widening
# eligibility accidentally.
EDITORIAL_COVERAGE_MODE = _choice_env(
    "EDITORIAL_COVERAGE_MODE", "shadow", {"off", "shadow", "active"}
)

# Universe metadata for the daily brief dataset (version is derived from the ticker list)
UNIVERSE_NAME = "sample-us-market-universe"

# The original 29 remain the only instrumented and signal-eligible universe.
# ALL_TICKERS intentionally remains a list projection of these 29 for legacy
# consumers; new code should choose the purpose-specific immutable constants.
_all = (WATCHLIST_STOCKS + WATCHLIST_COMMODITIES + WATCHLIST_URANIUM
        + WATCHLIST_DEFENSE + WATCHLIST_VULTURE + WATCHLIST_GAUGES)
SIGNAL_ELIGIBLE_TICKERS = tuple(dict.fromkeys(_all))
SIGNAL_ELIGIBLE_TICKER_SET = frozenset(SIGNAL_ELIGIBLE_TICKERS)
LEGACY_EDITORIAL_ONLY_TICKERS = (
    "MRNA", "VRTX", "BEAM", "RARE", "CEG", "LEU",
    "BWXT", "GEV", "MP", "NVDA", "RKLB", "HOOD",
)
# The 41-name cohort has already appeared in immutable shadow exports. Keep
# HOOD and append MRK; never silently replace the deployed cohort.
EDITORIAL_ONLY_TICKERS = LEGACY_EDITORIAL_ONLY_TICKERS + ("MRK",)
EDITORIAL_ONLY_TICKER_SET = frozenset(EDITORIAL_ONLY_TICKERS)
EDITORIAL_COVERAGE_TICKERS = (
    SIGNAL_ELIGIBLE_TICKERS + EDITORIAL_ONLY_TICKERS
)
EDITORIAL_COVERAGE_TICKER_SET = frozenset(EDITORIAL_COVERAGE_TICKERS)
ALL_TICKERS = list(SIGNAL_ELIGIBLE_TICKERS)

# Company-name aliases for entity linking and headline relevance scoring.
# Conservative: unambiguous names only (bare "crispr" or "gold" would false-hit).
CORE_TICKER_ALIASES = MappingProxyType({
    "TSLA": ("tesla",),
    "ROKU": ("roku",),
    "PLTR": ("palantir",),
    "CRSP": ("crispr therapeutics",),
    "ACHR": ("archer aviation",),
    "SHOP": ("shopify",),
    "SCCO": ("southern copper",),
    "NTLA": ("intellia",),
    "FCX": ("freeport",),
    "VRT": ("vertiv",),
    "COIN": ("coinbase",),
    "RIVN": ("rivian",),
    "AMAT": ("applied materials",),
    "VNDA": ("vanda",),
    "UUUU": ("energy fuels",),
    "CCJ": ("cameco",),
    "NXE": ("nexgen",),
    "DNN": ("denison",),
    "RGNX": ("regenxbio",),
    "LMT": ("lockheed",),
    "NOC": ("northrop",),
})
EDITORIAL_TICKER_ALIASES = MappingProxyType({
    # Deliberately omit bare mRNA/beam/rare/MP/hood: each is ordinary language
    # or a broad scientific/category term and cannot establish issuer identity.
    "MRNA": ("moderna",),
    "VRTX": ("vertex pharmaceuticals",),
    "BEAM": ("beam therapeutics",),
    "RARE": ("ultragenyx",),
    "CEG": ("constellation energy",),
    "LEU": ("centrus energy",),
    "BWXT": ("bwx technologies",),
    "GEV": ("ge vernova",),
    "MP": ("mp materials",),
    "NVDA": ("nvidia",),
    "RKLB": ("rocket lab",),
    "HOOD": ("robinhood markets", "robinhood"),
    "MRK": ("merck & co", "merck and co"),
})
TICKER_ALIASES = MappingProxyType({
    **CORE_TICKER_ALIASES,
    **EDITORIAL_TICKER_ALIASES,
})

# GDELT stays frozen at the pre-expansion terms. The new aliases are used to
# classify stories already present in broad feeds. Off/shadow FMP acquisition
# stays at the legacy payload; only active mode widens that payload.
GDELT_TICKER_ALIASES = CORE_TICKER_ALIASES

ISSUER_REGISTRY = MappingProxyType({
    "MRNA": MappingProxyType({
        "ticker": "MRNA", "cik": "0001682852", "legal_name": "Moderna, Inc.",
        "display_name": "Moderna", "aliases": EDITORIAL_TICKER_ALIASES["MRNA"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "VRTX": MappingProxyType({
        "ticker": "VRTX", "cik": "0000875320", "legal_name": "Vertex Pharmaceuticals Incorporated",
        "display_name": "Vertex Pharmaceuticals", "aliases": EDITORIAL_TICKER_ALIASES["VRTX"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "BEAM": MappingProxyType({
        "ticker": "BEAM", "cik": "0001745999", "legal_name": "Beam Therapeutics Inc.",
        "display_name": "Beam Therapeutics", "aliases": EDITORIAL_TICKER_ALIASES["BEAM"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "RARE": MappingProxyType({
        "ticker": "RARE", "cik": "0001515673", "legal_name": "Ultragenyx Pharmaceutical Inc.",
        "display_name": "Ultragenyx", "aliases": EDITORIAL_TICKER_ALIASES["RARE"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "CEG": MappingProxyType({
        "ticker": "CEG", "cik": "0001868275", "legal_name": "Constellation Energy Corporation",
        "display_name": "Constellation Energy", "aliases": EDITORIAL_TICKER_ALIASES["CEG"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "LEU": MappingProxyType({
        "ticker": "LEU", "cik": "0001065059", "legal_name": "Centrus Energy Corp.",
        "display_name": "Centrus Energy", "aliases": EDITORIAL_TICKER_ALIASES["LEU"],
        "exchange": "NYSE", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "BWXT": MappingProxyType({
        "ticker": "BWXT", "cik": "0001486957", "legal_name": "BWX Technologies, Inc.",
        "display_name": "BWX Technologies", "aliases": EDITORIAL_TICKER_ALIASES["BWXT"],
        "exchange": "NYSE", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "GEV": MappingProxyType({
        "ticker": "GEV", "cik": "0001996810", "legal_name": "GE Vernova Inc.",
        "display_name": "GE Vernova", "aliases": EDITORIAL_TICKER_ALIASES["GEV"],
        "exchange": "NYSE", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "MP": MappingProxyType({
        "ticker": "MP", "cik": "0001801368", "legal_name": "MP Materials Corp.",
        "display_name": "MP Materials", "aliases": EDITORIAL_TICKER_ALIASES["MP"],
        "exchange": "NYSE", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "NVDA": MappingProxyType({
        "ticker": "NVDA", "cik": "0001045810", "legal_name": "NVIDIA Corporation",
        "display_name": "NVIDIA", "aliases": EDITORIAL_TICKER_ALIASES["NVDA"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "RKLB": MappingProxyType({
        "ticker": "RKLB", "cik": "0001819994", "legal_name": "Rocket Lab Corporation",
        "display_name": "Rocket Lab", "aliases": EDITORIAL_TICKER_ALIASES["RKLB"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
    "HOOD": MappingProxyType({
        "ticker": "HOOD", "cik": "0001783879", "legal_name": "Robinhood Markets, Inc.",
        "display_name": "Robinhood Markets", "aliases": EDITORIAL_TICKER_ALIASES["HOOD"],
        "exchange": "NASDAQ", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
})

ISSUER_REGISTRY = MappingProxyType({
    **ISSUER_REGISTRY,
    "MRK": MappingProxyType({
        "ticker": "MRK", "cik": "0000310158",
        "legal_name": "Merck & Co., Inc.", "display_name": "Merck & Co.",
        "aliases": EDITORIAL_TICKER_ALIASES["MRK"],
        "exchange": "NYSE", "security_type": "common_stock",
        "coverage_tier": "editorial_only",
    }),
})

assert len(SIGNAL_ELIGIBLE_TICKERS) == 29
assert len(LEGACY_EDITORIAL_ONLY_TICKERS) == 12
assert len(EDITORIAL_ONLY_TICKERS) == 13
assert len(EDITORIAL_COVERAGE_TICKERS) == 42
assert SIGNAL_ELIGIBLE_TICKER_SET.isdisjoint(EDITORIAL_ONLY_TICKER_SET)
assert set(ISSUER_REGISTRY) == EDITORIAL_ONLY_TICKER_SET
assert all(
    ticker == profile["ticker"] for ticker, profile in ISSUER_REGISTRY.items()
)

# Macro themes for headline relevance scoring.
# Matched word-bounded with optional plural ("tariff" hits "tariffs"), so
# "fed" alone won't hit "federal holiday" — hence the explicit multiword forms.
MACRO_KEYWORDS = [
    "fed", "fomc", "federal reserve", "rate cut", "rate hike", "interest rate",
    "inflation", "cpi", "tariff", "recession", "yield", "opec",
    "crude", "uranium", "fda", "earnings", "layoff", "china", "semiconductor",
    "crypto", "bitcoin", "payroll", "jobs report",
    # Strong broad-market anchors. Deliberately omit bare "stock market" so
    # utility filler such as "Is the stock market open today?" remains score 0.
    "s&p 500", "nasdaq", "dow", "russell 2000", "wall street",
    "stock futures", "market futures", "oil",
]

# Social Sources Configuration
# 1. NARRATIVE (Reddit RSS): niche thesis subs only. ApeWisdom already covers
#    broad ticker mention/upvote heat, and the old 24-sub sweep was rate-limited
#    to death (~38 429s per run, pure wasted retries polluting health warnings).
#    Keep only communities whose *narrative* ApeWisdom's counts can't represent.
SUBREDDITS_NARRATIVE = ["uraniumsqueeze", "Biotechplays"]

# 2. DIRECT FEED (RSS)
RSS_FEEDS = [
    # ZeroHedge (direct RSS, not FeedBurner)
    "https://cms.zerohedge.com/fullrss2.xml",
    # OpenInsider (SEC Form 4s - Cluster Buys)
    "https://openinsider.com/rss",
    # Biopharmcatalyst / Biotech Scanning (FDA Approvals, PDUFA, Phase 3)
    "https://news.google.com/rss/search?q=(site:biopharmcatalyst.com+OR+intitle:FDA+OR+intitle:PDUFA+OR+intitle:Phase+3)&hl=en-US&gl=US&ceid=US:en",
    # Layoffs / Hiring Freeze / RIF tracker (tech sector focus)
    "https://news.google.com/rss/search?q=(intitle:layoffs+OR+intitle:hiring+freeze+OR+intitle:headcount+OR+intitle:RIF)+AND+(tech+OR+FAANG+OR+startup+OR+Silicon+Valley)&hl=en-US&gl=US&ceid=US:en",
    # Substack — Finance / Macro / Energy
    "https://doomberg.substack.com/feed",              # Energy, commodities, macro (green chicken)
    "https://thelastbearstanding.substack.com/feed",   # Macro bear case, credit, liquidity
    "https://morethanmoore.substack.com/feed",         # TechTechPotato (Ian Cutress) — semiconductor deep dives
    # Thesis-targeted Google News RSS
    "https://news.google.com/rss/search?q=Uranium+AND+(Sprott+OR+Cameco+OR+Kazatomprom)&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Palantir+AND+(Contract+OR+Army+OR+NHS)&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(CRISPR+OR+Intellia)+AND+(FDA+OR+Clinical+Trial)&hl=en-US&gl=US&ceid=US:en",
]

# Nitter RSS instances (for Twitter/X intel — fallback chain)
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.net",
]

# Financial Twitter accounts to monitor via Nitter RSS
TWITTER_ACCOUNTS = [
    "DeItaone",         # Breaking news / headlines
    "unusual_whales",   # Options flow / unusual activity
    "zabormetrics",     # Quant / metrics
    "WallStJesus",      # Market commentary
    "mcaborern",        # Macro / commodities
]

# Missing source time fails these freshness gates and is surfaced in the
# export's data_quality block rather than being presented as current.
CEO_CA_MAX_AGE_DAYS = 7
TWITTER_MAX_AGE_DAYS = 7

# Descriptive measurement thresholds
PRICE_ALERT_PCT = 3.0      # Flag completed-session moves above this magnitude
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Sector-relative return baskets
GROWTH_BASKET = ["TSLA", "ROKU", "PLTR", "SHOP", "VRT", "ACHR", "COIN"]
DEFENSIVE_BASKET = ["GLD", "USO", "SI=F", "URA"]

# Options measurements
# Deprecated compatibility value. Machine alerts are owned exclusively by the
# pipeline-versioned baseline layer; no absolute P/C alert consumes this.
PUT_CALL_ALERT_THRESHOLD = 1.5

# Option contract volume/open-interest anomaly filter
OPTION_CONTRACT_VOL_OI_THRESHOLD = 5.0
OPTION_CONTRACT_MAX_DTE = 5
OPTION_CONTRACT_MIN_NOTIONAL = 500_000

# Instrument relative-return spread tracker
# SRUUF = Sprott Physical Uranium Trust (physical proxy)
# URA   = Global X Uranium ETF (paper proxy)
INSTRUMENT_RELATIVE_RETURN_PAIRS = [
    {"physical": "SRUUF", "paper": "URA", "commodity": "Uranium"},
    {"physical": "SI=F", "paper": "SLV", "commodity": "Silver"},
]
INSTRUMENT_RELATIVE_RETURN_THRESHOLD_PCT = 3.0

# Ledger asset-class mapping. Syntax handles futures/indices; these are the
# configured fund products that otherwise look like ordinary equity symbols.
ETF_TICKERS = {"URA", "USO", "GLD", "PSLV", "ITA", "SPY", "SLV"}

# Retail-language intensity measurement (compatibility name retained)
CONTRARIAN_SUBREDDITS = ["teslainvestorsclub", "wallstreetbets"]
CONTRARIAN_EUPHORIA_KEYWORDS = [
    "moon", "rocket", "lambo", "to the moon", "cybercab", "squeeze",
    "diamond hands", "yolo", "all in", "can't go tits up", "free money",
    "generational wealth", "100x", "10x", "millionaire",
]
CONTRARIAN_EUPHORIA_THRESHOLD = 0.25  # 25% of scored posts contain euphoria = flag top

# ApeWisdom aggregates ticker mentions/upvotes from Reddit stock boards and 4chan /biz.
# It fills the gap left by Reddit RSS, which does not expose score/comment filters.
APEWISDOM_ENABLED = os.getenv("APEWISDOM_ENABLED", "true").lower() not in ("0", "false", "no")
APEWISDOM_FILTERS = [
    ("all-stocks", 15),
    ("4chan", 5),
]
# Universe-first scan: paginate all-stocks this deep (~100 rows/page) to locate
# every universe ticker whatever its rank. Beyond this depth a ticker is
# treated as mentions=0 / in_leaderboard=false.
APEWISDOM_UNIVERSE_PAGES = 5
# Filters whose source has no upvote mechanic: ApeWisdom reports upvotes=0
# there, so attention_score (upvotes/mentions) is emitted as null, not 0.0.
APEWISDOM_NO_UPVOTE_FILTERS = {"4chan"}
# Below this many mentions, 24h velocity/rank moves are mostly noise.
# Keep this low: 4chan-filter mention counts run tiny (top rows are 1-10
# mentions), so anything higher flags that entire feed. Checked 2026-07-03:
# at 30 it flagged 100% of stored 4chan rows and 76% of the all-stocks page.
APEWISDOM_LOW_VOLUME_MENTIONS = 5
