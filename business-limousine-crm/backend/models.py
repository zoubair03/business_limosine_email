"""
Data access layer sitting on top of database.py.

Every function opens its own short-lived connection via db_session(),
so this module is safe to call both from the Flask request handlers
and from the background sync worker.
"""
from datetime import datetime, timezone
import json

from database import db_session

# Statuses that the AI classifier / auto-sync are allowed to set on their
# own. Anything a staff member sets manually (e.g. CONFIRMED, CLOSED) is
# never overwritten by an incoming email.
AUTO_MANAGED_STATUSES = {"NEW_REQUEST", "DISCUSSION", "DOCCLE", "EBOX", "OTHER"}

ALL_STATUSES = ["NEW_REQUEST", "DISCUSSION", "CONFIRMED", "CLOSED", "DOCCLE", "EBOX", "OTHER"]


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
    if status in ("OTHER", "DOCCLE", "EBOX"):
        row = conn.execute(
            "SELECT id FROM conversations WHERE client_email = ? AND status = ? ORDER BY last_message_at DESC LIMIT 1",
            (client_email, status),
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
    4. If classified as DOCCLE -> attach to existing DOCCLE thread for this sender.
    5. If classified as EBOX -> attach to existing EBOX thread for this sender.
    6. If classified as OTHER -> attach to existing OTHER thread for this sender.
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

    elif category == "DOCCLE":
        doccle_cid = find_active_conversation_by_email(conn, client_email, status="DOCCLE")
        if doccle_cid:
            return doccle_cid, False
        cid = create_conversation(conn, client_email, client_name=client_name or "Doccle", status="DOCCLE")
        return cid, True

    elif category == "EBOX":
        ebox_cid = find_active_conversation_by_email(conn, client_email, status="EBOX")
        if ebox_cid:
            return ebox_cid, False
        cid = create_conversation(conn, client_email, client_name=client_name or "eBox", status="EBOX")
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


def _conversation_filters(status=None, search=None, starred=None, archived=False):
    """Shared WHERE builder so the list query and its total count can never drift."""
    clauses, params = [], []

    if archived is True:
        clauses.append("c.is_archived = 1")
    elif archived is False:
        clauses.append("c.is_archived = 0")
    # archived=None means "both", used by search

    if status == "IMPORTANT":
        clauses.append("c.status IN ('DOCCLE', 'EBOX', 'IMPORTANT')")
    elif status == "UNREAD":
        clauses.append("c.is_read = 0 AND c.status NOT IN ('OTHER', 'DOCCLE', 'EBOX')")
    elif status == "STARRED":
        clauses.append("c.is_starred = 1")
    elif status and status != "ALL":
        clauses.append("c.status = ?")
        params.append(status)
    else:
        # 'All conversations' shows active transport bookings, not utility mail.
        clauses.append("c.status NOT IN ('OTHER', 'DOCCLE', 'EBOX')")

    if starred is True:
        clauses.append("c.is_starred = 1")

    if search:
        # A mail search that cannot find a word in the mail is not a search. The
        # EXISTS keeps one row per conversation however many messages match.
        clauses.append("""(
            c.client_name LIKE ? OR c.client_email LIKE ?
            OR c.origin LIKE ? OR c.destination LIKE ?
            OR EXISTS (SELECT 1 FROM messages m2
                       WHERE m2.conversation_id = c.id
                         AND (m2.subject LIKE ? OR m2.body_text LIKE ?))
        )""")
        like = f"%{search}%"
        params.extend([like] * 6)

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# Correlated subqueries against the last message. Cheaper than joining the whole
# messages table and then de-duplicating, and it keeps one row per conversation.
_LIST_SELECT = """
SELECT c.*,
       (SELECT m.subject FROM messages m
         WHERE m.conversation_id = c.id
         ORDER BY m.received_at DESC, m.id DESC LIMIT 1)          AS last_subject,
       (SELECT m.body_text FROM messages m
         WHERE m.conversation_id = c.id
         ORDER BY m.received_at DESC, m.id DESC LIMIT 1)          AS last_body,
       (SELECT m.direction FROM messages m
         WHERE m.conversation_id = c.id
         ORDER BY m.received_at DESC, m.id DESC LIMIT 1)          AS last_direction,
       (SELECT COUNT(*) FROM messages m
         WHERE m.conversation_id = c.id)                          AS message_count,
       (SELECT COUNT(*) FROM messages m
         WHERE m.conversation_id = c.id
           AND m.attachments IS NOT NULL AND m.attachments != ''
           AND m.attachments != '[]')                             AS attachment_count,
       (SELECT COUNT(*) FROM notes n
         WHERE n.conversation_id = c.id)                          AS note_count
  FROM conversations c
"""


def list_conversations(conn, status=None, search=None, limit=None, offset=0,
                       starred=None, archived=False):
    """One page of the manifest, newest first.

    `limit=None` returns everything, which is what the callers that need the whole
    book (exports, the alert watcher) want; the UI always passes a limit.
    """
    where, params = _conversation_filters(status, search, starred, archived)
    # COALESCE so a conversation with no last_message_at still sorts sensibly
    # instead of drifting to the bottom on some SQLite builds and the top on others.
    query = _LIST_SELECT + where + " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC, c.id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params = params + [int(limit), int(offset)]
    return conn.execute(query, params).fetchall()


def count_conversations(conn, status=None, search=None, starred=None, archived=False):
    where, params = _conversation_filters(status, search, starred, archived)
    row = conn.execute("SELECT COUNT(*) AS n FROM conversations c" + where, params).fetchone()
    return row["n"] if row else 0


def set_conversation_flags(conn, conversation_id, **flags):
    """Sets is_read / is_starred / is_archived. Ignores anything else."""
    allowed = {"is_read", "is_starred", "is_archived"}
    sets, params = [], []
    for key, value in flags.items():
        if key in allowed and value is not None:
            sets.append(f"{key} = ?")
            params.append(1 if value else 0)
    if not sets:
        return False
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(conversation_id)
    conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params)
    return True


