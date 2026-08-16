"""
pending_request_watcher.py
Continuously checks for unreplied 'NEW_REQUEST' reservations exceeding the alert threshold,
and triggers WhatsApp notifications to dispatchers.
"""

from datetime import datetime, timezone
import json
import logging
from typing import List, Optional

from config import Config
from database import db_session
import models
from whatsapp_notifier import WhatsAppNotifier

logger = logging.getLogger("pending_request_watcher")


def check_pending_requests() -> int:
    """
    Checks all pending requests exceeding the configured threshold and sends Telegram / WhatsApp alerts.
    Returns the number of conversations alerted.
    """
    with db_session() as conn:
        # 1. Check if alerts are enabled
        enabled_setting = models.get_setting(conn, "whatsapp_alerts_enabled")
        if enabled_setting is not None:
            alerts_enabled = str(enabled_setting).lower() in ("1", "true", "yes", "on")
        else:
            alerts_enabled = Config.WHATSAPP_ALERTS_ENABLED

        if not alerts_enabled:
            logger.debug("Dispatch alerts are currently disabled in settings.")
            return 0

        # 2. Get alert threshold (minutes)
        threshold_setting = models.get_setting(conn, "whatsapp_alert_threshold_minutes")
        try:
            threshold_minutes = int(threshold_setting) if threshold_setting else Config.WHATSAPP_ALERT_THRESHOLD_MINUTES
        except (ValueError, TypeError):
            threshold_minutes = Config.WHATSAPP_ALERT_THRESHOLD_MINUTES

        # 3. Get dispatcher numbers / IDs
        numbers_setting = models.get_setting(conn, "dispatcher_whatsapp_numbers")
        dispatcher_numbers = []
        if numbers_setting:
            try:
                loaded = json.loads(numbers_setting)
                if isinstance(loaded, list):
                    for item in loaded:
                        if isinstance(item, dict):
                            dispatcher_numbers.append(item)
                        elif isinstance(item, str) and item.strip():
                            s = item.strip()
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

        if not dispatcher_numbers and Config.DISPATCHER_WHATSAPP_NUMBERS:
            dispatcher_numbers = [n.strip() for n in Config.DISPATCHER_WHATSAPP_NUMBERS.split(",") if n.strip()]

        notifier = WhatsAppNotifier(dispatcher_numbers=dispatcher_numbers)
        if not notifier.is_configured:
            logger.warning("No Telegram bot or WhatsApp gateway is configured. Skipping alerts.")
            return 0

        # 4. Atomically claim all eligible pending reservations
        pending_rows = models.claim_pending_unalerted_requests(conn, threshold_minutes=threshold_minutes)
        if not pending_rows:
            return 0

        logger.info(f"Found and claimed {len(pending_rows)} unalerted pending request(s) waiting > {threshold_minutes} min.")
        alerted_count = 0

        for row in pending_rows:
            convo_id = row["id"]
            created_at_str = row["created_at"]
            
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                now_utc = datetime.now(timezone.utc)
                minutes_waiting = max(1, int((now_utc - created_dt).total_seconds() // 60))
            except Exception:
                minutes_waiting = threshold_minutes

            try:
                alert_result = notifier.send_pending_alert(
                    client_name=row["client_name"] or "Inconnu",
                    client_email=row["client_email"] or "",
                    client_phone=row["client_phone"],
                    origin=row["origin"],
                    destination=row["destination"],
                    trip_date=row["trip_date"],
                    minutes_waiting=minutes_waiting,
                    conversation_id=convo_id,
                )

                if alert_result.get("sent"):
                    alerted_count += 1
                    logger.info(f"Sent Telegram alert for reservation #{convo_id} ({row['client_name']}).")
                else:
                    logger.warning(f"Failed to send alert for #{convo_id}: {alert_result}")
            except Exception as ex:
                logger.exception(f"Error sending dispatch alert for conversation #{convo_id}: {ex}")

        return alerted_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sent = check_pending_requests()
    print(f"Pending requests checked. Alerted: {sent}")

