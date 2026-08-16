"""
Data access layer sitting on top of database.py.

Every function opens its own short-lived connection via db_session(),
so this module is safe to call both from the Flask request handlers
and from the background sync worker.
"""
from datetime import datetime, timezone

from database import db_session

# Statuses that the AI classifier / auto-sync are allowed to set on their
# own. Anything a staff member sets manually (e.g. CONFIRMED, CLOSED) is
# never overwritten by an incoming email.
AUTO_MANAGED_STATUSES = {"NEW_REQUEST", "DISCUSSION", "OTHER"}

ALL_STATUSES = ["NEW_REQUEST", "DISCUSSION", "CONFIRMED", "CLOSED", "OTHER"]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------

def get_conversation_by_email(conn, client_email):
    row = conn.execute(
        "SELECT * FROM conversations WHERE client_email = ?", (client_email,)
    ).fetchone()
    return row


def get_conversation(conn, conversation_id):
    return conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()


def create_conversation(conn, client_email, client_name=None, status="NEW_REQUEST"):
    """Create a brand new conversation thread for a new booking/request."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO conversations
            (client_name, client_email, status, created_at, updated_at, last_message_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (client_name, client_email, status, now, now, now),
    )
    return cur.lastrowid


def find_conversation_by_thread_headers(conn, in_reply_to=None, references=None):
    """
    Looks for any existing message matching the In-Reply-To or References headers.
    Returns the conversation_id if found, else None.
    """
    candidates = []
    if in_reply_to:
        candidates.append(in_reply_to.strip())
    if references:
        candidates.extend([r.strip() for r in references.split() if r.strip()])

    for mid in candidates:
        row = conn.execute(
            "SELECT conversation_id FROM messages WHERE message_id = ?", (mid,)
        ).fetchone()
        if row:
            return row["conversation_id"]
    return None


def find_active_conversation_by_email(conn, client_email, status=None):
    """Finds an existing open conversation for this client email."""
    if status == "OTHER":
        row = conn.execute(
            "SELECT id FROM conversations WHERE client_email = ? AND status = 'OTHER' ORDER BY last_message_at DESC LIMIT 1",
            (client_email,),
        ).fetchone()
        return row["id"] if row else None

    # For customer discussions/leads: match the latest open conversation
    row = conn.execute(
        "SELECT id FROM conversations WHERE client_email = ? AND status != 'CLOSED' ORDER BY last_message_at DESC LIMIT 1",
        (client_email,),
    ).fetchone()
    return row["id"] if row else None


def get_or_create_conversation_for_inbound(conn, parsed, ai_result):
    """
    Smart routing for inbound email:
    1. If In-Reply-To / References match an existing thread -> attach to that thread.
    2. If AI classified as NEW_REQUEST -> create a separate NEW booking conversation.
    3. If AI classified as DISCUSSION -> attach to open active conversation if available.
    4. If AI classified as OTHER -> attach to existing OTHER thread for this sender.
    """
    client_email = (ai_result.get("client_email") or parsed.get("from_addr") or "").lower().strip()
    if not client_email:
        client_email = (parsed.get("from_addr") or "unknown@unknown").lower().strip()

    client_name = ai_result.get("client_name") or parsed.get("from_name") or None
    category = ai_result.get("category", "NEW_REQUEST")

    # 1. Direct thread reply matching (exact header reference)
    thread_cid = find_conversation_by_thread_headers(
        conn,
        in_reply_to=parsed.get("in_reply_to"),
        references=parsed.get("references"),
    )
    if thread_cid:
        return thread_cid, False

    # 2. Category-based routing
    if category == "NEW_REQUEST":
        # New reservation / quote inquiry -> always start a separate new booking!
        cid = create_conversation(conn, client_email, client_name=client_name, status="NEW_REQUEST")
        return cid, True

    elif category == "DISCUSSION":
        active_cid = find_active_conversation_by_email(conn, client_email)
        if active_cid:
            return active_cid, False
        cid = create_conversation(conn, client_email, client_name=client_name, status="DISCUSSION")
        return cid, True

    else:  # OTHER
        other_cid = find_active_conversation_by_email(conn, client_email, status="OTHER")
        if other_cid:
            return other_cid, False
        cid = create_conversation(conn, client_email, client_name=client_name, status="OTHER")
        return cid, True


def get_or_create_conversation(conn, client_email, client_name=None, status="NEW_REQUEST"):
    """Legacy helper for seeding/testing."""
    existing = get_conversation_by_email(conn, client_email)
    if existing:
        return existing["id"], False
    cid = create_conversation(conn, client_email, client_name=client_name, status=status)
    return cid, True


def update_conversation_fields(conn, conversation_id, **fields):
    """Update arbitrary whitelisted columns on a conversation."""
    allowed = {
        "client_name", "client_phone", "status",
        "trip_date", "origin", "destination",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE conversations SET {set_clause} WHERE id = ?",
        (*updates.values(), conversation_id),
    )


def maybe_auto_update_status(conn, conversation_id, ai_category):
    """
    Update a conversation's status from an incoming email's AI category,
    but only if the conversation is still in an auto-managed state so we
    never clobber a staff member's manual CONFIRMED/CLOSED decision.
    """
    convo = get_conversation(conn, conversation_id)
    if convo and convo["status"] in AUTO_MANAGED_STATUSES and ai_category:
        conn.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE id = ?",
            (ai_category, _now(), conversation_id),
        )


def touch_conversation_last_message(conn, conversation_id, when_iso):
    conn.execute(
        "UPDATE conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
        (when_iso, _now(), conversation_id),
    )


def list_conversations(conn, status=None, search=None):
    query = "SELECT * FROM conversations"
    clauses, params = [], []
    if status and status != "ALL":
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append(
            "(client_name LIKE ? OR client_email LIKE ? OR origin LIKE ? OR destination LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY last_message_at DESC"
    return conn.execute(query, params).fetchall()


def status_counts(conn):
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM conversations GROUP BY status"
    ).fetchall()
    counts = {s: 0 for s in ALL_STATUSES}
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["ALL"] = sum(counts.values())
    return counts


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

def message_exists(conn, message_id):
    if not message_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def add_message(conn, conversation_id, direction, subject, body_text, body_html,
                 from_addr, to_addr, message_id=None, in_reply_to=None,
                 ai_category=None, ai_confidence=None, received_at=None):
    now = _now()
    received_at = received_at or now
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (conversation_id, direction, message_id, in_reply_to, from_addr, to_addr,
             subject, body_text, body_html, ai_category, ai_confidence, received_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, direction, message_id, in_reply_to, from_addr, to_addr,
         subject, body_text, body_html, ai_category, ai_confidence, received_at, now),
    )
    touch_conversation_last_message(conn, conversation_id, received_at)
    return cur.lastrowid


def list_messages(conn, conversation_id):
    return conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY received_at ASC, id ASC",
        (conversation_id,),
    ).fetchall()


def get_last_inbound_message(conn, conversation_id):
    return conn.execute(
        """
        SELECT * FROM messages
        WHERE conversation_id = ? AND direction = 'inbound'
        ORDER BY received_at DESC LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

def add_note(conn, conversation_id, note_text, author=None):
    now = _now()
    cur = conn.execute(
        "INSERT INTO notes (conversation_id, author, note_text, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, author, note_text, now),
    )
    return cur.lastrowid


def list_notes(conn, conversation_id):
    return conn.execute(
        "SELECT * FROM notes WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()


# --------------------------------------------------------------------------
# Sync state (last processed IMAP UID)
# --------------------------------------------------------------------------

def get_sync_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_sync_state(conn, key, value):
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
