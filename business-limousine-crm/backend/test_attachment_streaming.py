"""End-to-end test of the on-demand attachment endpoint, against a stub mailbox.

Covers the happy path plus every failure the route is meant to distinguish:
unauthenticated, unknown part, a message with no IMAP reference, and a message
that has since disappeared from the mailbox.
"""
import json
import sys
from email.message import EmailMessage

BACKEND = r"C:/Users/zouba/OneDrive/Desktop/catalogue BL/bl-app/business-limousine-crm/backend"
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

SECRET = b"%PDF-1.4 fake bank details IBAN BE00 1111 2222 3333"

msg = EmailMessage()
msg["Subject"] = "Invoice + RIB"
msg["From"] = "supplier@example.com"
msg["To"] = "contact@business-limousine.com"
msg["Message-ID"] = "<stream-test@example>"
msg["Date"] = "Mon, 25 Aug 2026 09:00:00 +0000"
msg.set_content("Bank details attached.")
msg.add_attachment(SECRET, maintype="application", subtype="pdf", filename="RIB.pdf")
RAW = msg.as_bytes()

# --- stub IMAP -------------------------------------------------------------
KNOWN_UID = "4242"
imap_calls = []


class StubIMAP:
    def __init__(self, host, port):
        imap_calls.append(("connect", host, port))

    def login(self, u, p):
        imap_calls.append(("login", u))

    def select(self, folder):
        imap_calls.append(("select", folder))

    def uid(self, cmd, uid, spec):
        imap_calls.append((cmd, uid, spec))
        if str(uid) == KNOWN_UID:
            return "OK", [(b"1 (RFC822 {%d}" % len(RAW), RAW)]
        return "OK", [None]          # message no longer in the mailbox

    def logout(self):
        imap_calls.append(("logout",))


import imaplib
imaplib.IMAP4_SSL = StubIMAP

import app as flaskapp
from database import db_session
import models

fails = 0
def ok(m): print("  ok   " + m)
def bad(m):
    global fails
    fails += 1
    print("  FAIL " + m)

def check(cond, good, wrong):
    """`cond and ok(x) or bad(y)` is a trap: ok() returns None, so bad() always
    ran too and every passing check also reported a failure."""
    ok(good) if cond else bad(wrong)

# --- seed two messages: one with a UID, one without ------------------------
ATT = [{"filename": "RIB.pdf", "file_size": len(SECRET),
        "content_type": "application/pdf", "part_index": 2}]

with db_session() as conn:
    cid = models.create_conversation(conn, "supplier@example.com", "Stub Supplier", "NEW_REQUEST")
    conn.execute("DELETE FROM messages WHERE message_id IN ('<stream-ok@x>','<stream-nouid@x>','<stream-gone@x>')")
    for mid, uid in (("<stream-ok@x>", KNOWN_UID), ("<stream-nouid@x>", None), ("<stream-gone@x>", "9999")):
        models.add_message(conn, cid, "inbound", "Invoice + RIB", "body", None,
                           "supplier@example.com", "contact@business-limousine.com",
                           attachments=ATT, message_id=mid,
                           imap_uid=uid, imap_folder="INBOX")
    ids = {r["message_id"]: r["id"] for r in conn.execute(
        "SELECT id, message_id FROM messages WHERE message_id LIKE '<stream-%'")}

flaskapp.app.config["TESTING"] = True
client = flaskapp.app.test_client()

print("\n1. Unauthenticated is refused")
r = client.get(f"/api/messages/{ids['<stream-ok@x>']}/attachments/2")
check(r.status_code == 401, f"HTTP {r.status_code} without a session", f"expected 401, got {r.status_code}")

print("\n2. Log in")
r = client.post("/api/auth/login", json={"email": "admin@businesslimousine.com",
                                         "password": "admin123"})
check(r.status_code == 200, "session established", f"login failed: {r.status_code}")

print("\n3. Happy path streams the real bytes from the mailbox")
imap_calls.clear()
r = client.get(f"/api/messages/{ids['<stream-ok@x>']}/attachments/2")
if r.status_code != 200:
    bad(f"expected 200, got {r.status_code}: {r.data[:160]}")
else:
    ok(f"HTTP 200, {len(r.data)} bytes")
    check(r.data == SECRET, "bytes identical to the original attachment", "payload does not match the source")
    check("application/pdf" in r.headers.get("Content-Type", ""), f"content-type {r.headers['Content-Type']}", "wrong content-type")
    check("no-store" in r.headers.get("Cache-Control", ""), f"Cache-Control: {r.headers['Cache-Control']}", "missing no-store")
    check(any(c[0] == "fetch" for c in imap_calls), "went to IMAP for the bytes", "no IMAP fetch")

print("\n4. Nothing was written to disk")
import os
from config import UPLOADS_DIR
n = len(os.listdir(UPLOADS_DIR))
check(n == 0, "uploads/ still empty after serving the file", f"{n} files appeared in uploads/")

print("\n5. Failure modes are distinguished")
r = client.get(f"/api/messages/{ids['<stream-ok@x>']}/attachments/99")
check(r.status_code == 404, "unknown part -> 404", f"unknown part -> {r.status_code}")

r = client.get(f"/api/messages/{ids['<stream-nouid@x>']}/attachments/2")
code = r.get_json().get("code") if r.is_json else None
check(r.status_code == 409 and code == "NO_IMAP_REFERENCE",
      "message without an IMAP reference -> 409 NO_IMAP_REFERENCE", f"no-uid case -> {r.status_code} {code}")

r = client.get(f"/api/messages/{ids['<stream-gone@x>']}/attachments/2")
code = r.get_json().get("code") if r.is_json else None
check(r.status_code == 410 and code == "MESSAGE_GONE",
      "message deleted from the mailbox -> 410 MESSAGE_GONE", f"gone case -> {r.status_code} {code}")

r = client.get("/api/messages/999999/attachments/0")
check(r.status_code == 404, "unknown message -> 404", f"unknown message -> {r.status_code}")

print("\n6. The URL the frontend receives points at the stream, not /uploads")
r = client.get(f"/api/conversations/{cid}")
msgs = r.get_json().get("messages", [])
urls = [a["url"] for m in msgs for a in (m.get("attachments") or [])]
check(bool(urls) and all("/api/messages/" in u for u in urls), f"attachment url -> {urls[0] if urls else '-'}", f"urls: {urls}")

# cleanup
with db_session() as conn:
    conn.execute("DELETE FROM messages WHERE message_id LIKE '<stream-%'")
    conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))

print("\n" + (f"*** {fails} FAILURE(S) ***" if fails else "ALL STREAMING CHECKS PASSED"))
sys.exit(1 if fails else 0)
