import sqlite3
from pathlib import Path

DB_PATH = Path("dashboard/history.db")

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Create table if missing
    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT,
        diagnosis TEXT,
        created_ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # --- MIGRATION: ensure 'key' column exists ---
    cur.execute("PRAGMA table_info(history)")
    cols = [row[1] for row in cur.fetchall()]

    if "key" not in cols:
        # add missing column
        cur.execute("ALTER TABLE history ADD COLUMN key TEXT")
        conn.commit()

    conn.commit()
    conn.close()


def save_history(key: str, diagnosis: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO history (key, diagnosis) VALUES (?, ?)",
        (key, diagnosis)
    )
    conn.commit()
    conn.close()


def load_history(limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, key, diagnosis, created_ts
        FROM history
        ORDER BY created_ts DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()
    return rows
