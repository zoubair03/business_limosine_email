"""
Central configuration for the Business Limousine CRM backend.

All values are read dynamically from environment variables (see .env.example)
and automatically reload if backend/.env is edited.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    # Fallback if dotenv is not installed in global environment
    def load_dotenv(*args, **kwargs):
        pass

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULTS = {
    "IMAP_HOST": "imap.gmail.com",
    "IMAP_PORT": "993",
    "IMAP_FOLDER": "INBOX",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_FROM_NAME": "Business Limousine",
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "DB_PATH": str(PROJECT_ROOT / "crm.db"),
    "POLL_INTERVAL_SECONDS": "60",
    "INITIAL_SYNC_LIMIT": "110",
    "SECRET_KEY": "business-limousine-secure-dispatch-key-2026",
    "FLASK_HOST": "0.0.0.0",
    "FLASK_PORT": "5000",
}


class _Config:
    def _reload(self):
        load_dotenv(BACKEND_DIR / ".env", override=True)

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
        return self._get("SECRET_KEY", "business-limousine-secure-dispatch-key-2026")

    @property
    def FLASK_HOST(self):
        return self._get("FLASK_HOST", "0.0.0.0")

    @property
    def FLASK_PORT(self):
        return int(self._get("FLASK_PORT", "5000"))

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
