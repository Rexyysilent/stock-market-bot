"""Independent SQLite evidence/event store with atomic progress and local outbox."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .evidence import check_document
from .identities import digest, instant

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT, revision_id TEXT, first_observed_at TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(evidence_id, revision_id));
CREATE TABLE IF NOT EXISTS events(event_id TEXT, revision_id TEXT, first_observed_at TEXT NOT NULL,
 payload TEXT NOT NULL, decision TEXT NOT NULL, PRIMARY KEY(event_id, revision_id));
CREATE TABLE IF NOT EXISTS event_heads(event_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
 changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_evidence(event_id TEXT, revision_id TEXT, evidence_id TEXT,
 evidence_revision TEXT, PRIMARY KEY(event_id,revision_id,evidence_id,evidence_revision));
CREATE TABLE IF NOT EXISTS source_cycles(cycle_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, input_hash TEXT NOT NULL, health TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cursors(source_id TEXT PRIMARY KEY, watermark TEXT, continuation TEXT,
 pending_watermark TEXT, last_complete_at TEXT);
CREATE TABLE IF NOT EXISTS outbox(delivery_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
 revision_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', acknowledged_at TEXT);
CREATE TABLE IF NOT EXISTS reviews(review_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
 revision_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budgets(day TEXT NOT NULL, kind TEXT NOT NULL, used INTEGER NOT NULL,
 PRIMARY KEY(day, kind));
CREATE TABLE IF NOT EXISTS trial_snapshots(query_id TEXT, trial_id TEXT, revision_id TEXT,
 first_observed_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(query_id, trial_id, revision_id));
CREATE TABLE IF NOT EXISTS trial_heads(query_id TEXT, trial_id TEXT, revision_id TEXT NOT NULL,
 PRIMARY KEY(query_id, trial_id));
CREATE TABLE IF NOT EXISTS acquisition_queue(source_id TEXT, native_id TEXT, first_observed_at TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(source_id,native_id));
CREATE TABLE IF NOT EXISTS backoff(source_id TEXT PRIMARY KEY, retry_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trial_changes(change_id TEXT PRIMARY KEY, query_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_jobs(source_id TEXT, native_id TEXT, url TEXT,
 payload TEXT NOT NULL, PRIMARY KEY(source_id,native_id,url));
CREATE TABLE IF NOT EXISTS source_receipts(receipt_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_leads(lead_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_records(record_id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL,
 kind TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL);
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class EventStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        # Refuse accidental use of an existing production ledger/history DB.
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "metadata" not in tables:
            self.conn.close()
            raise ValueError("not an OMNI database")
        if tables:
            version = self.conn.execute("SELECT value FROM metadata WHERE key='contract'").fetchone()
            if version is None or version[0] != "omni-store-1":
                self.conn.close()
                raise ValueError("unsupported OMNI database")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO event_heads SELECT e.event_id,e.revision_id,e.first_observed_at FROM events e WHERE e.rowid=(SELECT max(n.rowid) FROM events n WHERE n.event_id=e.event_id)")
        self.conn.execute("INSERT OR IGNORE INTO metadata VALUES('contract','omni-store-1')")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def cursor(self, source_id):
        row = self.conn.execute("SELECT * FROM cursors WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else {"source_id": source_id, "watermark": None, "continuation": None, "pending_watermark": None, "last_complete_at": None}

    def commit_cycle(self, *, cycle_id, source_id, observed_at, documents, events, decisions,
                     watermark=None, complete=False, continuation=None, gaps=(), metrics=None, fail_at=None,
                     candidates=(), initial_seed=False, document_jobs=(), source_receipts=(), leads=(), manage_transaction=True, preserve_cursor=False):
        instant(observed_at)
        if watermark:
            instant(watermark)
        if len(events) != len(decisions):
            raise ValueError("every event needs a decision")
        for doc in documents:
            check_document(doc)
        for event, decision in zip(events, decisions):
            if event.get("signal_eligible") is not False or decision.get("signal_status") != "ineligible":
                raise ValueError("event store cannot promote production signals")
        health = {"status": "complete" if complete and not gaps else "partial", "gaps": sorted(set(gaps)),
                  "metrics": metrics or {}, "watermark_candidate": watermark, "continuation": continuation}
        health["initial_seed"] = initial_seed
        input_hash = digest([source_id, documents, events, decisions, health, candidates, document_jobs, source_receipts,leads,preserve_cursor])
        inserted = 0
        try:
            if manage_transaction: self.conn.execute("BEGIN IMMEDIATE")
            elif not self.conn.in_transaction: raise ValueError("outer transaction required")
            prior = self.conn.execute("SELECT input_hash FROM source_cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
            if prior:
                if prior[0] != input_hash:
                    raise ValueError("cycle ID reused with different input")
                if manage_transaction: self.conn.rollback()
                return {"status": "duplicate_cycle", "new_events": 0}
            for job in document_jobs:
                self.conn.execute("INSERT OR REPLACE INTO document_jobs VALUES(?,?,?,?)",(
                    source_id,job["native_id"],job["url"],encode(job)))
            for receipt in source_receipts:
                self.conn.execute("INSERT OR IGNORE INTO source_receipts VALUES(?,?,?,?)",(
                    receipt["receipt_id"],source_id,observed_at,encode(receipt)))
            for lead in leads:
                self.conn.execute("INSERT OR IGNORE INTO discovery_leads VALUES(?,?,?,?)",(
                    lead["lead_id"],source_id,observed_at,encode(lead)))
            for doc in documents:
                # The first observation of an evidence identity never advances on rerun.
                first = self.conn.execute("SELECT min(first_observed_at) FROM evidence WHERE evidence_id=?", (doc["evidence_id"],)).fetchone()[0]
                self.conn.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)", (
                    doc["evidence_id"], doc["revision_id"], first or doc["first_observed_at"], encode(doc)))
            for row in candidates:
                self.conn.execute("INSERT INTO acquisition_queue VALUES(?,?,?,?,?) ON CONFLICT(source_id,native_id) DO UPDATE SET payload=excluded.payload,state=excluded.state", (
                    source_id, row["native_id"], observed_at, encode(row), row.get("hydration_status", "metadata_only")))
            for event, decision in zip(events, decisions):
                first = self.conn.execute("SELECT min(first_observed_at) FROM events WHERE event_id=?", (event["event_id"],)).fetchone()[0]
                head = self.conn.execute("SELECT revision_id FROM event_heads WHERE event_id=?", (event["event_id"],)).fetchone()
                changed = head is None or head[0] != event["event_revision"]
                wrote = self.conn.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)", (
                    event["event_id"], event["event_revision"], first or event["first_observed_at"], encode(event), encode(decision))).rowcount
                inserted += wrote
                for claim in event["claims"]:
                    support=claim["support"]
                    self.conn.execute("INSERT OR IGNORE INTO event_evidence VALUES(?,?,?,?)", (
                        event["event_id"],event["event_revision"],support["evidence_id"],support["revision_id"]))
                if changed:
                    self.conn.execute("INSERT OR REPLACE INTO event_heads VALUES(?,?,?)", (event["event_id"],event["event_revision"],observed_at))
                if changed and decision["editorial_status"] != "suppressed":
                    delivery_id = "review:" + digest([event["event_id"], event["event_revision"]])
                    self.conn.execute("INSERT OR IGNORE INTO outbox(delivery_id,event_id,revision_id) VALUES(?,?,?)", (
                        delivery_id, event["event_id"], event["event_revision"]))
                    self.conn.execute("UPDATE outbox SET state='pending',acknowledged_at=NULL WHERE delivery_id=?", (delivery_id,))
            if fail_at == "after_events":
                raise RuntimeError("injected crash before progress commit")
            old = self.cursor(source_id)
            next_mark = watermark if complete and not gaps else old["watermark"]
            if initial_seed and old["watermark"] is None and set(gaps) <= {"initial_window_only"}:
                # Seed defines a new observed interval; it is not backfilled history.
                next_mark = watermark
            if old["watermark"] and next_mark and instant(next_mark) < instant(old["watermark"]):
                raise ValueError("watermark cannot move backward")
            pending = max(filter(None, (old["pending_watermark"], watermark)), key=instant, default=None)
            if not preserve_cursor:
                self.conn.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?,?,?)", (
                    source_id, next_mark, continuation, pending if not complete or gaps else None,
                    observed_at if complete and not gaps else old["last_complete_at"]))
            self.conn.execute("INSERT INTO source_cycles VALUES(?,?,?,?,?)", (
                cycle_id, source_id, observed_at, input_hash, encode(health)))
            if manage_transaction: self.conn.commit()
            return {"status": health["status"], "new_events": inserted}
        except BaseException:
            if manage_transaction: self.conn.rollback()
            raise

    def pending(self, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError("invalid review queue limit")
        return [dict(r) for r in self.conn.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY rowid LIMIT ?", (limit,))]

    def backlog(self, source_id, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError("invalid backlog limit")
        return [json.loads(r[0]) for r in self.conn.execute(
            "SELECT payload FROM acquisition_queue WHERE source_id=? AND state NOT IN ('parsed','not_applicable') ORDER BY first_observed_at,native_id LIMIT ?", (source_id,limit))]

    def candidate(self, source_id, native_id):
        row=self.conn.execute("SELECT payload FROM acquisition_queue WHERE source_id=? AND native_id=?",(source_id,native_id)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self, source_id, native_id):
        return {r[0]:json.loads(r[1]) for r in self.conn.execute(
            "SELECT url,payload FROM document_jobs WHERE source_id=? AND native_id=?",(source_id,native_id))}

    def backlog_counts(self, source_id):
        return {r[0]:r[1] for r in self.conn.execute(
            "SELECT state,count(*) FROM acquisition_queue WHERE source_id=? GROUP BY state",(source_id,))}

    def acknowledge(self, delivery_id, *, at):
        instant(at)
        with self.conn:
            self.conn.execute("UPDATE outbox SET state='acknowledged',acknowledged_at=COALESCE(acknowledged_at,?) WHERE delivery_id=?", (at, delivery_id))

    def reserve(self, kind, *, day, limit):
        """Persist a budget reservation before a network attempt, including retries."""
        from datetime import date
        date.fromisoformat(day)
        if kind not in ("metadata", "document", "attempt") or type(limit) is not int or limit < 0:
            raise ValueError("invalid budget")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT used FROM budgets WHERE day=? AND kind=?", (day, kind)).fetchone()
            used = row[0] if row else 0
            if used >= limit:
                self.conn.rollback()
                return False
            self.conn.execute("INSERT OR REPLACE INTO budgets VALUES(?,?,?)", (day, kind, used+1))
            self.conn.commit()
            return True
        except BaseException:
            self.conn.rollback()
            raise

    def backoff(self, source_id, retry_at=None):
        if retry_at is not None:
            instant(retry_at)
            with self.conn:
                self.conn.execute("INSERT OR REPLACE INTO backoff VALUES(?,?)", (source_id, retry_at))
        row = self.conn.execute("SELECT retry_at FROM backoff WHERE source_id=?", (source_id,)).fetchone()
        return row[0] if row else None

    def inventory(self):
        return {table: self.conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in ("evidence", "events", "source_cycles", "outbox", "reviews", "trial_snapshots", "trial_changes", "acquisition_queue", "document_jobs", "source_receipts", "discovery_leads", "pilot_records")}
