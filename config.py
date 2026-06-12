import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # Optional, for syncing commands
REPORT_CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID")  # Channel for auto-alerts

# Agents
OLLAMA_MODEL = "gemma4:26b"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_NUM_CTX = 4096  # Cap context window — 256K default would cook 8GB VRAM

# SEC EDGAR (public API — requires identifying User-Agent)
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "StockMarketBot contact@example.com")

# Watchlists
# Commodities / ETFs
# SI=F (Silver Futures), URA (Global Uranium ETF), USO (Oil), GLD (Gold)
WATCHLIST_COMMODITIES = ["SI=F", "URA", "USO", "GLD", "PSLV"]
WATCHLIST_STOCKS = ["TSLA", "ROKU", "PLTR", "STTDF", "CRSP", "ACHR", "SHOP", "SCCO", "NTLA", "FCX", "VRT", "COIN", "RIVN", "AMAT", "VNDA"]

# Optional local paper-trading allocation. Keep personal sizing out of git.
PAPER_PORTFOLIO = {}

# Uranium Miners (CEO.ca focus)
WATCHLIST_URANIUM = ["UUUU", "CCJ", "NXE", "DNN"]

# Vulture Watch — stocks to buy on big dips
# Bot alerts when these drop significantly (default > 8% intraday)
WATCHLIST_VULTURE = ["RGNX", "CCJ"]
VULTURE_DROP_PCT = 8.0  # Alert when a vulture ticker drops more than this %

# Defense / Aerospace
WATCHLIST_DEFENSE = ["LMT", "NOC", "ITA"]

# Market Gauges
WATCHLIST_GAUGES = ["^VIX"]  # Fear gauge

# Daily brief deep-dive focus ticker (the export builds a dedicated section for it)
FOCUS_TICKER = "ROKU"

# Universe metadata for the daily brief dataset (version is derived from the ticker list)
UNIVERSE_NAME = "default-watchlist"

# Company-name aliases for entity linking and headline relevance scoring.
# Conservative: unambiguous names only (bare "crispr" or "gold" would false-hit).
TICKER_ALIASES = {
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
}

# Macro themes for headline relevance scoring
MACRO_KEYWORDS = [
    "fed", "fomc", "rate cut", "rate hike", "interest rate", "inflation", "cpi",
    "tariff", "recession", "treasury", "yield", "opec", "crude", "uranium",
    "fda", "earnings", "layoffs", "china", "semiconductor", "crypto", "bitcoin",
]

# All tickers combined (deduplicated, preserves order)
_all = (WATCHLIST_STOCKS + WATCHLIST_COMMODITIES + WATCHLIST_URANIUM
        + WATCHLIST_DEFENSE + WATCHLIST_VULTURE + WATCHLIST_GAUGES)
ALL_TICKERS = list(dict.fromkeys(_all))  # dedup while keeping order

# Social Sources Configuration
# 1. CORE (Deep Dive): Niche/High-Knowledge communities
SUBREDDITS_CORE = [
    "securityanalysis", "ValueInvesting",       # Serious DD & analysis
    "uraniumsqueeze", "Commodities", "Wallstreetsilver",  # Commodity thesis subs
    "PLTR", "teslainvestorsclub", "CRISPR", "genetherapy",  # Ticker-specific (your watchlist)
    "Biotechplays", "Semiconductors",            # Sector plays
    "unusual_whales", "Thetagang",               # Options flow / strategy
]

# 2. SKIM (Broad Overview): Just check top headlines
SUBREDDITS_SKIM = ["stocks", "investing", "stockmarket", "FluentInFinance"]

# 3. HIGH VOLTAGE (Noise Filter): Only report if widespread (High Score/Volume)
# India subs included as grain-of-salt global sentiment gauge
SUBREDDITS_VOLATILE = [
    "wallstreetbets", "options",                 # Degen sentiment
    "indianstreetbets", "IndiaInvestments",       # Emerging market pulse (grain of salt)
    "SPACs", "pennystocks", "superstonk",        # Speculative / meme
]

# 4. DIRECT FEED (RSS)
RSS_FEEDS = [
    # ZeroHedge (direct RSS, not FeedBurner)
    "https://cms.zerohedge.com/fullrss2.xml",
    # OpenInsider (SEC Form 4s - Cluster Buys)
    "http://openinsider.com/rss",
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

# Alert Thresholds
PRICE_ALERT_PCT = 3.0      # Alert on moves > 3% intraday
VOLUME_ALERT_MULT = 2.0    # Alert on volume > 2x 20-day avg
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Dip Anticipation — Sector Rotation Baskets
GROWTH_BASKET = ["TSLA", "ROKU", "PLTR", "SHOP", "VRT", "ACHR", "COIN"]
DEFENSIVE_BASKET = ["GLD", "USO", "SI=F", "URA"]

# Options Flow Context
PUT_CALL_ALERT_THRESHOLD = 1.5  # P/C ratio above this is flagged as put-skewed

# Short-Dated Options Activity Detection
GAMMA_VOL_OI_THRESHOLD = 5.0    # Vol/OI ratio for high-activity detection
GAMMA_MAX_DTE = 5               # Only scan 0-5 DTE contracts
GAMMA_MIN_PREMIUM = 500_000     # $500k minimum premium to filter low-conviction flow

# Backwardation Tracker — Physical vs Paper Divergence
# SRUUF = Sprott Physical Uranium Trust (physical proxy)
# URA   = Global X Uranium ETF (paper proxy)
BACKWARDATION_PAIRS = [
    {"physical": "SRUUF", "paper": "URA", "commodity": "Uranium"},
    {"physical": "SI=F", "paper": "SLV", "commodity": "Silver"},
]
BACKWARDATION_THRESHOLD_PCT = 3.0  # Flag when physical > paper by this %

# Retail Contrarian Index — Inverse Sentiment
CONTRARIAN_SUBREDDITS = ["teslainvestorsclub", "wallstreetbets"]
CONTRARIAN_EUPHORIA_KEYWORDS = [
    "moon", "rocket", "lambo", "to the moon", "cybercab", "squeeze",
    "diamond hands", "yolo", "all in", "can't go tits up", "free money",
    "generational wealth", "100x", "10x", "millionaire",
]
CONTRARIAN_EUPHORIA_THRESHOLD = 0.25  # 25% of scored posts contain euphoria = flag top
