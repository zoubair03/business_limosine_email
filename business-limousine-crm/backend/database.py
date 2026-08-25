"""
SQLite persistence layer.

Uses the stdlib sqlite3 module directly (no ORM) to keep the project
dependency-light and easy to inspect/debug with any SQLite browser.
"""
import sqlite3
from contextlib import contextmanager

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'DISPATCHER' CHECK (role IN ('ADMIN', 'DISPATCHER', 'DRIVER', 'ACCOUNTANT')),
    avatar_color  TEXT DEFAULT '#C5A059',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email);

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
    -- Mailbox state, independent of the dispatch status above. A conversation can
    -- be CONFIRMED and still unread, or NEW_REQUEST and already triaged.
    is_read         INTEGER NOT NULL DEFAULT 0,
    is_starred      INTEGER NOT NULL DEFAULT 0,
    is_archived     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_email
    ON conversations (client_email);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_id      TEXT UNIQUE,
    in_reply_to     TEXT,
    from_addr       TEXT,
    to_addr         TEXT,
    cc_addr         TEXT,
    subject         TEXT,
    body_text       TEXT,
    body_html       TEXT,
    attachments     TEXT,
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
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
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

-- System-wide application settings (e.g., WhatsApp / Telegram dispatch alert rules)
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Layer 1 of the pre-AI email filter: exact domain or email matches here
-- are auto-classified as OTHER with zero Gemini calls. hit_count/last_hit_at
-- let the UI show which rules are actually earning their keep.
CREATE TABLE IF NOT EXISTS blocked_senders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pattern_type  TEXT NOT NULL DEFAULT 'domain' CHECK (pattern_type IN ('domain', 'email')),
    reason        TEXT,
    hit_count     INTEGER NOT NULL DEFAULT 0,
    last_hit_at   TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blocked_senders_pattern ON blocked_senders (pattern);

-- Layer 3 of the pre-AI email filter: exact domain or email matches here
-- always skip the blocklist/heuristics and go straight to Gemini, e.g.
-- known corporate accounts (hotels, partner agencies) you never want
-- misclassified as noise.
CREATE TABLE IF NOT EXISTS allowed_senders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pattern_type  TEXT NOT NULL DEFAULT 'domain' CHECK (pattern_type IN ('domain', 'email')),
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_allowed_senders_pattern ON allowed_senders (pattern);
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


def _add_column(conn, table, column, ddl):
    """Adds a column if it isn't there yet. Returns True if it was just added.

    Checked against PRAGMA table_info rather than catching the duplicate-column
    error, so that a real problem — a locked file, a corrupt database — is raised
    instead of being mistaken for "already migrated".
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def init_db():
    with db_session() as conn:
        try:
            conn.execute("DROP INDEX IF EXISTS idx_conversations_email")
        except Exception:
            pass
        conn.executescript(SCHEMA)

        # Mailbox state on existing databases. Unlike the blanket try/except
        # migrations below, this checks the column list first, so a genuine failure
        # (a locked database, a disk error) surfaces instead of being swallowed.
        added_read = _add_column(conn, "conversations", "is_read", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "conversations", "is_starred", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "conversations", "is_archived", "INTEGER NOT NULL DEFAULT 0")

        # Everything already in the book predates read tracking. Marking it all read
        # is the honest default: flagging years of history as unread would bury
        # whatever actually arrived today. Only on the migration itself — after that,
        # unread is meaningful and must not be reset.
        if added_read:
            conn.execute("UPDATE conversations SET is_read = 1")

        # Where the message lives in the mailbox, so an attachment can be streamed
        # from IMAP on demand instead of being copied to disk at sync time.
        _add_column(conn, "messages", "imap_uid", "TEXT")
        _add_column(conn, "messages", "imap_folder", "TEXT")

        # Created here rather than in SCHEMA: on an existing database the columns
        # above do not exist until the ALTERs have run, and CREATE TABLE IF NOT
        # EXISTS is a no-op, so an index in SCHEMA referencing them fails outright.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_recent "
            "ON conversations (is_archived, status, last_message_at DESC)"
        )

        # Migrations: ensure user_id column exists on existing notes table if table existed
        try:
            conn.execute("ALTER TABLE notes ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN whatsapp_alert_sent INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN cc_addr TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")
        except Exception:
            pass

        # Migrate existing Doccle & eBox messages and conversations
        try:
            conn.execute("""
                UPDATE messages SET ai_category = 'DOCCLE'
                WHERE (lower(from_addr) LIKE '%doccle%' OR lower(from_addr) LIKE '%doccer%' OR lower(subject) LIKE '%doccle%' OR lower(subject) LIKE '%doccer%')
                  AND (ai_category IS NULL OR ai_category = 'OTHER')
            """)
            conn.execute("""
                UPDATE conversations SET status = 'DOCCLE', client_name = 'Doccle'
                WHERE (lower(client_email) LIKE '%doccle%' OR lower(client_email) LIKE '%doccer%')
                  AND status = 'OTHER'
            """)
            conn.execute("""
                UPDATE messages SET ai_category = 'EBOX'
                WHERE (lower(from_addr) LIKE '%bosa.fgov.be%' OR lower(from_addr) LIKE '%myebox%' OR lower(from_addr) LIKE '%ebox%' OR lower(subject) LIKE '%ebox%')
                  AND (ai_category IS NULL OR ai_category = 'OTHER')
            """)
            conn.execute("""
                UPDATE conversations SET status = 'EBOX', client_name = 'eBox'
                WHERE (lower(client_email) LIKE '%bosa.fgov.be%' OR lower(client_email) LIKE '%myebox%' OR lower(client_email) LIKE '%ebox%')
                  AND status = 'OTHER'
            """)
        except Exception:
            pass


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {Config.DB_PATH}")