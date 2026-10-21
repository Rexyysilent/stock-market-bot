"""Additive OMNI extension migration and verified backup/restore to a new path."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

VERSION = "omni-extension-schema-1"
STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS omni_receipts(receipt_id TEXT PRIMARY KEY,kind TEXT NOT NULL,observed_at TEXT NOT NULL,payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS source_heads(source_id TEXT,native_id TEXT,observed_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(source_id,native_id))",
    "CREATE TABLE IF NOT EXISTS event_change_links(event_id TEXT,receipt_id TEXT,PRIMARY KEY(event_id,receipt_id))",
    "CREATE TABLE IF NOT EXISTS evidence_heads(evidence_id TEXT PRIMARY KEY,revision_id TEXT NOT NULL,observed_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS event_versions(event_id TEXT,revision_id TEXT,implementation_hash TEXT NOT NULL,policy_hash TEXT NOT NULL,PRIMARY KEY(event_id,revision_id))",
    "CREATE TABLE IF NOT EXISTS event_packets(event_id TEXT,revision_id TEXT,packet_hash TEXT,payload TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(event_id,revision_id,packet_hash))",
    "CREATE TABLE IF NOT EXISTS event_packet_heads(event_id TEXT,revision_id TEXT,packet_hash TEXT NOT NULL,PRIMARY KEY(event_id,revision_id))",
    "CREATE TRIGGER IF NOT EXISTS omni_receipts_immutable_update BEFORE UPDATE ON omni_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END",
    "CREATE TRIGGER IF NOT EXISTS omni_receipts_immutable_delete BEFORE DELETE ON omni_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END",
)


def upgrade(conn):
    prior = conn.execute("SELECT value FROM metadata WHERE key='extensions_contract'").fetchone()
    if prior and prior[0] != VERSION:
        raise ValueError("unsupported OMNI extension schema")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in STATEMENTS:
            conn.execute(statement)
        if not prior:
            conn.execute("INSERT INTO metadata VALUES('extensions_contract',?)", (VERSION,))
            conn.execute("INSERT INTO metadata VALUES('extensions_migrated_at',?)", (datetime.now(timezone.utc).isoformat(),))
        # Existing evidence and reviews are retained verbatim. Heads are a
        # conservative index, never a retroactive approval or prospective proof.
        conn.execute("INSERT OR IGNORE INTO evidence_heads SELECT e.evidence_id,e.revision_id,e.first_observed_at FROM evidence e WHERE e.rowid=(SELECT max(n.rowid) FROM evidence n WHERE n.evidence_id=e.evidence_id)")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"schema": VERSION, "status": "already_current" if prior else "upgraded", "legacy_records_rewritten": False}


def backup(store, destination):
    path = Path(destination)
    if path.exists():
        raise ValueError("backup destination already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(path)
    try:
        store.conn.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup integrity failure")
        target.close()
    except BaseException:
        target.close()
        path.unlink(missing_ok=True)
        raise
    return {"contract": "omni-backup-1", "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "method": "sqlite_online_backup", "original_preserved": True}


def restore(backup_path, destination, *, expected_sha256):
    source, target = Path(backup_path).resolve(), Path(destination)
    if target.exists():
        raise ValueError("restore requires a new destination; existing store is preserved")
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("backup hash mismatch")
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup integrity failure")
        if conn.execute("SELECT value FROM metadata WHERE key='contract'").fetchone()[0] != "omni-store-1":
            raise ValueError("backup is not an OMNI store")
    finally:
        conn.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(raw)
    return {"status": "restored", "path": str(target), "sha256": expected_sha256, "original_preserved": True}


def backup_existing(source_path, destination):
    """Back up a legacy store without opening EventStore or running migrations."""
    source = Path(source_path).resolve()
    conn = sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)
    try:
        row=conn.execute("SELECT value FROM metadata WHERE key='contract'").fetchone()
        if row is None or row[0]!="omni-store-1":
            raise ValueError("not an OMNI database")
        class ReadOnlyStore:
            pass
        holder=ReadOnlyStore(); holder.conn=conn
        return backup(holder,destination)
    finally:
        conn.close()
