"""The price-cache tables have one definition (ledger/prices.py).

price_acquisitions, price_points and price_windows were defined in both
ledger/db.py SCHEMA and ledger/prices.py _CACHE_SCHEMA. They matched, but an
edit to one copy would silently drift from the other.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ledger.db as db
import ledger.prices as prices

TABLES = ("price_acquisitions", "price_points", "price_windows")
INDEX = "idx_price_acquisitions_ticker_range"


def _columns(conn, table):
    return [(r[1], r[2], r[3], r[5]) for r in conn.execute(f"PRAGMA table_info({table})")]


class PriceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ledger.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_ledger_schema_does_not_define_the_price_cache(self):
        for table in TABLES:
            self.assertNotIn(f"CREATE TABLE IF NOT EXISTS {table}", db.SCHEMA)
        self.assertNotIn(INDEX, db.SCHEMA)

    def test_connect_creates_the_price_cache_from_its_single_definition(self):
        conn = db.connect(self.path)
        reference = sqlite3.connect(":memory:")
        reference.executescript(prices._CACHE_SCHEMA)
        try:
            for table in TABLES:
                self.assertEqual(_columns(conn, table), _columns(reference, table), table)
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(price_acquisitions)")}
            self.assertIn(INDEX, indexes)
        finally:
            conn.close()
            reference.close()

    def test_reopening_an_existing_ledger_works(self):
        db.connect(self.path).close()
        conn = db.connect(self.path)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_prices_does_not_import_the_ledger_db_module(self):
        source = Path(prices.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .db", source)
        self.assertNotIn("import ledger.db", source)


if __name__ == "__main__":
    unittest.main()
