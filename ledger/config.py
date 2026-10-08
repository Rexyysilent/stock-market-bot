"""Central configuration for the Signal Ledger."""
from pathlib import Path

from config import (
    BRIEF_ARCHIVE_DIR, ETF_TICKERS,
    SIGNAL_ELIGIBLE_TICKERS, SIGNAL_ELIGIBLE_TICKER_SET,
)

HORIZONS = (1, 5, 20)
BENCHMARK = "SPY"
# A partial or unavailable frozen-cohort benchmark is retried for this many
# calendar days after that revision was published, then left as published.
# Without a bound, a delisted cohort member was re-downloaded on every run.
BENCHMARK_RETRY_DAYS = 14
# Price windows that came back empty or incomplete: retried every run while
# the window ended within PRICE_MISS_RECENT_DAYS, then at most once every
# PRICE_MISS_BACKOFF_DAYS. A miss is never terminal (no unpriceable claim);
# it only bounds re-downloads, e.g. of a delisted signal ticker.
PRICE_MISS_RECENT_DAYS = 14
PRICE_MISS_BACKOFF_DAYS = 7
BURST_MIN = 3.0
BURST_MIN_MENTIONS = 10
BURST_BUCKETS = (5.0, 10.0)
MIN_N = 20
NOTIONAL_BUCKETS = (100_000, 1_000_000)
Z_BUCKETS = (3.0, 5.0)
LEGACY_PIPELINE_VERSION = "legacy-unversioned"

# M11: prospectively declared evaluation holdout. Entry sessions on or after
# the start are reserved for evaluation; development outcomes whose window
# reaches into it are purged. Declared before any holdout session existed.
# Never move it to fit results: a new design needs a new version and a new,
# later holdout.
EVALUATION_DESIGN_VERSION = "holdout-1"
EVALUATION_HOLDOUT_START = "2026-10-01"
EVALUATION_HOLDOUT_DECLARED_AT = "2026-09-22"

ARCHIVE_DIR = Path(BRIEF_ARCHIVE_DIR)
DB_PATH = Path("ledger") / "ledger.db"
STATS_JSON_PATH = Path("ledger") / "stats.json"
STATS_MD_PATH = Path("ledger") / "stats.md"
UNIVERSE_TICKERS = tuple(SIGNAL_ELIGIBLE_TICKERS)
UNIVERSE_TICKER_SET = SIGNAL_ELIGIBLE_TICKER_SET
FUND_TICKERS = frozenset(ETF_TICKERS)
