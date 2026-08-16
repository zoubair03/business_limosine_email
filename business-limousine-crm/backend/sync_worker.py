"""
Standalone loop that polls the mailbox every POLL_INTERVAL_SECONDS.

Run this as a separate long-lived process alongside the Flask app, e.g.
via systemd, a `screen`/`tmux` session, or a simple Docker service:

    python sync_worker.py

The Flask app also exposes POST /api/sync for an on-demand manual sync,
which reuses the exact same email_fetcher.run_once() function.
"""
import logging
import sys
import time

# Fix Windows console encoding (cp1252 → utf-8) so emoji/French chars in
# log messages don't crash the worker with UnicodeEncodeError
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import Config
from database import init_db
from email_fetcher import run_once
from pending_request_watcher import check_pending_requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_worker")


def main():
    init_db()
    logger.info(
        "Starting sync worker (polling every %ss, mailbox %s)",
        Config.POLL_INTERVAL_SECONDS,
        Config.IMAP_USER,
    )
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Sync cycle failed; will retry next interval")

        try:
            alerted = check_pending_requests()
            if alerted > 0:
                logger.info(f"WhatsApp pending alert cycle sent {alerted} notification(s).")
        except Exception:
            logger.exception("WhatsApp pending request check failed; will retry next interval")

        time.sleep(Config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