def mark_all_read(conn, status=None):
    """Marks every conversation in the current view read. Returns rows affected."""
    where, params = _conversation_filters(status, None, None, False)
    # _conversation_filters aliases the table as c; a bare UPDATE cannot use that,
    # so scope by id through a subquery instead.
    cur = conn.execute(
        f"UPDATE conversations SET is_read = 1 WHERE is_read = 0 AND id IN "
        f"(SELECT c.id FROM conversations c{where})", params
    )
    return cur.rowcount


def status_counts(conn):
    """Totals per status, plus the unread counts the sidebar badges show.

    Archived conversations are excluded throughout — an archived thread should not
    keep a badge lit.
    """
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM conversations WHERE is_archived = 0 GROUP BY status"
    ).fetchall()
    counts = {s: 0 for s in ALL_STATUSES}
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["IMPORTANT"] = counts.get("DOCCLE", 0) + counts.get("EBOX", 0)
    counts["ALL"] = sum(count for st, count in counts.items()
                        if st not in ("OTHER", "DOCCLE", "EBOX", "IMPORTANT"))

    unread_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM conversations "
        "WHERE is_read = 0 AND is_archived = 0 GROUP BY status"
    ).fetchall()
    unread = {s: 0 for s in ALL_STATUSES}
    for row in unread_rows:
        unread[row["status"]] = row["n"]
    unread["IMPORTANT"] = unread.get("DOCCLE", 0) + unread.get("EBOX", 0)
    unread["ALL"] = sum(n for st, n in unread.items()
                        if st not in ("OTHER", "DOCCLE", "EBOX", "IMPORTANT"))

    starred = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE is_starred = 1 AND is_archived = 0"
    ).fetchone()["n"]
    archived = conn.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE is_archived = 1"
    ).fetchone()["n"]

    counts["STARRED"] = starred
    counts["ARCHIVED"] = archived
    counts["UNREAD"] = unread["ALL"]
    return {"counts": counts, "unread": unread}


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
                 from_addr, to_addr, cc_addr=None, attachments=None, message_id=None, in_reply_to=None,
                 ai_category=None, ai_confidence=None, received_at=None,
                 imap_uid=None, imap_folder=None):
    now = _now()
    received_at = received_at or now
    if isinstance(attachments, (list, dict)):
        attachments_str = json.dumps(attachments)
    else:
        attachments_str = attachments

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (conversation_id, direction, message_id, in_reply_to, from_addr, to_addr, cc_addr,
             subject, body_text, body_html, attachments, ai_category, ai_confidence, received_at, created_at,
             imap_uid, imap_folder)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, direction, message_id, in_reply_to, from_addr, to_addr, cc_addr,
         subject, body_text, body_html, attachments_str, ai_category, ai_confidence, received_at, now,
         imap_uid, imap_folder),
    )
    # INSERT OR IGNORE: a duplicate message_id is a re-sync of mail already seen,
    # and must not resurrect a thread as unread or bump it up the manifest.
    if cur.rowcount:
        touch_conversation_last_message(conn, conversation_id, received_at)
        if direction == "inbound":
            # New mail from the client reopens the thread as unread — including a
            # thread someone had already read, which is what makes a reply visible.
            conn.execute(
                "UPDATE conversations SET is_read = 0, is_archived = 0 WHERE id = ?",
                (conversation_id,),
            )
        else:
            # We just sent something, so we have obviously seen the thread.
            conn.execute(
                "UPDATE conversations SET is_read = 1 WHERE id = ?", (conversation_id,)
            )
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


