import sqlite3
import json
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger("Database")
DB_NAME = "market_memory.db"


def _get_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = _get_conn()
    c = conn.cursor()

    # Table: Sentiment Analysis Logs (The "Consensus")
    c.execute('''CREATE TABLE IF NOT EXISTS sentiment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    subject TEXT,
                    source TEXT,
                    raw_content TEXT,
                    sentiment_score REAL,
                    summary TEXT
                )''')

    # Table: Price History (Cache for WatcherAgent)
    c.execute('''CREATE TABLE IF NOT EXISTS price_cache (
                    symbol TEXT,
                    price REAL,
                    change_pct REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, timestamp)
                )''')

    # Table: Alerts History (Track triggered alerts)
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ticker TEXT,
                    alert_type TEXT,
                    message TEXT,
                    value REAL
                )''')

    # Table: Content Dedup (Prevent showing same stories twice)
    c.execute('''CREATE TABLE IF NOT EXISTS seen_content (
                    content_hash TEXT PRIMARY KEY,
                    source TEXT,
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')

    conn.commit()
    conn.close()
    logger.info(f"Database {DB_NAME} initialized.")


def log_sentiment(subject, source, content, score, summary):
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO sentiment_logs (subject, source, raw_content, sentiment_score, summary) VALUES (?, ?, ?, ?, ?)",
        (subject, source, content, score, summary)
    )
    conn.commit()
    conn.close()


def cache_price(symbol, price, change_pct=0.0):
    """Cache a price reading for historical tracking."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO price_cache (symbol, price, change_pct) VALUES (?, ?, ?)",
        (symbol, price, change_pct)
    )
    conn.commit()
    conn.close()


def log_alert(ticker, alert_type, message, value=0.0):
    """Log a triggered alert."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO alerts (ticker, alert_type, message, value) VALUES (?, ?, ?, ?)",
        (ticker, alert_type, message, value)
    )
    conn.commit()
    conn.close()


def get_recent_alerts(hours=24):
    """Get alerts from the last N hours."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT timestamp, ticker, alert_type, message, value FROM alerts WHERE timestamp > datetime('now', ?)",
        (f"-{hours} hours",)
    )
    rows = c.fetchall()
    conn.close()
    return rows


def is_content_seen(text, source="unknown"):
    """
    Check if content has been seen before (for dedup).
    Returns True if already seen, False if new.
    If new, marks it as seen.
    """
    content_hash = hashlib.md5(text.encode()).hexdigest()
    conn = _get_conn()
    c = conn.cursor()

    c.execute("SELECT 1 FROM seen_content WHERE content_hash = ?", (content_hash,))
    exists = c.fetchone() is not None

    if not exists:
        c.execute(
            "INSERT INTO seen_content (content_hash, source) VALUES (?, ?)",
            (content_hash, source)
        )
        conn.commit()

    conn.close()
    return exists


def cleanup_old_content(days=3):
    """Remove seen_content entries older than N days to prevent DB bloat."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "DELETE FROM seen_content WHERE first_seen < datetime('now', ?)",
        (f"-{days} days",)
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old dedup entries")


def export_for_notebooklm(limit=50):
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT timestamp, subject, source, sentiment_score, summary FROM sentiment_logs ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()

    filename = "market_intel_dump.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("MARKET INTELLIGENCE DUMP (FOR NOTEBOOKLM/GEMINI)\n")
        f.write("================================================\n\n")
        for r in rows:
            timestamp, subject, source, score, summary = r
            f.write(f"[{timestamp}] SOURCE: {source} | SUBJECT: {subject} | SENTIMENT: {score}\n")
            f.write(f"SUMMARY: {summary}\n")
            f.write("-" * 40 + "\n")

    return filename


if __name__ == "__main__":
    init_db()
