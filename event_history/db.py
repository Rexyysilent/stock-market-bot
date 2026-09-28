"""Sidecar storage for rebuildable evidence history (T7).

A separate SQLite file, never the ledger. Every table is append-only; a
correction is a new row that names what it supersedes. The schema starts from
the audit bundle's proposed DDL with three deliberate changes:

- capture-time policy lives on each sighting, not on the evidence version, so
  identical content captured under different permissions stays attributable
  to each original capture instead of whichever was indexed first;
- archives are identified by exact bytes, and every path a copy was found at
  is kept separately (one identity, many locations);
- resolvers (rule versions) are registered with the time they came into
  existence, so a later linker can never pose as earlier interpretation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

CONTRACT_VERSION = "event-history-1"
DEFAULT_DB = Path("history") / "event_history.db"
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
STRICT_KNOWLEDGE = ("explicit_observation", "archive_bound")

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archives (
  archive_sha TEXT PRIMARY KEY CHECK(length(archive_sha)=64),
  byte_length INTEGER NOT NULL,
  producer_run_id TEXT,
  generated_at_raw TEXT,
  observed_at TEXT,
  knowledge_quality TEXT NOT NULL CHECK(knowledge_quality IN
    ('explicit_observation','archive_bound','legacy_inferred','unknown')),
  schema_version TEXT,
  pipeline_version TEXT,
  adapter TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('indexed','quarantined')),
  status_reason TEXT,
  components_json TEXT NOT NULL CHECK(json_valid(components_json)),
  indexed_at TEXT NOT NULL,
  CHECK(knowledge_quality NOT IN ('explicit_observation','archive_bound') OR observed_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS archive_paths (
  archive_sha TEXT NOT NULL REFERENCES archives(archive_sha),
  path TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY(archive_sha, path)
);
CREATE TABLE IF NOT EXISTS archive_annotations (
  annotation_id TEXT PRIMARY KEY,
  archive_sha TEXT NOT NULL REFERENCES archives(archive_sha),
  kind TEXT NOT NULL CHECK(kind IN ('observed_at_not_before')),
  value TEXT NOT NULL,
  reason TEXT NOT NULL,
  decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_versions (
  version_id TEXT PRIMARY KEY,
  native_evidence_id TEXT,
  native_revision_id TEXT,
  origin_hint TEXT,
  source_key TEXT NOT NULL,
  content_sha TEXT NOT NULL CHECK(length(content_sha)=64),
  kind TEXT NOT NULL CHECK(kind IN ('filing','news','registry','measurement','notice','context')),
  subject_id TEXT,
  publication_lo TEXT,
  publication_hi TEXT,
  event_lo TEXT,
  event_hi TEXT,
  publication_precision TEXT NOT NULL CHECK(publication_precision IN ('instant','day','month','interval','unknown')),
  event_precision TEXT NOT NULL CHECK(event_precision IN ('instant','day','month','interval','unknown')),
  normalizer_version TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
  UNIQUE(source_key, content_sha, normalizer_version),
  CHECK(publication_lo IS NULL OR publication_hi IS NULL OR publication_lo<=publication_hi),
  CHECK(event_lo IS NULL OR event_hi IS NULL OR event_lo<=event_hi)
);
CREATE TABLE IF NOT EXISTS sightings (
  archive_sha TEXT NOT NULL REFERENCES archives(archive_sha),
  json_pointer TEXT NOT NULL,
  version_id TEXT NOT NULL REFERENCES evidence_versions(version_id),
  observed_at TEXT,
  knowledge_quality TEXT NOT NULL CHECK(knowledge_quality IN
    ('explicit_observation','archive_bound','legacy_inferred','unknown')),
  capture_policy_json TEXT NOT NULL CHECK(json_valid(capture_policy_json)),
  PRIMARY KEY(archive_sha, json_pointer),
  CHECK(knowledge_quality NOT IN ('explicit_observation','archive_bound') OR observed_at IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS resolvers (
  rule_version TEXT PRIMARY KEY,
  introduced_at TEXT NOT NULL,
  description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  anchor_key TEXT NOT NULL UNIQUE,
  subject_id TEXT,
  event_kind TEXT NOT NULL,
  provisional INTEGER NOT NULL CHECK(provisional IN (0,1)),
  created_at TEXT NOT NULL,
  rule_version TEXT NOT NULL REFERENCES resolvers(rule_version)
);
CREATE TABLE IF NOT EXISTS event_memberships (
  membership_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  version_id TEXT NOT NULL REFERENCES evidence_versions(version_id),
  role TEXT NOT NULL CHECK(role IN ('primary_assertion','reports_on','measurement','context')),
  decision TEXT NOT NULL CHECK(decision IN ('accepted','candidate','retracted')),
  decision_at TEXT NOT NULL,
  rule_version TEXT NOT NULL REFERENCES resolvers(rule_version),
  supersedes_membership_id TEXT REFERENCES event_memberships(membership_id)
);
CREATE TABLE IF NOT EXISTS evidence_relations (
  relation_id TEXT PRIMARY KEY,
  from_version_id TEXT NOT NULL REFERENCES evidence_versions(version_id),
  to_version_id TEXT NOT NULL REFERENCES evidence_versions(version_id),
  relation_type TEXT NOT NULL CHECK(relation_type IN
    ('reproduces','derived_from','amends','contradicts','resolves','supersedes_claim')),
  from_claim_pointer TEXT,
  to_claim_pointer TEXT,
  decision TEXT NOT NULL CHECK(decision IN ('accepted','candidate','retracted')),
  decision_at TEXT NOT NULL,
  rule_version TEXT NOT NULL REFERENCES resolvers(rule_version),
  supersedes_relation_id TEXT REFERENCES evidence_relations(relation_id),
  CHECK(from_version_id<>to_version_id),
  CHECK(relation_type<>'supersedes_claim' OR (from_claim_pointer IS NOT NULL AND to_claim_pointer IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS event_relations (
  relation_id TEXT PRIMARY KEY,
  from_event_id TEXT NOT NULL REFERENCES events(event_id),
  to_event_id TEXT NOT NULL REFERENCES events(event_id),
  relation_type TEXT NOT NULL CHECK(relation_type IN
    ('alias_of','merge_into','split_into','candidate_same_event')),
  decision TEXT NOT NULL CHECK(decision IN ('accepted','candidate','retracted')),
  decision_at TEXT NOT NULL,
  rule_version TEXT NOT NULL REFERENCES resolvers(rule_version),
  supersedes_relation_id TEXT REFERENCES event_relations(relation_id),
  CHECK(from_event_id<>to_event_id)
);
CREATE INDEX IF NOT EXISTS sightings_version ON sightings(version_id);
CREATE INDEX IF NOT EXISTS sightings_known ON sightings(observed_at, knowledge_quality);
CREATE INDEX IF NOT EXISTS evidence_source ON evidence_versions(source_key);
CREATE INDEX IF NOT EXISTS memberships_event ON event_memberships(event_id, decision_at);
CREATE INDEX IF NOT EXISTS relations_target ON evidence_relations(to_version_id, decision_at);
"""

_APPEND_ONLY = ("archives", "archive_paths", "archive_annotations", "evidence_versions",
                "sightings", "resolvers", "events", "event_memberships",
                "evidence_relations", "event_relations")


def _triggers():
    statements = []
    for table in _APPEND_ONLY:
        for action in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                f"BEFORE {action} ON {table} BEGIN "
                f"SELECT RAISE(ABORT,'append-only {table}'); END;"
            )
    return "\n".join(statements)


def connect(path=DEFAULT_DB):
    """Open (creating if needed) a sidecar database of this contract version."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_TABLES + _triggers())
    row = conn.execute("SELECT value FROM meta WHERE key='contract_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta VALUES ('contract_version', ?)", (CONTRACT_VERSION,))
    elif row["value"] != CONTRACT_VERSION:
        conn.close()
        raise ValueError(f"event history contract {row['value']!r} is not {CONTRACT_VERSION!r}")
    return conn


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value):
    return sha256_text(canonical(value))
