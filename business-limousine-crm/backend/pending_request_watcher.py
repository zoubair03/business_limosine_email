"""
pending_request_watcher.py
Vérifie périodiquement les conversations en statut "New request" (en attente)
depuis plus de X minutes, et déclenche une alerte WhatsApp au dispatcher.

À intégrer dans votre boucle existante (sync_worker.py) ou à lancer comme
tâche planifiée séparée (ex: via APScheduler ou un cron).

ADAPTER : les noms de colonnes/table ci-dessous (`conversations`, `status`,
`created_at`, `whatsapp_alert_sent`) doivent correspondre à votre schéma réel
dans database.py / models.py.
"""

import logging
import sqlite3
from datetime import datetime, timedelta

from whatsapp_notifier import WhatsAppNotifier

logger = logging.getLogger("pending_request_watcher")

PENDING_STATUS = "new_request"   # adapter à la valeur réelle utilisée dans votre DB
ALERT_THRESHOLD_MINUTES = 10
DB_PATH = "crm.db"  # adapter au chemin réel


def check_pending_requests(db_path: str = DB_PATH):
    notifier = WhatsAppNotifier()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    threshold_time = datetime.utcnow() - timedelta(minutes=ALERT_THRESHOLD_MINUTES)

    cursor.execute(
        """
        SELECT id, contact_name, contact_email, created_at
        FROM conversations
        WHERE status = ?
          AND created_at <= ?
          AND (whatsapp_alert_sent IS NULL OR whatsapp_alert_sent = 0)
        """,
        (PENDING_STATUS, threshold_time.isoformat()),
    )
    pending = cursor.fetchall()

    if not pending:
        logger.info("Aucune demande en attente dépassant le seuil.")
        conn.close()
        return

    for row in pending:
        created_at = datetime.fromisoformat(row["created_at"])
        minutes_waiting = int((datetime.utcnow() - created_at).total_seconds() // 60)

        sent = notifier.send_pending_alert(
            contact_name=row["contact_name"] or "Inconnu",
            contact_email=row["contact_email"] or "",
            minutes_waiting=minutes_waiting,
        )

        if sent:
            cursor.execute(
                "UPDATE conversations SET whatsapp_alert_sent = 1 WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            logger.info(f"Conversation {row['id']} marquée comme notifiée.")

    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check_pending_requests()
