"""
Connects to the mailbox over IMAP, pulls any messages that arrived since
the last sync, and pushes each one through the parser + AI classifier +
database layer.

UID-based tracking (rather than the \\Seen flag) is used to decide what's
"new", so this is safe to run repeatedly even if someone reads mail in
another client (webmail, Outlook, etc) in between syncs.
"""
import imaplib
import logging
import threading
import time
import uuid

from ai_classifier import classify_email
from config import Config
from database import db_session
from email_filter import pre_filter
from email_parser import extract_form_fields, parse_raw_email
import models

logger = logging.getLogger("email_fetcher")

SYNC_STATE_KEY = "last_uid"
_sync_lock = threading.Lock()


def connect():
    Config.require_imap()
    conn = imaplib.IMAP4_SSL(Config.IMAP_HOST, Config.IMAP_PORT)
    conn.login(Config.IMAP_USER, Config.IMAP_PASSWORD)
    conn.select(Config.IMAP_FOLDER)
    return conn


def _fetch_new_uids(imap_conn, last_uid, initial_limit=None, deep_limit=None):
    status, data = imap_conn.uid("search", None, "ALL")
    if status != "OK" or not data or not data[0]:
        return []

    raw_uids = data[0].split()
    all_uids = sorted([int(u) for u in raw_uids if u.isdigit()])
    if not all_uids:
        return []

    if deep_limit and deep_limit > 0:
        target_uids = all_uids[-deep_limit:]
        return [str(u) for u in target_uids]

    if last_uid:
        last_int = int(last_uid)
        new_uids = [u for u in all_uids if u > last_int]
        return [str(u) for u in new_uids]
    else:
        limit = initial_limit or Config.INITIAL_SYNC_LIMIT
        target_uids = all_uids[-limit:] if len(all_uids) > limit else all_uids
        logger.info(
            "Initial sync on mailbox (%d total messages found). Processing the latest %d messages.",
            len(all_uids),
            len(target_uids),
        )
        return [str(u) for u in target_uids]


def _is_outbound_sender(from_addr):
    if not from_addr:
        return False
    from_clean = from_addr.lower().strip()
    our_addresses = set()
    if Config.IMAP_USER:
        our_addresses.add(Config.IMAP_USER.lower().strip())
    if Config.SMTP_USER:
        our_addresses.add(Config.SMTP_USER.lower().strip())
    return from_clean in our_addresses


def _store_message(db_conn, parsed, ai_result=None, imap_uid=None):
    is_outbound = _is_outbound_sender(parsed.get("from_addr"))

    if is_outbound:
        direction = "outbound"
        conversation_id, created = models.get_or_create_conversation_for_outbound(
            db_conn, parsed
        )
        models.maybe_auto_update_status(db_conn, conversation_id, "DISCUSSION")
    else:
        direction = "inbound"
        conversation_id, created = models.get_or_create_conversation_for_inbound(
            db_conn, parsed, ai_result
        )
        client_name = ai_result.get("client_name") or parsed.get("from_name") or None

        if created:
            models.update_conversation_fields(
                db_conn,
                conversation_id,
                client_name=client_name,
                client_phone=ai_result.get("client_phone"),
                trip_date=ai_result.get("trip_date"),
                origin=ai_result.get("origin"),
                destination=ai_result.get("destination"),
            )
        else:
            # Fill in any missing details on existing conversation without overwriting existing trip details
            existing = models.get_conversation(db_conn, conversation_id)
            updates = {}
            if existing:
                if not existing["client_phone"] and ai_result.get("client_phone"):
                    updates["client_phone"] = ai_result["client_phone"]
                if not existing["trip_date"] and ai_result.get("trip_date"):
                    updates["trip_date"] = ai_result["trip_date"]
                if not existing["origin"] and ai_result.get("origin"):
                    updates["origin"] = ai_result["origin"]
                if not existing["destination"] and ai_result.get("destination"):
                    updates["destination"] = ai_result["destination"]
                if updates:
                    models.update_conversation_fields(db_conn, conversation_id, **updates)

            models.maybe_auto_update_status(db_conn, conversation_id, ai_result["category"])

    msg_id = parsed.get("message_id")
    if not msg_id:
        msg_id = f"<fallback-{uuid.uuid4().hex[:12]}@crm>"

    models.add_message(
        db_conn,
        conversation_id=conversation_id,
        direction=direction,
        subject=parsed["subject"],
        body_text=parsed["body_text"],
        body_html=parsed["body_html"],
        from_addr=parsed["from_addr"],
        to_addr=parsed["to_addr"],
        cc_addr=parsed.get("cc_addr"),
        attachments=parsed.get("attachments"),
        message_id=msg_id,
        in_reply_to=parsed["in_reply_to"],
        ai_category=ai_result["category"] if ai_result else None,
        ai_confidence=ai_result["confidence"] if ai_result else None,
        received_at=parsed["date_iso"],
        # Where this message sits in the mailbox, so its attachments can be
        # fetched on demand rather than stored locally.
        imap_uid=str(imap_uid) if imap_uid is not None else None,
        imap_folder=Config.IMAP_FOLDER,
    )
    return conversation_id


