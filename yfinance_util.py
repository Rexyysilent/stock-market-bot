"""Project-local yfinance cache configuration."""
import os
from pathlib import Path


def configure_yfinance_cache(yf_module):
    """Point yfinance's SQLite caches at ignored, writable project state."""
    path = Path(os.getenv("YFINANCE_CACHE_DIR", "state/yfinance_cache")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    setter = getattr(yf_module, "set_tz_cache_location", None)
    if setter:
        setter(str(path))
    return path
