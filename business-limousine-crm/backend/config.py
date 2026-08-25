"""
Central configuration for the Business Limousine CRM backend.

All values are read dynamically from environment variables (see .env.example)
and automatically reload if backend/.env is edited.
"""
import logging
import os
from pathlib import Path
import secrets

try:
    from dotenv import load_dotenv
except ImportError:
    # Fallback if dotenv is not installed in global environment
    def load_dotenv(*args, **kwargs):
        pass

logger = logging.getLogger("config")

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# The session key that used to ship as the default in this file. It is in the
# public git history, so it is treated as compromised and never used.
_PUBLISHED_DEFAULT_KEY = "business-limousine-secure-dispatch-key-2026"

_SECRET_KEY_FILE = BACKEND_DIR / ".secret_key"
_secret_key_cache = None


def _load_or_create_secret_key():
    """Reads the instance's session key, generating one on first run.

    Kept in a gitignored file rather than the source so it stays out of the public
    repository, and persisted rather than generated per-process so a restart does
    not silently log every user out.
    """
    global _secret_key_cache
    if _secret_key_cache:
        return _secret_key_cache

    try:
        if _SECRET_KEY_FILE.exists():
            key = _SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
            if key:
                _secret_key_cache = key
                return key
    except OSError as exc:
        logger.warning("Could not read %s (%s). Generating a session key in memory.",
                       _SECRET_KEY_FILE.name, exc)

    key = secrets.token_urlsafe(48)
    try:
        _SECRET_KEY_FILE.write_text(key, encoding="utf-8")
        try:
            os.chmod(_SECRET_KEY_FILE, 0o600)  # best effort; a no-op on some filesystems
        except OSError:
            pass
        logger.info("Generated a new session key in backend/%s.", _SECRET_KEY_FILE.name)
    except OSError as exc:
        logger.warning(
            "Could not persist a session key to %s (%s). Using an in-memory key — "
            "everyone will be logged out when this process restarts.",
            _SECRET_KEY_FILE.name, exc,
        )
    _secret_key_cache = key
    return key

DEFAULTS = {
    "IMAP_HOST": "pro3.mail.ovh.net",
    "IMAP_PORT": "993",
    "IMAP_FOLDER": "INBOX",
    "SMTP_HOST": "pro3.mail.ovh.net",
    "SMTP_PORT": "587",
    "SMTP_FROM_NAME": "Business Limousine",
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "DB_PATH": str(PROJECT_ROOT / "crm.db"),
    "POLL_INTERVAL_SECONDS": "60",
    "INITIAL_SYNC_LIMIT": "100",
    "FLASK_HOST": "0.0.0.0",
    "FLASK_PORT": "5000",
}


