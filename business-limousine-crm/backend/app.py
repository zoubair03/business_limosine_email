"""
Flask application: REST API for the dashboard + serves the static
frontend (frontend/index.html, styles.css, app.js).

Run with:  python app.py
"""
import logging

from flask import Flask, jsonify, request, send_from_directory

import models
from config import Config, PROJECT_ROOT
from database import db_session, init_db
from email_sender import send_reply

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")


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
        "author": row["author"],
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
# Conversations
# --------------------------------------------------------------------------

@app.get("/api/conversations")
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
def api_add_note(conversation_id):
    data = request.get_json(force=True) or {}
    note_text = (data.get("note_text") or "").strip()
    if not note_text:
        return jsonify({"error": "note_text is required"}), 400
    with db_session() as conn:
        convo = models.get_conversation(conn, conversation_id)
        if not convo:
            return jsonify({"error": "not found"}), 404
        models.add_note(conn, conversation_id, note_text, author=data.get("author"))
        notes = models.list_notes(conn, conversation_id)
    return jsonify({"notes": [row_to_note(n) for n in notes]})


# --------------------------------------------------------------------------
# Reply
# --------------------------------------------------------------------------

@app.post("/api/conversations/<int:conversation_id>/reply")
def api_reply(conversation_id):
    data = request.get_json(force=True) or {}
    subject = (data.get("subject") or "").strip()
    body_text = (data.get("body_text") or "").strip()
    if not body_text:
        return jsonify({"error": "body_text is required"}), 400

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
        messages = models.list_messages(conn, conversation_id)

    return jsonify({"messages": [row_to_message(m) for m in messages]})


# --------------------------------------------------------------------------
# Manual sync trigger
# --------------------------------------------------------------------------

@app.post("/api/sync")
def api_sync():
    from email_fetcher import run_once
    try:
        processed = run_once()
    except Exception as exc:
        logger.exception("Manual sync failed")
        return jsonify({"error": str(exc)}), 502
    return jsonify({"processed": processed})


if __name__ == "__main__":
    init_db()
    app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=True)
