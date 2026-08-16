"""Sends outbound replies via SMTP, properly threaded to the original email."""
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from config import Config


def send_reply(to_addr, subject, body_text, in_reply_to_message_id=None):
    """
    Sends a plain-text reply and returns the new Message-ID so it can be
    stored alongside the outbound message row.
    """
    Config.require_smtp()

    msg = EmailMessage()
    msg["From"] = f"{Config.SMTP_FROM_NAME} <{Config.SMTP_USER}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    new_message_id = make_msgid()
    msg["Message-ID"] = new_message_id

    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id

    msg.set_content(body_text)

    if Config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT) as server:
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT) as server:
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.send_message(msg)

    return new_message_id
