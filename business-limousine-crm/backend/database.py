"""
SQLite persistence layer.

Uses the stdlib sqlite3 module directly (no ORM) to keep the project
dependency-light and easy to inspect/debug with any SQLite browser.
"""
import sqlite3
from contextlib import contextmanager

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name     TEXT,
    client_email    TEXT NOT NULL,
    client_phone    TEXT,
    status          TEXT NOT NULL DEFAULT 'NEW_REQUEST',
    trip_date       TEXT,
    origin          TEXT,
    destination     TEXT,
    last_message_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_email
    ON conversations (client_email);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_id      TEXT UNIQUE,
    in_reply_to     TEXT,
    from_addr       TEXT,
    to_addr         TEXT,
    subject         TEXT,
    body_text       TEXT,
    body_html       TEXT,
    ai_category     TEXT,
    ai_confidence   REAL,
    received_at     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, received_at);

CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    author          TEXT,
    note_text       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_conversation
    ON notes (conversation_id, created_at);

-- generic key/value store, currently used to remember the last IMAP UID
-- we processed so re-running the sync never re-imports old mail.
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_connection():
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """Context manager that yields a connection and commits/rolls back."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_session() as conn:
        try:
            conn.execute("DROP INDEX IF EXISTS idx_conversations_email")
        except Exception:
            pass
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {Config.DB_PATH}")
