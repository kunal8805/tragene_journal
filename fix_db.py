import sqlite3
import os

files = ['trading_journal.db', 'tragene.db', os.path.join('instance', 'tragene.db')]

for f in files:
    if not os.path.exists(f):
        print(f"[SKIP] {f} - file not found")
        continue
    try:
        conn = sqlite3.connect(f)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trade'")
        if not c.fetchone():
            print(f"[SKIP] {f} - no 'trade' table")
            conn.close()
            continue
        c.execute("PRAGMA table_info(trade)")
        cols = [row[1] for row in c.fetchall()]
        if 'platform_ticket' in cols:
            print(f"[ALREADY EXISTS] {f} - platform_ticket already present")
        else:
            c.execute("ALTER TABLE trade ADD COLUMN platform_ticket VARCHAR(50)")
            conn.commit()
            print(f"[ADDED] {f} - platform_ticket column added")
        c.execute("SELECT COUNT(*) FROM trade")
        count = c.fetchone()[0]
        print(f"        -> {f} has {count} trade rows")
        conn.close()
    except Exception as e:
        print(f"[ERROR] {f} - {e}")
