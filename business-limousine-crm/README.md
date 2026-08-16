# Business Limousine — AI Email CRM

Turns the company's shared inbox into a lightweight CRM: it pulls mail over
IMAP, classifies each message with Google Gemini (`NEW_REQUEST` /
`DISCUSSION` / `OTHER`), extracts client + trip details, groups everything
into per-client conversations in SQLite, and shows it all in a dashboard
where staff can change status, leave internal notes, and reply — replies
go out over SMTP, threaded to the original email.

```
business-limousine-crm/
├── backend/
│   ├── app.py            Flask REST API + serves the frontend
│   ├── config.py         Reads backend/.env
│   ├── database.py       SQLite schema + connection helper
│   ├── models.py         Data access layer (conversations/messages/notes)
│   ├── email_parser.py   MIME parsing, HTML→text, contact-form field extraction
│   ├── ai_classifier.py  Gemini API call (classify + extract)
│   ├── email_fetcher.py  IMAP sync: fetch → parse → classify → store
│   ├── email_sender.py   SMTP reply sending
│   ├── sync_worker.py    Standalone polling loop
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── styles.css
    └── app.js             Vanilla JS — no build step
```

## 1. Install

```bash
cd business-limousine-crm/backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

- **IMAP / SMTP** — OVH mail settings are pre-filled as defaults
  (`ssl0.ovh.net`, IMAP 993, SMTP 465). Set `IMAP_USER` / `IMAP_PASSWORD`
  and `SMTP_USER` / `SMTP_PASSWORD` to the mailbox's real credentials. If
  the account isn't on OVH, swap in the correct host/port.
- **GEMINI_API_KEY** — create one at
  [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).
- **GEMINI_MODEL** — defaults to `gemini-2.0-flash`. Google periodically
  renames/retires models; if classification starts failing with a 404,
  check [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models)
  for the current name.

**Never commit the real `.env` file** — it holds live mailbox and API
credentials.

## 3. Run

Two processes run side by side:

```bash
# Terminal 1 — the web dashboard + API
python app.py
# → http://localhost:5000

# Terminal 2 — the background mail sync (polls every POLL_INTERVAL_SECONDS)
python sync_worker.py
```

Open `http://localhost:5000` in a browser. The database (`crm.db`) and its
tables are created automatically on first run.

You don't have to wait for the poller — the "Sync inbox" button in the
sidebar triggers an on-demand sync via `POST /api/sync`, which runs the
exact same code path.

## 4. How classification works

Each new email's subject + body is sent to Gemini with a prompt asking for
a category plus any client/trip details it can find (`ai_classifier.py`).
Website contact-form submissions (lines like `Name: ...`, `Phone: ...`)
also get a lightweight regex pass (`email_parser.extract_form_fields`) that
fills in anything the AI left blank — useful as a fallback if the AI call
fails or a field is oddly formatted.

Conversations are matched to clients **by email address**. A conversation's
status auto-advances between `NEW_REQUEST` → `DISCUSSION` → `OTHER` as new
mail comes in, but the moment a staff member manually sets it to
`CONFIRMED` or `CLOSED`, incoming mail will never silently overwrite that —
see `models.maybe_auto_update_status`.

## 5. Threading replies

`GET /api/conversations/<id>` returns the full thread. Sending a reply
(`POST /api/conversations/<id>/reply`) sets `In-Reply-To`/`References` to
the last inbound message's `Message-ID`, so it lands as a proper reply in
the client's mail client, and stores the outbound copy in the same
conversation.

## 6. Running the sync worker continuously (production)

`sync_worker.py` is a plain infinite loop — run it under a process
supervisor so it restarts on crash/reboot. Example `systemd` unit:

```ini
# /etc/systemd/system/limo-crm-sync.service
[Unit]
Description=Business Limousine CRM — mail sync worker
After=network.target

[Service]
WorkingDirectory=/opt/business-limousine-crm/backend
ExecStart=/opt/business-limousine-crm/backend/venv/bin/python sync_worker.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/business-limousine-crm/backend/.env

[Install]
WantedBy=multi-user.target
```

Run the Flask app itself behind a real WSGI server (gunicorn/uWSGI) rather
than `python app.py`'s dev server for anything beyond local testing.

## 7. API reference

| Method | Path                                   | Purpose                              |
|--------|-----------------------------------------|---------------------------------------|
| GET    | `/api/conversations?status=&search=`    | List conversations + status counts    |
| GET    | `/api/conversations/<id>`               | Conversation + full thread + notes    |
| PATCH  | `/api/conversations/<id>`                | Update status / client / trip fields  |
| POST   | `/api/conversations/<id>/notes`          | Add an internal note                  |
| POST   | `/api/conversations/<id>/reply`          | Send a threaded email reply           |
| POST   | `/api/sync`                              | Trigger an on-demand IMAP sync        |

## 8. Known limitations / good next steps

- Conversation matching is by email address only; a client writing from a
  second address starts a second conversation.
- Attachments are currently skipped, not stored.
- The dev Flask server (`app.run`) is single-process — fine for staff use
  on a LAN, not for public internet exposure without a real WSGI server,
  HTTPS, and authentication (there is currently no login screen at all —
  add one before deploying anywhere reachable outside the office).
- `sync_worker.py` polls; moving to IMAP `IDLE` would give near-instant
  updates instead of waiting for the next interval.


cd c:\Users\zoubair\Desktop\work\business-limousine-crm\business-limousine-crm\backend
python app.py
(And in a second terminal if you want automatic polling every 60 seconds):
cd c:\Users\zoubair\Desktop\work\business-limousine-crm\business-limousine-crm\backend
python sync_worker.py