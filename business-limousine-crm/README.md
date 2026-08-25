# Business Limousine — Dispatch Console

One authenticated web app for the office, in two halves that share a shell:

**Mail CRM.** Pulls the shared inbox over IMAP, classifies each message with
Google Gemini (`NEW_REQUEST` / `DISCUSSION` / `OTHER`), extracts client and
trip details, groups everything into per-client conversations in SQLite, and
lets staff change status, leave internal notes and reply — replies go out over
SMTP, threaded to the original email.

**Fleet analytics & quoting.** Revenue, fleet, client, chauffeur and operations
reporting built from Waynium mission exports, plus a quote calculator fitted on
8,032 executed bookings and a Google-review request composer. Reached from the
**Intelligence** section of the sidebar.

```
business-limousine-crm/
├── backend/
│   ├── app.py            Flask REST API + serves the frontend
│   ├── auth.py           Password hashing, sessions, role decorators
│   ├── config.py         Reads backend/.env; manages the session key
│   ├── database.py       SQLite schema + connection helper
│   ├── models.py         Data access layer (conversations/messages/notes/users)
│   ├── email_parser.py   MIME parsing, HTML→text, contact-form field extraction
│   ├── ai_classifier.py  Gemini API call (classify + extract)
│   ├── email_fetcher.py  IMAP sync: fetch → parse → classify → store
│   ├── email_sender.py   SMTP reply sending
│   ├── sync_worker.py    Standalone polling loop
│   ├── analytics/
│   │   ├── pricing/              Fits the price model from a Waynium export
│   │   ├── make_sample.py        Builds the fabricated demo dataset
│   │   └── dashboard_data.json   Generated. Gitignored — real client data.
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── styles.css        Dispatch console shell
    ├── app.js            Shell, manifest, settings — vanilla JS, no build step
    ├── analytics.css     Bridges the analytics components onto the shell palette
    └── analytics.js      Charts, quote calculator, review composer
```

## Accounts

The first run seeds two accounts. **Change both passwords immediately** — they
are published in this README and in `models.ensure_default_admin`.

| Email | Password | Role |
|---|---|---|
| `admin@businesslimousine.com` | `admin123` | ADMIN |
| `dispatcher@businesslimousine.com` | `dispatch123` | DISPATCHER |

Every `/api/*` route except `POST /api/auth/login` requires a session;
user management additionally requires the ADMIN role.

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
| POST   | `/api/auth/login` / `logout` / `me`      | Session authentication                |
| GET    | `/api/analytics`                         | Analytics payload (see below)         |
| POST   | `/api/conversations/<id>/flags`          | Read / starred / archived             |
| POST   | `/api/conversations/mark-all-read`       | Mark the current view read            |

`GET /api/conversations` takes `status`, `search`, `archived`, `limit` (max 200,
default 50) and `offset`, and returns `total` / `has_more` alongside the page.

## 8. The inbox

The manifest behaves like a mail client, not a table of records.

- **Read and unread** are tracked per conversation, separately from the dispatch
  status — a thread can be CONFIRMED and still unread. Opening one marks it read;
  new inbound mail marks it unread again and pulls it out of the archive, which
  is what makes a client's reply visible. Sending marks it read.
- **Rows show sender, subject and a preview** of the latest message, with quoted
  replies and signatures stripped so a long thread doesn't preview as
  `> On Tuesday, X wrote:`. Thread length, attachments, internal notes and
  "we replied last" each get an indicator.
- **Star and archive** per row. Archiving removes it from the inbox views and
  from the sidebar badge counts.
- **Search covers message subjects and bodies**, not just the client record.
- **Keyboard:** `↑`/`↓` (or `j`/`k`) move, `Enter` opens, `U` toggles unread,
  `S` stars, `E` archives. Ignored while typing.
- **Paged at 50** with a "Load more" button, rather than fetching every
  conversation on every poll.

The 15-second poll reconciles the list in place: rows that are still present keep
their DOM node, so a refresh no longer resets scroll position or drops keyboard
focus, and it no longer reloads the thread you are reading.

## 9. Fleet analytics & quoting

The analytics do **not** come from the CRM mailbox database. They are fitted
from Waynium mission exports by `backend/analytics/pricing`, which writes
`backend/analytics/dashboard_data.json`; `GET /api/analytics` serves that file
to logged-in users.

### Refreshing the numbers

```bash
cd backend/analytics
# drop the new export_*.csv in this folder first — the newest is picked up
cd pricing
python features.py             # clean + geocode every booking  -> feat.pkl
python engine.py               # fit the price model            -> engine.json
python export_payload.py       # pricing + comparables          -> ../dashboard_data.json
python reviews_payload.py      # review-request ride list       -> ../dashboard_data.json
python driver_hours_payload.py # chauffeur hours + shifts       -> ../dashboard_data.json
```

Restart the app afterwards, or just reload the page — the payload is cached on
file mtime, so a rebuilt file is picked up automatically.

`backend/analytics/pricing/README.md` documents how the model is fitted and,
importantly, how far to trust each mode.

### The demo dataset

`dashboard_data.json` and `export_*.csv` are **gitignored**: they name clients
alongside revenue, chauffeurs alongside earnings, and passengers alongside
pickup addresses. This repository is public.

So that a fresh clone still runs, `dashboard_data.sample.json` is committed — a
shape-identical copy with every identity replaced by a fabricated one and every
figure jittered. The API falls back to it when the real file is absent and sets
`is_sample: true`, which makes the UI show a "Demonstration data" banner rather
than passing invented revenue off as real.

Rebuild it with `python backend/analytics/make_sample.py`. That script refuses
to write if any real identity survives anonymisation, so a new name-carrying
field added upstream fails the build instead of being published quietly.

## 10. Security notes

**The session key.** `Config.SECRET_KEY` used to fall back to a constant
committed to this public repository — knowing it is enough to forge a
logged-in admin cookie, which made the login screen decorative. The app now
generates a random key into `backend/.secret_key` (gitignored) on first run,
and refuses that published constant if it is still set. Set your own
`SECRET_KEY` in `backend/.env` for a multi-server deployment.

**Removing the exposed uploads from history.** `business-limousine-crm/uploads/`
held 24 client attachments — invoices, quotes and a RIB with bank details — that
were committed before `.gitignore` covered them, and are therefore still public
in this repository's history. They have now been untracked, which stops new ones
being added but does **not** remove the existing ones. To purge them:

```bash
pip install git-filter-repo
git filter-repo --path business-limousine-crm/uploads --invert-paths --force
git push origin --force --all
git push origin --force --tags
```

This rewrites history: everyone with a clone must re-clone. Anything already
public should be treated as disclosed regardless — rotate the bank details on
that RIB rather than assuming the purge undoes the exposure. Consider making
the repository private.

**Default accounts.** `admin123` / `dispatch123` are published above. Change
them on first login.

## 11. Known limitations / good next steps

- Conversation matching is by email address only; a client writing from a
  second address starts a second conversation.
- Attachments are currently skipped, not stored.
- The dev Flask server (`app.run`) is single-process — fine for staff use on a
  LAN, not for public internet exposure without a real WSGI server and HTTPS.
- `sync_worker.py` polls; moving to IMAP `IDLE` would give near-instant
  updates instead of waiting for the next interval.
- Analytics are read-only and refresh by re-running the pipeline; there is no
  in-app upload for a new Waynium export yet.