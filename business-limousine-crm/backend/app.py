"""
Flask application: REST API for the dashboard + serves the static
frontend (frontend/index.html, styles.css, app.js).

Run with:  python app.py
"""
from datetime import timedelta
import json
import logging
import mimetypes
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from auth import (
    get_current_user,
    hash_password,
    login_required,
    login_user,
    logout_user,
    roles_required,
    verify_password,
)
from config import Config, PROJECT_ROOT, UPLOADS_DIR
from database import db_session, init_db
from email_sender import send_reply
import models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.secret_key = Config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB max upload


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------

def row_to_conversation(row):
    return {
        "id": row["id"],
        "client_name": row["client_name"],
        "client_email": row["client_email"],
        "client_phone": row["client_phone"],
        "status": row["status"],
        "trip_date": row["trip_date"],
        "origin": row["origin"],
        "destination": row["destination"],
        "last_message_at": row["last_message_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_message(row):
    att_raw = row["attachments"] if "attachments" in row.keys() else None
    attachments = []
    if att_raw:
        if isinstance(att_raw, str):
            try:
                attachments = json.loads(att_raw)
            except Exception:
                attachments = []
        elif isinstance(att_raw, list):
            attachments = att_raw

    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "direction": row["direction"],
        "message_id": row["message_id"],
        "from_addr": row["from_addr"],
        "to_addr": row["to_addr"],
        "cc_addr": row["cc_addr"] if "cc_addr" in row.keys() else None,
        "subject": row["subject"],
        "body_text": row["body_text"],
        "body_html": row["body_html"],
        "attachments": attachments,
        "ai_category": row["ai_category"],
        "ai_confidence": row["ai_confidence"],
        "received_at": row["received_at"],
    }


def row_to_note(row):
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "author": row["author"],
        "author_role": row["author_role"] if "author_role" in row.keys() else None,
        "author_avatar": row["author_avatar"] if "author_avatar" in row.keys() else "#C5A059",
        "note_text": row["note_text"],
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/uploads/<path:filename>")
def serve_uploads(filename):
    return send_from_directory(str(UPLOADS_DIR), filename)


@app.post("/api/upload")
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    original_name = secure_filename(file.filename) or f"file_{uuid.uuid4().hex[:6]}"
    unique_prefix = uuid.uuid4().hex[:8]
    disk_filename = f"{unique_prefix}_{original_name}"

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOADS_DIR / disk_filename
    file.save(str(save_path))

    file_size = save_path.stat().st_size
    content_type = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    return jsonify({
        "filename": original_name,
        "file_size": file_size,
        "content_type": content_type,
        "url": f"/uploads/{disk_filename}"
    })


# --------------------------------------------------------------------------
# Authentication & Users API
# --------------------------------------------------------------------------

@app.post("/api/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    with db_session() as conn:
        user = models.get_user_by_email(conn, email)
        if not user or not user["is_active"] or not verify_password(user["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401

        user_dict = {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
        }
        login_user(user_dict)
        return jsonify({"user": user_dict, "message": "Logged in successfully"})


@app.post("/api/auth/logout")
def api_logout():
    logout_user()
    return jsonify({"message": "Logged out successfully"})


@app.get("/api/auth/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"user": None, "authenticated": False}), 401
    return jsonify({"user": user, "authenticated": True})


@app.get("/api/users")
@roles_required("ADMIN", "DISPATCHER")
def api_list_users():
    with db_session() as conn:
        rows = models.list_users(conn)
        return jsonify({"users": [dict(r) for r in rows]})


@app.post("/api/users")
@roles_required("ADMIN")
def api_create_user():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()
    role = (data.get("role") or "DISPATCHER").upper()
    avatar_color = data.get("avatar_color") or "#C5A059"

    if not email or not password or not full_name:
        return jsonify({"error": "Email, password, and full name are required"}), 400
    if role not in models.ALL_ROLES:
        return jsonify({"error": f"Role must be one of {models.ALL_ROLES}"}), 400

    with db_session() as conn:
        if models.get_user_by_email(conn, email):
            return jsonify({"error": "A user with this email already exists"}), 409
        pw_hash = hash_password(password)
        uid = models.create_user(conn, email, pw_hash, full_name, role, avatar_color)
        return jsonify({"id": uid, "email": email, "full_name": full_name, "role": role}), 201


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------

@app.get("/api/conversations")
@login_required
def api_list_conversations():
    status = request.args.get("status")
    search = request.args.get("search")
    with db_session() as conn:
        rows = models.list_conversations(conn, status=status, search=search)
        counts = models.status_counts(conn)
    return jsonify({
        "conversations": [row_to_conversation(r) for r in rows],
        "counts": counts,
    })


@app.get("/api/conversations/<int:conversation_id>")
@login_required
def api_get_conversation(conversation_id):
    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        messages = models.list_messages(conn, conversation_id)
        notes = models.list_notes(conn, conversation_id)
    return jsonify({
        "conversation": row_to_conversation(convo),
        "messages": [row_to_message(m) for m in messages],
        "notes": [row_to_note(n) for n in notes],
    })


@app.patch("/api/conversations/<int:conversation_id>")
@login_required
def api_update_conversation(conversation_id):
    data = request.get_json(force=True) or {}
    if "status" in data and data["status"] not in models.ALL_STATUSES:
        return jsonify({"error": f"invalid status, must be one of {models.ALL_STATUSES}"}), 400
    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        models.update_conversation_fields(conn, conversation_id, **data)
        updated = models.get_conversation(conn, conversation_id)
    return jsonify({"conversation": row_to_conversation(updated)})


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

def row_to_recent_note(row):
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "author": row["author"],
        "author_role": row["author_role"] if "author_role" in row.keys() else None,
        "author_avatar": row["author_avatar"] if "author_avatar" in row.keys() else "#C5A059",
        "note_text": row["note_text"],
        "created_at": row["created_at"],
        "client_name": row["client_name"] if "client_name" in row.keys() else None,
        "client_email": row["client_email"] if "client_email" in row.keys() else None,
        "origin": row["origin"] if "origin" in row.keys() else None,
        "destination": row["destination"] if "destination" in row.keys() else None,
        "trip_date": row["trip_date"] if "trip_date" in row.keys() else None,
        "conversation_status": row["conversation_status"] if "conversation_status" in row.keys() else None,
    }


@app.get("/api/notes/recent")
@login_required
def api_list_recent_notes():
    limit = int(request.args.get("limit", 50))
    with db_session() as conn:
        notes = models.list_recent_notes(conn, limit=limit)
    return jsonify({"notes": [row_to_recent_note(n) for n in notes]})


@app.post("/api/conversations/<int:conversation_id>/notes")
@login_required
def api_add_note(conversation_id):
    data = request.get_json(force=True) or {}
    note_text = (data.get("note_text") or "").strip()
    if not note_text:
        return jsonify({"error": "note_text is required"}), 400

    current_user = get_current_user()
    user_id = current_user["id"] if current_user else None
    author_name = current_user["full_name"] if current_user else (data.get("author") or "Staff")

    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        models.add_note(conn, conversation_id, note_text, user_id=user_id, author=author_name)
        notes = models.list_notes(conn, conversation_id)
    return jsonify({"notes": [row_to_note(n) for n in notes]})



# --------------------------------------------------------------------------
# Reply
# --------------------------------------------------------------------------

@app.post("/api/conversations/<int:conversation_id>/reply")
@login_required
def api_reply(conversation_id):
    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "").strip()
    body_text = (data.get("body_text") or "").strip()
    body_html = data.get("body_html")
    to_addr = (data.get("to_addr") or "").strip()
    cc_addr = data.get("cc_addr")
    attachments = data.get("attachments") or []

    if not body_text and not body_html and not attachments:
        return jsonify({"error": "Message body or attachment is required"}), 400

    current_user = get_current_user()
    user_id = current_user["id"] if current_user else None

    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        last_inbound = models.get_last_inbound_message(conn, conversation_id)

    recipient = to_addr or convo["client_email"]
    in_reply_to = last_inbound["message_id"] if last_inbound else None
    subject = subject or f"Re: {(last_inbound['subject'] if last_inbound else 'Your inquiry')}"

    try:
        new_message_id = send_reply(
            to_addr=recipient,
            subject=subject,
            body_text=body_text,
            cc_addr=cc_addr,
            body_html=body_html,
            attachments=attachments,
            in_reply_to_message_id=in_reply_to,
        )
    except Exception as exc:
        logger.exception("Failed to send reply")
        return jsonify({"error": f"Failed to send email: {exc}"}), 502

    with db_session() as conn:
        msg_id = models.add_message(
            conn,
            conversation_id=conversation_id,
            direction="outbound",
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            from_addr=Config.SMTP_USER,
            to_addr=recipient,
            cc_addr=cc_addr if isinstance(cc_addr, str) else (", ".join(cc_addr) if cc_addr else None),
            attachments=attachments,
            message_id=new_message_id,
            in_reply_to=in_reply_to,
        )
        models.maybe_auto_update_status(conn, conversation_id, "DISCUSSION")
        if user_id:
            try:
                conn.execute("UPDATE messages SET user_id = ? WHERE id = ?", (user_id, msg_id))
            except Exception:
                pass
        messages = models.list_messages(conn, conversation_id)

    return jsonify({"messages": [row_to_message(m) for m in messages]})

# --------------------------------------------------------------------------
# WhatsApp Dispatch Alert Settings API
# --------------------------------------------------------------------------

@app.get("/api/settings/whatsapp")
@login_required
def api_get_whatsapp_settings():
    from whatsapp_notifier import WhatsAppNotifier
    notifier = WhatsAppNotifier()
    twilio_status = notifier.get_status()

    with db_session() as conn:
        enabled_setting = models.get_setting(conn, "whatsapp_alerts_enabled")
        if enabled_setting is not None:
            enabled = str(enabled_setting).lower() in ("1", "true", "yes", "on")
        else:
            enabled = Config.WHATSAPP_ALERTS_ENABLED

        threshold_setting = models.get_setting(conn, "whatsapp_alert_threshold_minutes")
        try:
            threshold_minutes = int(threshold_setting) if threshold_setting else Config.WHATSAPP_ALERT_THRESHOLD_MINUTES
        except (ValueError, TypeError):
            threshold_minutes = Config.WHATSAPP_ALERT_THRESHOLD_MINUTES

        numbers_setting = models.get_setting(conn, "dispatcher_whatsapp_numbers")
        if numbers_setting:
            try:
                loaded = json.loads(numbers_setting)
                # Clean up any items that were saved as Python repr strings e.g. "{'type': 'telegram', ...}"
                dispatcher_numbers = []
                for item in loaded:
                    if isinstance(item, dict):
                        dispatcher_numbers.append(item)
                    elif isinstance(item, str):
                        s = item.strip()
                        # Try to re-parse if it looks like a Python repr dict
                        if s.startswith("{") and "'" in s:
                            try:
                                import ast
                                parsed = ast.literal_eval(s)
                                if isinstance(parsed, dict):
                                    dispatcher_numbers.append(parsed)
                                    continue
                            except Exception:
                                pass
                        dispatcher_numbers.append(s)
            except Exception:
                dispatcher_numbers = [n.strip() for n in numbers_setting.split(",") if n.strip()]
        else:
            raw_numbers = Config.DISPATCHER_WHATSAPP_NUMBERS or ""
            dispatcher_numbers = [n.strip() for n in raw_numbers.split(",") if n.strip()]

    status_info = notifier.get_status()
    return jsonify({
        "enabled": enabled,
        "threshold_minutes": threshold_minutes,
        "dispatcher_numbers": dispatcher_numbers,
        "gateway": status_info,
        "provider": status_info.get("provider", "telegram"),
        "telegram": status_info.get("telegram"),
        "callmebot": status_info.get("callmebot"),
    })


@app.post("/api/settings/whatsapp")
@roles_required("ADMIN", "DISPATCHER")
def api_save_whatsapp_settings():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    try:
        threshold_minutes = max(1, min(1440, int(data.get("threshold_minutes", 10))))
    except (ValueError, TypeError):
        threshold_minutes = 10

    raw_numbers = data.get("dispatcher_numbers", [])
    if isinstance(raw_numbers, str):
        numbers = [n.strip() for n in raw_numbers.split(",") if n.strip()]
    elif isinstance(raw_numbers, list):
        # Preserve dict objects (Telegram entries) as-is; only stringify plain strings
        numbers = []
        for n in raw_numbers:
            if isinstance(n, dict):
                numbers.append(n)          # keep {"type":"telegram","chat_id":...}
            elif isinstance(n, str) and n.strip():
                numbers.append(n.strip())  # plain phone string
    else:
        numbers = []

    with db_session() as conn:
        models.set_setting(conn, "whatsapp_alerts_enabled", "true" if enabled else "false")
        models.set_setting(conn, "whatsapp_alert_threshold_minutes", str(threshold_minutes))
        models.set_setting(conn, "dispatcher_whatsapp_numbers", json.dumps(numbers, ensure_ascii=False))

    return jsonify({
        "success": True,
        "message": "Paramètres d'alertes WhatsApp enregistrés avec succès.",
        "settings": {
            "enabled": enabled,
            "threshold_minutes": threshold_minutes,
            "dispatcher_numbers": numbers,
        }
    })


@app.post("/api/settings/whatsapp/test")
@roles_required("ADMIN", "DISPATCHER")
def api_test_whatsapp_alert():
    data = request.get_json(silent=True) or {}
    phone_number = (data.get("phone_number") or "").strip()

    with db_session() as conn:
        if not phone_number:
            numbers_setting = models.get_setting(conn, "dispatcher_whatsapp_numbers")
            if numbers_setting:
                try:
                    numbers = json.loads(numbers_setting)
                    if numbers:
                        phone_number = numbers[0]
                except Exception:
                    pass
        if not phone_number and Config.DISPATCHER_WHATSAPP_NUMBERS:
            phone_number = Config.DISPATCHER_WHATSAPP_NUMBERS.split(",")[0].strip()

    if not phone_number:
        return jsonify({"success": False, "error": "Aucun numéro de téléphone destinataire fourni."}), 400

    apikey = (data.get("apikey") or "").strip()
    from whatsapp_notifier import WhatsAppNotifier
    notifier = WhatsAppNotifier()
    result = notifier.send_test_alert(phone_number, apikey=apikey)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


# --------------------------------------------------------------------------
# Sender Filtering (Blocked & Allowed Senders)
# --------------------------------------------------------------------------

@app.get("/api/senders/blocked")
@login_required
def api_list_blocked_senders():
    with db_session() as conn:
        rows = models.list_blocked_senders(conn)
        return jsonify({"blocked": [dict(r) for r in rows]})


@app.post("/api/senders/block")
@login_required
def api_block_sender():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pattern = (data.get("pattern") or email).strip().lower()
    reason = (data.get("reason") or "Bloqué depuis le manifest").strip()
    convo_id = data.get("conversation_id")

    if not pattern:
        return jsonify({"error": "Pattern or email is required"}), 400

    with db_session() as conn:
        models.add_blocked_sender(conn, pattern, reason=reason)
        # Reclassify affected conversations to OTHER so they leave ALL conversations immediately
        if email:
            conn.execute(
                "UPDATE conversations SET status = 'OTHER', updated_at = ? WHERE lower(client_email) = ? AND status != 'OTHER'",
                (models._now(), email),
            )
        elif convo_id:
            conn.execute(
                "UPDATE conversations SET status = 'OTHER', updated_at = ? WHERE id = ? AND status != 'OTHER'",
                (models._now(), convo_id),
            )
        counts = models.status_counts(conn)

    return jsonify({
        "success": True,
        "pattern": pattern,
        "counts": counts,
        "message": f"Expéditeur '{pattern}' bloqué avec succès.",
    })


@app.delete("/api/senders/blocked/<int:blocked_id>")
@login_required
def api_delete_blocked_sender(blocked_id):
    with db_session() as conn:
        models.delete_blocked_sender(conn, blocked_id)
        counts = models.status_counts(conn)
    return jsonify({"success": True, "counts": counts})


# --------------------------------------------------------------------------
# Manual sync trigger
# --------------------------------------------------------------------------

@app.post("/api/sync")
@login_required
def api_sync():
    from email_fetcher import run_once
    from pending_request_watcher import check_pending_requests
    try:
        processed = run_once()
        # Immediately check and fire any due Telegram alerts
        try:
            check_pending_requests()
        except Exception as alert_err:
            logger.warning("Post-sync alert check warning: %s", alert_err)
    except Exception as exc:
        logger.exception("Manual sync failed")
        return jsonify({"error": str(exc)}), 502
    return jsonify({"processed": processed})


# --------------------------------------------------------------------------
# Background Alert Watcher Thread (Embedded in Flask app)
# --------------------------------------------------------------------------

_watcher_lock = threading.Lock()
_watcher_running = False

def _start_background_watcher():
    global _watcher_running
    with _watcher_lock:
        if _watcher_running:
            return
        _watcher_running = True

    def _watcher_loop():
        time.sleep(3)  # brief startup delay
        logger.info("Embedded Dispatch Alert Watcher loop is active (checking every 15s).")
        while True:
            try:
                from pending_request_watcher import check_pending_requests
                sent = check_pending_requests()
                if sent > 0:
                    logger.info("Embedded watcher sent %s Telegram dispatch alert(s).", sent)
            except Exception as e:
                logger.debug("Embedded watcher loop tick: %s", e)
            time.sleep(15)

    thread = threading.Thread(target=_watcher_loop, daemon=True, name="DispatchAlertWatcher")
    thread.start()


# --------------------------------------------------------------------------
# Application Startup
# --------------------------------------------------------------------------

def setup_app():
    init_db()
    with db_session() as conn:
        models.ensure_default_admin(conn)
    
    # In Flask development mode, only start the watcher in the actual worker process
    is_main_worker = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug
    if is_main_worker and not _watcher_running:
        _start_background_watcher()


setup_app()

if __name__ == "__main__":
    app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=True)

