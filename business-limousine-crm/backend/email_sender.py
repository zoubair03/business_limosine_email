"""Sends outbound replies via SMTP, properly threaded to the original email with CC and attachment support."""
import mimetypes
import os
from pathlib import Path
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from config import Config, UPLOADS_DIR


def send_reply(to_addr, subject, body_text, cc_addr=None, body_html=None,
               attachments=None, in_reply_to_message_id=None):
    """
    Sends an outbound reply (plain text + optional HTML + attachments + CC)
    and returns the new Message-ID.
    """
    Config.require_smtp()

    msg = EmailMessage()
    msg["From"] = f"{Config.SMTP_FROM_NAME} <{Config.SMTP_USER}>"
    msg["To"] = to_addr
    msg["Subject"] = subject

    # Parse CC recipients
    cc_list = []
    if cc_addr:
        if isinstance(cc_addr, list):
            cc_list = [c.strip() for c in cc_addr if c and c.strip()]
        elif isinstance(cc_addr, str):
            cc_list = [c.strip() for c in cc_addr.split(",") if c.strip()]
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

    new_message_id = make_msgid()
    msg["Message-ID"] = new_message_id

    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id

    # Set body content
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    # Attachments
    if attachments:
        for att in attachments:
            file_path = None
            filename = None

            if isinstance(att, dict):
                filename = att.get("filename")
                url = att.get("url", "")
                if url.startswith("/uploads/"):
                    disk_name = url.replace("/uploads/", "")
                    candidate = UPLOADS_DIR / disk_name
                    if candidate.exists():
                        file_path = candidate
                elif att.get("path") and Path(att.get("path")).exists():
                    file_path = Path(att.get("path"))
            elif isinstance(att, (str, Path)):
                p = Path(att)
                if p.exists():
                    file_path = p
                    filename = p.name

            if file_path and file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    
                    ctype, encoding = mimetypes.guess_type(str(file_path))
                    if ctype is None or encoding is not None:
                        ctype = "application/octet-stream"
                    maintype, subtype = ctype.split("/", 1)

                    msg.add_attachment(
                        file_data,
                        maintype=maintype,
                        subtype=subtype,
                        filename=filename or file_path.name,
                    )
                except Exception as ex:
                    print(f"Warning: Failed to attach file {file_path}: {ex}")

    # Build full recipient list for SMTP envelope
    all_recipients = [to_addr] + cc_list

    if Config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT) as server:
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.send_message(msg, to_addrs=all_recipients)
    else:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT) as server:
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.send_message(msg, to_addrs=all_recipients)

    return new_message_id