def list_recent_notes(conn, limit=50):
    """
    Returns recent internal notes across all conversations with client name, route, and status.
    """
    return conn.execute(
        """
        SELECT n.id, n.conversation_id, n.user_id, n.note_text, n.created_at,
               COALESCE(u.full_name, n.author, 'Staff') AS author,
               u.role AS author_role,
               u.avatar_color AS author_avatar,
               c.client_name,
               c.client_email,
               c.origin,
               c.destination,
               c.trip_date,
               c.status AS conversation_status
        FROM notes n
        LEFT JOIN users u ON n.user_id = u.id
        LEFT JOIN conversations c ON n.conversation_id = c.id
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT ?
        """,
        (limit,),
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
# --------------------------------------------------------------------------
# Application Settings & Configuration
# --------------------------------------------------------------------------

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    now = _now()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, str(value), now),
    )


def get_all_settings(conn):
    rows = conn.execute("SELECT key, value, updated_at FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# --------------------------------------------------------------------------
# WhatsApp / Telegram Pending Alert Queries
# --------------------------------------------------------------------------

def claim_pending_unalerted_requests(conn, threshold_minutes=10):
    """
    Atomically selects and immediately marks pending unalerted requests as claimed (whatsapp_alert_sent = 1).
    This ensures that even if multiple processes (sync_worker, app.py background watcher, manual sync)
    poll the database at the exact same moment, only ONE process can ever claim and send the alert.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    rows = conn.execute(
        """
        SELECT id, client_name, client_email, client_phone, origin, destination, trip_date, created_at
        FROM conversations
        WHERE status = 'NEW_REQUEST'
          AND created_at <= ?
          AND (whatsapp_alert_sent IS NULL OR whatsapp_alert_sent = 0)
        ORDER BY created_at ASC
        """,
        (cutoff,),
    ).fetchall()
    
    if not rows:
        return []
    
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE conversations SET whatsapp_alert_sent = 1, updated_at = ? WHERE id IN ({placeholders})",
        [_now()] + ids,
    )
    return rows


def get_pending_unalerted_requests(conn, threshold_minutes=10):
    """
    Finds all conversations in status 'NEW_REQUEST' created more than threshold_minutes ago
    which have not yet received a WhatsApp alert and have not had outbound replies.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    return conn.execute(
        """
        SELECT id, client_name, client_email, client_phone, origin, destination, trip_date, created_at
        FROM conversations
        WHERE status = 'NEW_REQUEST'
          AND created_at <= ?
          AND (whatsapp_alert_sent IS NULL OR whatsapp_alert_sent = 0)
        ORDER BY created_at ASC
        """,
        (cutoff,),
    ).fetchall()


def mark_conversation_alerted(conn, conversation_id):
    conn.execute(
        "UPDATE conversations SET whatsapp_alert_sent = 1, updated_at = ? WHERE id = ?",
        (_now(), conversation_id),
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

