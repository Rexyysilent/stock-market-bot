"""Content-bound append-only local receipts. References are evidence, not identity authentication."""
from __future__ import annotations

from contextlib import contextmanager
import json
import re

from .identities import digest, instant

VERSION = "omni-receipt-1"
MAX_RECEIPT_BYTES = 2_000_000


@contextmanager
def transaction(store, *, manage_transaction=True):
    if manage_transaction:
        if store.conn.in_transaction:
            raise ValueError("explicit outer transaction already active")
        store.conn.execute("BEGIN IMMEDIATE")
    elif not store.conn.in_transaction:
        raise ValueError("outer transaction required")
    try:
        yield
        if manage_transaction:
            store.conn.commit()
    except BaseException:
        if manage_transaction:
            store.conn.rollback()
        raise


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def put_receipt(store, kind, payload, *, observed_at, refs=(), receipt_id=None, manage_transaction=True):
    instant(observed_at)
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", kind):
        raise ValueError("invalid receipt kind")
    if not isinstance(payload, dict) or not isinstance(refs, (tuple, list)) or len(refs) > 10000:
        raise ValueError("invalid receipt payload or references")
    if any(not isinstance(r, str) or not r for r in refs) or len(set(refs)) != len(refs):
        raise ValueError("references must be unique receipt identifiers")
    core = {"contract": VERSION, "kind": kind, "observed_at": observed_at,
            "payload": payload, "refs": sorted(refs)}
    identifier = receipt_id or "receipt:" + kind + ":" + digest(core)
    if not isinstance(identifier, str) or not identifier or len(identifier) > 250:
        raise ValueError("invalid receipt identity")
    core["receipt_id"] = identifier
    record = {**core, "record_hash": digest(core)}
    serialized = _json(record)
    if len(serialized.encode()) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt size limit")
    with transaction(store, manage_transaction=manage_transaction):
        previous = store.conn.execute("SELECT payload FROM omni_receipts WHERE receipt_id=?", (identifier,)).fetchone()
        if previous:
            if previous[0] != serialized:
                raise ValueError("receipt ID reused with different input")
            return get_receipt(store, identifier)
        for ref in refs:
            prior = get_receipt(store, ref)
            if instant(prior["observed_at"]) > instant(observed_at):
                raise ValueError("receipt reference is from the future")
        store.conn.execute("INSERT INTO omni_receipts VALUES(?,?,?,?)", (identifier, kind, observed_at, serialized))
    return record


def get_receipt(store, receipt_id, *, kind=None):
    if not isinstance(receipt_id,str) or not receipt_id or len(receipt_id)>250:
        raise ValueError("exact receipt identifier required")
    if kind is not None and (not isinstance(kind,str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}",kind)):
        raise ValueError("invalid receipt kind")
    row = store.conn.execute("SELECT kind,observed_at,payload FROM omni_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
    if not row:
        raise ValueError("unknown receipt reference: " + str(receipt_id)[:160])
    record = json.loads(row[2])
    core = {k: v for k, v in record.items() if k != "record_hash"}
    if (record.get("contract") != VERSION or record.get("record_hash") != digest(core)
            or record.get("receipt_id") != receipt_id or record.get("kind") != row[0]
            or record.get("observed_at") != row[1] or (kind is not None and row[0] != kind)):
        raise ValueError("receipt integrity or kind mismatch")
    instant(record["observed_at"])
    for ref in record.get("refs", []):
        if not store.conn.execute("SELECT 1 FROM omni_receipts WHERE receipt_id=?", (ref,)).fetchone():
            raise ValueError("dangling receipt reference")
    return record


def list_receipts(store, *, kind=None):
    sql = "SELECT receipt_id FROM omni_receipts"
    params = ()
    if kind is not None:
        sql += " WHERE kind=?"
        params = (kind,)
    return [get_receipt(store, r[0], kind=kind) for r in store.conn.execute(sql + " ORDER BY rowid", params)]


def import_receipt(store, record):
    """Import an exact envelope; changed content cannot retain a receipt identity."""
    if (not isinstance(record, dict) or record.get("contract") != VERSION
            or set(record) != {"contract", "kind", "observed_at", "payload", "refs", "receipt_id", "record_hash"}):
        raise ValueError("invalid receipt envelope")
    if record.get("record_hash") != digest({k: v for k, v in record.items() if k != "record_hash"}):
        raise ValueError("receipt hash mismatch")
    result = put_receipt(store, record["kind"], record["payload"], observed_at=record["observed_at"],
                         refs=record["refs"], receipt_id=record["receipt_id"])
    if result != record:
        raise ValueError("receipt envelope contains unsupported fields")
    return result
