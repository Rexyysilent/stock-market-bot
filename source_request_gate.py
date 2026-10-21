"""Shared, cross-process SEC pacing. One reservation for every actual attempt.

All local SEC consumers use this gate; concurrency is one reservation each 1/3s.
It does not coordinate other machines using the same upstream identity.
"""
from pathlib import Path
from contextlib import closing
import sqlite3
import time

DEFAULT_PATH = Path(__file__).resolve().parent / "state/sec-request-gate.db"


def wait_for_sec(*, path=DEFAULT_PATH, interval=1/3, clock=time.time, sleep=time.sleep):
    if not 1/10 <= interval <= 60:
        raise ValueError("SEC pacing interval outside safe bounds")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS rate_gate(key TEXT PRIMARY KEY, next_at REAL NOT NULL)")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT next_at FROM rate_gate WHERE key='sec'").fetchone()
        now = clock()
        scheduled = max(now, row[0] if row else now)
        # Clock movement or an unexpectedly long queue requires operator review.
        if scheduled - now > 30:
            raise RuntimeError("SEC request gate backlog or clock skew")
        conn.execute("INSERT OR REPLACE INTO rate_gate VALUES('sec',?)", (scheduled + interval,))
    if scheduled > now:
        sleep(scheduled - now)
