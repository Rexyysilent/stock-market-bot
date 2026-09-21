"""SQLite schema and connection helpers."""
import sqlite3
from pathlib import Path

from .config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  regime TEXT,
  brief_sha256 TEXT NOT NULL,
  pipeline_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
  record_id TEXT PRIMARY KEY,
  source_record_id TEXT NOT NULL,
  family TEXT NOT NULL,
  subtype TEXT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('long', 'short', 'none')),
  strength TEXT,
  regime_at_emission TEXT,
  first_seen_run TEXT NOT NULL REFERENCES runs(run_id),
  last_seen_run TEXT NOT NULL REFERENCES runs(run_id),
  as_of TEXT,
  asset_class TEXT NOT NULL CHECK(asset_class IN ('equity', 'etf', 'future', 'index')),
  payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
  ticker TEXT NOT NULL,
  session TEXT NOT NULL,
  open REAL,
  close REAL,
  PRIMARY KEY (ticker, session)
);

-- Additive coherent price cache. The legacy prices table remains untouched;
-- its rows have no acquisition/basis identity and cannot satisfy new windows.
CREATE TABLE IF NOT EXISTS price_acquisitions (
  acquisition_id TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  provider TEXT NOT NULL,
  interval TEXT NOT NULL,
  requested_start TEXT NOT NULL,
  requested_end TEXT NOT NULL,
  adjustment_basis TEXT NOT NULL,
  currency TEXT,
  acquired_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_points (
  acquisition_id TEXT NOT NULL REFERENCES price_acquisitions(acquisition_id),
  session TEXT NOT NULL,
  open REAL,
  close REAL,
  PRIMARY KEY (acquisition_id, session)
);

CREATE TABLE IF NOT EXISTS price_windows (
  ticker TEXT NOT NULL,
  entry_session TEXT NOT NULL,
  exit_session TEXT NOT NULL,
  acquisition_id TEXT NOT NULL REFERENCES price_acquisitions(acquisition_id),
  PRIMARY KEY (ticker, entry_session, exit_session)
);

CREATE TABLE IF NOT EXISTS outcomes (
  record_id TEXT NOT NULL REFERENCES signals(record_id),
  horizon INTEGER NOT NULL,
  entry_session TEXT,
  exit_session TEXT,
  entry_open REAL,
  exit_close REAL,
  ret REAL,
  spy_ret REAL,
  excess REAL,
  univ_ret REAL,
  status TEXT NOT NULL CHECK(status IN ('pending', 'filled', 'unpriceable')),
  PRIMARY KEY (record_id, horizon)
);

CREATE INDEX IF NOT EXISTS idx_signals_first_seen ON signals(first_seen_run);
CREATE INDEX IF NOT EXISTS idx_signals_family ON signals(family, subtype, strength);
CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes(status);
CREATE INDEX IF NOT EXISTS idx_price_acquisitions_ticker_range
  ON price_acquisitions(ticker, requested_start, requested_end);
"""


def connect(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
