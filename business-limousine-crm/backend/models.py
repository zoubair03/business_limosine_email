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


def get_or_create_conversation_for_outbound(conn, parsed):
    """
    Routes an outbound email (sent by staff/dispatch from Gmail/phone/etc.):
    1. If In-Reply-To / References match an existing thread -> attach to that thread.
    2. Otherwise, look for an active conversation with the recipient (to_addr).
    3. If none exists, create a new DISCUSSION conversation for to_addr.
    """
    to_addr = (parsed.get("to_addr") or "").lower().strip()
    if not to_addr:
        to_addr = "unknown@unknown"

    # 1. Match by thread headers
    thread_cid = find_conversation_by_thread_headers(
        conn,
        in_reply_to=parsed.get("in_reply_to"),
        references=parsed.get("references"),
    )
    if thread_cid:
        return thread_cid, False

    # 2. Match active conversation by recipient email
    active_cid = find_active_conversation_by_email(conn, to_addr)
    if active_cid:
        return active_cid, False

    # 3. Create discussion conversation for recipient
    cid = create_conversation(conn, to_addr, client_name=None, status="DISCUSSION")
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
# Users
# --------------------------------------------------------------------------

ALL_ROLES = ["ADMIN", "DISPATCHER", "DRIVER", "ACCOUNTANT"]


def create_user(conn, email, password_hash, full_name, role="DISPATCHER", avatar_color="#C5A059", is_active=1):
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO users (email, password_hash, full_name, role, avatar_color, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (email.lower().strip(), password_hash, full_name.strip(), role, avatar_color, int(bool(is_active)), now, now),
    )
    return cur.lastrowid


def get_user_by_email(conn, email):
    if not email:
        return None
    return conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)).fetchone()


def get_user_by_id(conn, user_id):
    if not user_id:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn):
    return conn.execute(
        "SELECT id, email, full_name, role, avatar_color, is_active, created_at FROM users ORDER BY id ASC"
    ).fetchall()


def update_user(conn, user_id, **fields):
    allowed = {"full_name", "role", "avatar_color", "is_active", "password_hash"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*updates.values(), user_id))


def ensure_default_admin(conn):
    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if count == 0:
        from auth import hash_password
        create_user(
            conn,
            email="admin@businesslimousine.com",
            password_hash=hash_password("admin123"),
            full_name="System Administrator",
            role="ADMIN",
            avatar_color="#C5A059",
        )
        create_user(
            conn,
            email="dispatcher@businesslimousine.com",
            password_hash=hash_password("dispatch123"),
            full_name="Lead Dispatcher",
            role="DISPATCHER",
            avatar_color="#3B82F6",
        )


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

def add_note(conn, conversation_id, note_text, user_id=None, author=None):
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO notes (conversation_id, user_id, author, note_text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, user_id, author, note_text, now),
    )
    return cur.lastrowid


def list_notes(conn, conversation_id):
    return conn.execute(
        """
        SELECT n.id, n.conversation_id, n.user_id, n.note_text, n.created_at,
               COALESCE(u.full_name, n.author, 'Staff') AS author,
               u.role AS author_role,
               u.avatar_color AS author_avatar
        FROM notes n
        LEFT JOIN users u ON n.user_id = u.id
        WHERE n.conversation_id = ?
        ORDER BY n.created_at ASC, n.id ASC
        """,
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


# --------------------------------------------------------------------------
# Sender filtering (blocked_senders / allowed_senders)
#
# Used by email_filter.py to avoid spending Gemini calls on obvious noise
# (system notifications, newsletters...) and to guarantee known corporate
# accounts are never filtered out.
# --------------------------------------------------------------------------

def _pattern_type_for(pattern):
    return "email" if "@" in pattern else "domain"


def add_blocked_sender(conn, pattern, reason=None):
    """pattern is either a bare domain ('waynium.net') or a full email
    ('backup@waynium.net'). Re-adding an existing pattern just updates the reason."""
    pattern = pattern.strip().lower()
    cur = conn.execute(
        """
        INSERT INTO blocked_senders (pattern, pattern_type, reason, hit_count, created_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(pattern) DO UPDATE SET reason = excluded.reason
        """,
        (pattern, _pattern_type_for(pattern), reason, _now()),
    )
    return cur.lastrowid


def delete_blocked_sender(conn, blocked_id):
    conn.execute("DELETE FROM blocked_senders WHERE id = ?", (blocked_id,))


def list_blocked_senders(conn):
    return conn.execute(
        "SELECT * FROM blocked_senders ORDER BY hit_count DESC, created_at DESC"
    ).fetchall()


def get_matching_blocked_sender(conn, from_addr, domain):
    """Exact match only (by design — no wildcard/substring matching here,
    that's what the heuristics layer in email_filter.py is for)."""
    return conn.execute(
        "SELECT * FROM blocked_senders WHERE pattern = ? OR pattern = ?",
        (from_addr, domain),
    ).fetchone()


def record_blocked_sender_hit(conn, blocked_id):
    conn.execute(
        "UPDATE blocked_senders SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
        (_now(), blocked_id),
    )


def add_allowed_sender(conn, pattern, note=None):
    pattern = pattern.strip().lower()
    cur = conn.execute(
        """
        INSERT INTO allowed_senders (pattern, pattern_type, note, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(pattern) DO UPDATE SET note = excluded.note
        """,
        (pattern, _pattern_type_for(pattern), note, _now()),
    )
    return cur.lastrowid


def delete_allowed_sender(conn, allowed_id):
    conn.execute("DELETE FROM allowed_senders WHERE id = ?", (allowed_id,))


def list_allowed_senders(conn):
    return conn.execute(
        "SELECT * FROM allowed_senders ORDER BY created_at DESC"
    ).fetchall()


def is_sender_allowed(conn, from_addr, domain):
    row = conn.execute(
        "SELECT 1 FROM allowed_senders WHERE pattern = ? OR pattern = ?",
        (from_addr, domain),
    ).fetchone()
    return row is not None