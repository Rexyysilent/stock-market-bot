"""SQLite schema and connection helpers."""
import sqlite3
from pathlib import Path

from .config import DB_PATH
from .prices import _CACHE_SCHEMA


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  regime TEXT,
  brief_sha256 TEXT NOT NULL,
  pipeline_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_cohorts (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  status TEXT NOT NULL CHECK(status IN ('valid', 'refused', 'legacy_unfrozen')),
  reason TEXT,
  members_json TEXT NOT NULL,
  weights_json TEXT NOT NULL,
  weight_method TEXT NOT NULL,
  cohort_sha256 TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS signal_sightings (
  record_id TEXT NOT NULL REFERENCES signals(record_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  observed_at TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  family TEXT NOT NULL,
  subtype TEXT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('long', 'short', 'none')),
  strength TEXT,
  regime_at_emission TEXT,
  as_of TEXT,
  asset_class TEXT NOT NULL CHECK(asset_class IN ('equity', 'etf', 'future', 'index')),
  payload TEXT NOT NULL,
  PRIMARY KEY (record_id, run_id)
);

CREATE TABLE IF NOT EXISTS prices (
  ticker TEXT NOT NULL,
  session TEXT NOT NULL,
  open REAL,
  close REAL,
  PRIMARY KEY (ticker, session)
);

-- The additive coherent price cache (price_acquisitions, price_points,
-- price_windows) is defined once, in ledger/prices.py _CACHE_SCHEMA, and
-- created by connect() before this schema. The legacy prices table above
-- remains untouched; its rows have no acquisition/basis identity and cannot
-- satisfy new windows.

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

CREATE TABLE IF NOT EXISTS outcome_revisions (
  revision_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  horizon INTEGER NOT NULL,
  revision_number INTEGER NOT NULL,
  parent_revision_id TEXT REFERENCES outcome_revisions(revision_id),
  basis_run_id TEXT NOT NULL REFERENCES runs(run_id),
  revised_at TEXT NOT NULL,
  revision_reason TEXT NOT NULL CHECK(revision_reason IN (
    'initial_maturity', 'benchmark_recovery', 'benchmark_state_change',
    'sighting_basis_change'
  )),
  raw_status TEXT NOT NULL CHECK(raw_status='complete'),
  benchmark_status TEXT NOT NULL CHECK(benchmark_status IN (
    'complete', 'partial', 'unavailable', 'refused'
  )),
  benchmark_reason TEXT,
  entry_session TEXT NOT NULL,
  exit_session TEXT NOT NULL,
  entry_open REAL NOT NULL,
  exit_close REAL NOT NULL,
  ret REAL NOT NULL,
  signal_acquisition_id TEXT REFERENCES price_acquisitions(acquisition_id),
  spy_ret REAL NOT NULL,
  spy_acquisition_id TEXT REFERENCES price_acquisitions(acquisition_id),
  excess REAL,
  cohort_sha256 TEXT,
  weight_method TEXT,
  expected_n INTEGER NOT NULL,
  covered_n INTEGER NOT NULL,
  covered_weight REAL NOT NULL,
  missing_members_json TEXT NOT NULL,
  member_results_json TEXT NOT NULL,
  benchmark_basis_sha256 TEXT,
  partial_univ_ret REAL,
  univ_ret REAL,
  state_sha256 TEXT NOT NULL,
  UNIQUE (record_id, horizon, revision_number),
  UNIQUE (record_id, horizon, state_sha256),
  FOREIGN KEY (record_id, horizon) REFERENCES outcomes(record_id, horizon)
);

CREATE INDEX IF NOT EXISTS idx_signals_first_seen ON signals(first_seen_run);
CREATE INDEX IF NOT EXISTS idx_signals_family ON signals(family, subtype, strength);
CREATE INDEX IF NOT EXISTS idx_signal_sightings_observed
  ON signal_sightings(record_id, observed_at, run_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes(status);
CREATE INDEX IF NOT EXISTS idx_outcome_revisions_latest
  ON outcome_revisions(record_id, horizon, revision_number);
"""


def connect(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # outcome_revisions references price_acquisitions, so the price cache's
    # single definition is created first.
    conn.executescript(_CACHE_SCHEMA)
    conn.executescript(SCHEMA)
    cohort_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(run_cohorts)")
    }
    if "weight_method" not in cohort_columns:
        conn.execute(
            """ALTER TABLE run_cohorts ADD COLUMN weight_method TEXT NOT NULL
               DEFAULT 'unknown'"""
        )
        conn.execute(
            """UPDATE run_cohorts SET weight_method=CASE
                 WHEN status='valid' THEN 'equal_weight' ELSE 'unavailable' END
               WHERE weight_method='unknown'"""
        )
    # Existing ledgers retained only first/last run pointers. Preserve those
    # admissible observations as additive sightings until archives are replayed.
    for run_column in ("first_seen_run", "last_seen_run"):
        conn.execute(
            f"""INSERT OR IGNORE INTO signal_sightings(
                   record_id,run_id,observed_at,source_record_id,family,subtype,
                   ticker,direction,strength,regime_at_emission,as_of,asset_class,payload
                 )
                 SELECT s.record_id,s.{run_column},r.generated_at,s.source_record_id,
                        s.family,s.subtype,s.ticker,s.direction,s.strength,
                        s.regime_at_emission,s.as_of,s.asset_class,s.payload
                 FROM signals AS s JOIN runs AS r ON r.run_id=s.{run_column}"""
        )
    conn.commit()
    return conn
