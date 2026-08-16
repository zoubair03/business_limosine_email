"""
whatsapp_notifier.py
Envoie des alertes WhatsApp aux dispatchers via l'API Twilio.

Nécessite dans le .env :
    TWILIO_ACCOUNT_SID=xxxxxxxx
    TWILIO_AUTH_TOKEN=xxxxxxxx
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # numéro sandbox ou numéro business Twilio
    DISPATCHER_WHATSAPP_NUMBERS=+32470000000,+32471111111   # séparés par virgule

Installation :
    pip install twilio
"""

import logging
import os

from twilio.rest import Client

logger = logging.getLogger("whatsapp_notifier")


class WhatsAppNotifier:
    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = os.getenv("TWILIO_WHATSAPP_FROM")
        raw_numbers = os.getenv("DISPATCHER_WHATSAPP_NUMBERS", "")
        self.dispatcher_numbers = [n.strip() for n in raw_numbers.split(",") if n.strip()]

        if not all([self.account_sid, self.auth_token, self.from_number]):
            logger.warning("Configuration Twilio incomplète : les alertes WhatsApp sont désactivées.")
            self.client = None
        else:
            self.client = Client(self.account_sid, self.auth_token)

    def send_pending_alert(self, contact_name: str, contact_email: str, minutes_waiting: int, conversation_url: str = None) -> bool:
        """
        Envoie une alerte WhatsApp à tous les dispatchers configurés.
        Retourne True si au moins un message a été envoyé avec succès.
        """
        if not self.client:
            logger.error("Client Twilio non initialisé, alerte non envoyée.")
            return False

        if not self.dispatcher_numbers:
            logger.warning("Aucun numéro dispatcher configuré (DISPATCHER_WHATSAPP_NUMBERS).")
            return False

        message_body = (
            f"⏰ *Demande en attente*\n"
            f"Client : {contact_name}\n"
            f"Email : {contact_email}\n"
            f"En attente depuis {minutes_waiting} minutes sans réponse."
        )
        if conversation_url:
            message_body += f"\n\nVoir : {conversation_url}"

        success = False
        for number in self.dispatcher_numbers:
            try:
                self.client.messages.create(
                    from_=self.from_number,
                    to=f"whatsapp:{number}",
                    body=message_body,
                )
                logger.info(f"Alerte WhatsApp envoyée à {number}")
                success = True
            except Exception as e:
                logger.error(f"Échec envoi WhatsApp à {number}: {e}")

        return success
