"""
Turns a raw RFC822 email (bytes, as returned by IMAP FETCH) into a plain
Python dict, and pulls out structured fields when the mail is clearly a
website contact-form submission (e.g. "Name: ...", "Pickup: ...").
"""
from datetime import timezone
import re
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup


def _decode(part):
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


def html_to_text(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # collapse excess blank lines
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_raw_email(raw_bytes):
    """
    Returns a dict:
        message_id, in_reply_to, references, subject,
        from_name, from_addr, to_addr, date_iso,
        body_text, body_html
    """
    msg = message_from_bytes(raw_bytes, policy=policy.default)

    subject = msg.get("Subject", "") or ""
    from_name, from_addr = parseaddr(msg.get("From", ""))
    _, to_addr = parseaddr(msg.get("To", ""))
    message_id = msg.get("Message-ID")
    in_reply_to = msg.get("In-Reply-To")
    references = msg.get("References")

    date_header = msg.get("Date")
    try:
        if date_header:
            dt = parsedate_to_datetime(date_header)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            date_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            date_iso = None
    except Exception:
        date_iso = None

    body_text, body_html = "", ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and not body_text:
                body_text = _decode(part)
            elif content_type == "text/html" and not body_html:
                body_html = _decode(part)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/html":
            body_html = _decode(msg)
        else:
            body_text = _decode(msg)

    if not body_text and body_html:
        body_text = html_to_text(body_html)

    return {
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "subject": subject.strip(),
        "from_name": from_name.strip(),
        "from_addr": from_addr.strip().lower(),
        "to_addr": to_addr.strip(),
        "date_iso": date_iso,
        "body_text": body_text.strip(),
        "body_html": body_html,
    }


# Common labels used by website booking/contact forms. Matching is
# case-insensitive and tolerant of a few different label spellings.
_FORM_FIELD_PATTERNS = {
    "name": r"(?:full\s*name|name)",
    "email": r"e-?mail",
    "phone": r"(?:phone|tel(?:ephone)?|mobile)",
    "trip_date": r"(?:date|pickup\s*date|travel\s*date)",
    "origin": r"(?:from|pickup|origin|departure)",
    "destination": r"(?:to|drop-?off|destination|arrival)",
}


def extract_form_fields(text):
    """
    Best-effort extraction of 'Label: value' style lines that most website
    contact-form plugins produce. This is a cheap fallback/cross-check that
    runs alongside (not instead of) the AI extraction in ai_classifier.py.
    """
    if not text:
        return {}

    result = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z][A-Za-z \-/]{1,30}?)\s*[:：]\s*(.+)", line)
        if not m:
            continue
        label, value = m.group(1).strip().lower(), m.group(2).strip()
        if not value:
            continue
        for field, pattern in _FORM_FIELD_PATTERNS.items():
            if field in result:
                continue
            if re.fullmatch(pattern, label):
                result[field] = value
                break
    return result
