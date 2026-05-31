from pathlib import Path
import sqlite3

db_path = Path(__file__).with_name("market_memory.db")

if not db_path.exists():
    print(f"No local database found at {db_path}")
    raise SystemExit(0)

with sqlite3.connect(db_path) as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        print("No tables found.")
        raise SystemExit(0)

    print("Tables:")
    for table in tables:
        safe_table = table.replace('"', '""')
        cursor.execute(f'SELECT COUNT(*) FROM "{safe_table}";')
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} rows")
