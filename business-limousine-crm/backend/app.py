"""
Flask application: REST API for the dashboard + serves the static
frontend (frontend/index.html, styles.css, app.js).

Run with:  python app.py
"""
from datetime import timedelta
import logging

from flask import Flask, jsonify, request, send_from_directory

from auth import (
    get_current_user,
    hash_password,
    login_required,
    login_user,
    logout_user,
    roles_required,
    verify_password,
)
from config import Config, PROJECT_ROOT
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
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "direction": row["direction"],
        "message_id": row["message_id"],
        "from_addr": row["from_addr"],
        "to_addr": row["to_addr"],
        "subject": row["subject"],
        "body_text": row["body_text"],
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
    if not body_text:
        return jsonify({"error": "body_text is required"}), 400

    current_user = get_current_user()
    user_id = current_user["id"] if current_user else None

    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        last_inbound = models.get_last_inbound_message(conn, conversation_id)

    in_reply_to = last_inbound["message_id"] if last_inbound else None
    subject = subject or f"Re: {(last_inbound['subject'] if last_inbound else 'Your inquiry')}"

    try:
        new_message_id = send_reply(
            to_addr=convo["client_email"],
            subject=subject,
            body_text=body_text,
            in_reply_to_message_id=in_reply_to,
        )
    except Exception as exc:
        logger.exception("Failed to send reply")
        return jsonify({"error": f"failed to send email: {exc}"}), 502

    with db_session() as conn:
        models.add_message(
            conn,
            conversation_id=conversation_id,
            direction="outbound",
            subject=subject,
            body_text=body_text,
            body_html=None,
            from_addr=Config.SMTP_USER,
            to_addr=convo["client_email"],
            message_id=new_message_id,
            in_reply_to=in_reply_to,
        )
        models.maybe_auto_update_status(conn, conversation_id, "DISCUSSION")
        messages = models.list_messages(conn, conversation_id)

    return jsonify({"messages": [row_to_message(m) for m in messages]})

@app.route("/settings/whatsapp")
def whatsapp_settings():
    # ajoutez votre décorateur d'auth existant si vous en avez un (ex: @login_required)
    return render_template("whatsapp_alerts.html")


# --------------------------------------------------------------------------
# Manual sync trigger
# --------------------------------------------------------------------------

@app.post("/api/sync")
@login_required
def api_sync():
    from email_fetcher import run_once
    try:
        processed = run_once()
    except Exception as exc:
        logger.exception("Manual sync failed")
        return jsonify({"error": str(exc)}), 502
    return jsonify({"processed": processed})


# --------------------------------------------------------------------------
# Application Startup
# --------------------------------------------------------------------------

def setup_app():
    init_db()
    with db_session() as conn:
        models.ensure_default_admin(conn)


setup_app()

if __name__ == "__main__":
    app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=True)