def run_once(deep_limit=None):
    """Fetch and process all mail received since the last run. Returns count processed."""
    if not _sync_lock.acquire(blocking=False):
        logger.info("A sync cycle is already in progress, skipping concurrent run.")
        return 0

    processed = 0
    try:
        imap_conn = connect()
        try:
            with db_session() as db_conn:
                last_uid = models.get_sync_state(db_conn, SYNC_STATE_KEY)

            uids = _fetch_new_uids(imap_conn, last_uid, deep_limit=deep_limit)
            highest_uid = int(last_uid) if last_uid else 0

            for uid in uids:
                try:
                    status, msg_data = imap_conn.uid("fetch", str(uid), "(RFC822)")
                    if status != "OK" or not msg_data or msg_data[0] is None or not isinstance(msg_data[0], tuple):
                        continue
                    raw_bytes = msg_data[0][1]

                    try:
                        parsed = parse_raw_email(raw_bytes)
                    except Exception as exc:
                        logger.error("Failed to parse message UID %s: %s", uid, exc)
                        continue

                    with db_session() as db_conn:
                        if parsed.get("message_id") and models.message_exists(db_conn, parsed["message_id"]):
                            highest_uid = max(highest_uid, int(uid))
                            continue

                        is_outbound = _is_outbound_sender(parsed.get("from_addr"))
                        was_filtered = False
                        if is_outbound:
                            ai_result = {"category": "DISCUSSION", "confidence": 1.0}
                        else:
                            ai_result = pre_filter(
                                db_conn,
                                from_addr=parsed["from_addr"],
                                subject=parsed["subject"],
                                body_text=parsed["body_text"],
                            )
                            if ai_result is not None:
                                was_filtered = True
                            else:
                                ai_result = classify_email(
                                    subject=parsed["subject"],
                                    body=parsed["body_text"],
                                    from_name=parsed["from_name"],
                                    from_addr=parsed["from_addr"],
                                )
                            # Form-field regex extraction fills any gaps the AI left blank.
                            # Skipped for pre-filtered mail: it was already deemed noise.
                            if not was_filtered:
                                form_fields = extract_form_fields(parsed["body_text"])
                                for key in ("client_name", "client_phone", "trip_date", "origin", "destination"):
                                    form_key = {"client_name": "name", "client_phone": "phone"}.get(key, key)
                                    if not ai_result.get(key) and form_fields.get(form_key):
                                        ai_result[key] = form_fields[form_key]
                                if not ai_result.get("client_email") and form_fields.get("email"):
                                    ai_result["client_email"] = form_fields["email"]

                        _store_message(db_conn, parsed, ai_result, imap_uid=uid)
                        processed += 1

                    highest_uid = max(highest_uid, int(uid))
                    if not is_outbound and not was_filtered:
                        time.sleep(0.3)  # Smooth pacing to respect Gemini API rate limits
                except Exception as loop_err:
                    logger.error("Error processing UID %s: %s", uid, loop_err)
                    continue

            with db_session() as db_conn:
                models.set_sync_state(db_conn, SYNC_STATE_KEY, highest_uid)

        finally:
            try:
                imap_conn.logout()
            except Exception:
                pass

    finally:
        _sync_lock.release()

    logger.info("Sync complete: %d new message(s) processed", processed)
    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from database import init_db
    init_db()
    run_once()