class _Config:
    def _reload(self):
        env_file = BACKEND_DIR / ".env"
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("'").strip('"')
            except Exception:
                pass
        try:
            load_dotenv(env_file, override=True)
        except Exception:
            pass

    def _get(self, name: str, default=None):
        self._reload()
        return os.environ.get(name, DEFAULTS.get(name, default))

    @property
    def IMAP_HOST(self):
        return self._get("IMAP_HOST")

    @property
    def IMAP_PORT(self):
        return int(self._get("IMAP_PORT", "993"))

    @property
    def IMAP_USER(self):
        return self._get("IMAP_USER")

    @property
    def IMAP_PASSWORD(self):
        return self._get("IMAP_PASSWORD")

    @property
    def IMAP_FOLDER(self):
        return self._get("IMAP_FOLDER", "INBOX")

    @property
    def SMTP_HOST(self):
        return self._get("SMTP_HOST")

    @property
    def SMTP_PORT(self):
        return int(self._get("SMTP_PORT", "587"))

    @property
    def SMTP_USER(self):
        return self._get("SMTP_USER")

    @property
    def SMTP_PASSWORD(self):
        return self._get("SMTP_PASSWORD")

    @property
    def SMTP_FROM_NAME(self):
        return self._get("SMTP_FROM_NAME", "Business Limousine")

    @property
    def GEMINI_API_KEY(self):
        return self._get("GEMINI_API_KEY")

    @property
    def GEMINI_MODEL(self):
        return self._get("GEMINI_MODEL", "gemini-flash-latest")

    @property
    def DB_PATH(self):
        raw = self._get("DB_PATH", str(PROJECT_ROOT / "crm.db"))
        p = Path(raw)
        if not p.is_absolute():
            p = (BACKEND_DIR / p).resolve()
        return str(p)

    @property
    def POLL_INTERVAL_SECONDS(self):
        return int(self._get("POLL_INTERVAL_SECONDS", "60"))

    @property
    def INITIAL_SYNC_LIMIT(self):
        return int(self._get("INITIAL_SYNC_LIMIT", "100"))

    @property
    def SECRET_KEY(self):
        """Signs the session cookie — anyone who knows it can forge a logged-in
        admin session.

        This used to fall back to a constant committed to the repository, which is
        public: the fallback was equivalent to no authentication at all. Now the key
        comes from the environment, or from a generated file kept out of git, and
        the published constant is rejected outright if it is still configured.
        """
        configured = self._get("SECRET_KEY", None)
        if configured and configured != _PUBLISHED_DEFAULT_KEY:
            return configured

        if configured == _PUBLISHED_DEFAULT_KEY:
            logger.warning(
                "SECRET_KEY is still the value published in the public repository. "
                "Ignoring it and using the generated key instead. Set your own "
                "SECRET_KEY in backend/.env to control it."
            )

        return _load_or_create_secret_key()

    @property
    def FLASK_HOST(self):
        return self._get("FLASK_HOST", "0.0.0.0")

    @property
    def FLASK_PORT(self):
        return int(self._get("FLASK_PORT", "5000"))

    # Dispatch Alerts Configuration (Telegram & WhatsApp)
    @property
    def TELEGRAM_BOT_TOKEN(self):
        return self._get("TELEGRAM_BOT_TOKEN")

    @property
    def TELEGRAM_CHAT_IDS(self):
        return self._get("TELEGRAM_CHAT_IDS", "")

    @property
    def WHATSAPP_API_PROVIDER(self):
        return self._get("WHATSAPP_API_PROVIDER", "telegram").strip().lower()

    @property
    def CALLMEBOT_API_KEY(self):
        return self._get("CALLMEBOT_API_KEY")

    @property
    def CALLMEBOT_PHONE(self):
        return self._get("CALLMEBOT_PHONE")

    @property
    def META_WA_PHONE_NUMBER_ID(self):
        return self._get("META_WA_PHONE_NUMBER_ID") or self._get("WHATSAPP_PHONE_NUMBER_ID")

    @property
    def META_WA_ACCESS_TOKEN(self):
        return self._get("META_WA_ACCESS_TOKEN") or self._get("WHATSAPP_ACCESS_TOKEN")

    @property
    def META_WA_BUSINESS_ACCOUNT_ID(self):
        return self._get("META_WA_BUSINESS_ACCOUNT_ID")

    @property
    def TWILIO_ACCOUNT_SID(self):
        return self._get("TWILIO_ACCOUNT_SID")

    @property
    def TWILIO_AUTH_TOKEN(self):
        return self._get("TWILIO_AUTH_TOKEN")

    @property
    def TWILIO_WHATSAPP_FROM(self):
        return self._get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    @property
    def DISPATCHER_WHATSAPP_NUMBERS(self):
        return self._get("DISPATCHER_WHATSAPP_NUMBERS", "")

    @property
    def WHATSAPP_ALERTS_ENABLED(self):
        val = str(self._get("WHATSAPP_ALERTS_ENABLED", "true")).strip().lower()
        return val in ("1", "true", "yes", "on")

    @property
    def WHATSAPP_ALERT_THRESHOLD_MINUTES(self):
        return int(self._get("WHATSAPP_ALERT_THRESHOLD_MINUTES", "10"))

    def require_imap(self):
        if not self.IMAP_USER or not self.IMAP_PASSWORD:
            raise RuntimeError("IMAP_USER / IMAP_PASSWORD are not set. Fill them in backend/.env")

    def require_smtp(self):
        if not self.SMTP_USER or not self.SMTP_PASSWORD:
            raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not set. Fill them in backend/.env")

    def require_gemini(self):
        if not self.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set. Fill it in backend/.env")


Config = _Config()
