"""Application configuration loaded from environment variables (.env file).

Everything that is environment-specific or sensitive — the MongoDB URL and
credentials, the database name, and the collection / GridFS bucket names — is
read from environment variables here so nothing is hard-coded in the source.
Copy ``.env.example`` to ``.env`` and fill in your values.
"""

import os
import re
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Project root is two levels up from this file: backend/config/settings.py.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load variables from a .env file at the project root, if present.
load_dotenv(BASE_DIR / ".env")


def _build_mongo_url(url: str, username: str, password: str) -> str:
    """Inject and URL-encode credentials into a MongoDB connection string.

    This lets users keep passwords with special characters (like '@', '/', ':')
    in MONGODB_USERNAME / MONGODB_PASSWORD without hand-encoding them. Any
    credentials already present in the URL are replaced.
    """
    if not url or (not username and not password):
        return url

    match = re.match(r"^(mongodb(?:\+srv)?://)(.*)$", url)
    if not match:
        return url

    scheme, rest = match.group(1), match.group(2)

    # The authority (host part) ends at the first '/' or '?'.
    host_boundary = re.search(r"[/?]", rest)
    boundary = host_boundary.start() if host_boundary else len(rest)
    authority, tail = rest[:boundary], rest[boundary:]

    # Drop any existing "user:pass@" so we can supply our own.
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]

    creds = quote_plus(username)
    if password:
        creds += ":" + quote_plus(password)

    return f"{scheme}{creds}@{authority}{tail}"


class Settings:
    # ---- Database connection ----
    MONGODB_DB: str = os.getenv("MONGODB_DB", "chattingsystem")

    # Either provide a full MONGODB_URL (with credentials already in it),
    # or provide MONGODB_URL without credentials plus MONGODB_USERNAME /
    # MONGODB_PASSWORD and they will be encoded and injected automatically.
    MONGODB_URL: str = _build_mongo_url(
        os.getenv("MONGODB_URL", ""),
        os.getenv("MONGODB_USERNAME", ""),
        os.getenv("MONGODB_PASSWORD", ""),
    )

    # ---- Collection / bucket names (override via .env if you like) ----
    USERS_COLLECTION: str = os.getenv("USERS_COLLECTION", "users")
    CHATS_COLLECTION: str = os.getenv("CHATS_COLLECTION", "chats")
    MESSAGES_COLLECTION: str = os.getenv("MESSAGES_COLLECTION", "messages")
    PUSH_SUBSCRIPTIONS_COLLECTION: str = os.getenv(
        "PUSH_SUBSCRIPTIONS_COLLECTION", "push_subscriptions"
    )
    # GridFS bucket for uploaded files (creates "<name>.files" / "<name>.chunks").
    UPLOADS_BUCKET: str = os.getenv("UPLOADS_BUCKET", "uploads")

    # ---- Web Push (VAPID) ----
    VAPID_PUBLIC_KEY: str = os.getenv("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY: str = os.getenv("VAPID_PRIVATE_KEY", "")
    VAPID_SUBJECT: str = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")

    # ---- Security limits ----
    # Max size of an uploaded file (in megabytes).
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    # Max characters allowed in a single text message.
    MAX_MESSAGE_LENGTH: int = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))
    # Max messages a single WebSocket connection may send per 10 seconds.
    WS_RATE_LIMIT: int = int(os.getenv("WS_RATE_LIMIT", "40"))
    # Comma-separated list of allowed browser origins for the WebSocket.
    # Leave empty to enforce same-origin automatically (recommended).
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "")

    # ---- Logging ----
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def push_enabled(self) -> bool:
        """Push works only when both VAPID keys are configured."""
        return bool(self.VAPID_PUBLIC_KEY and self.VAPID_PRIVATE_KEY)

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def allowed_origins(self) -> list[str]:
        """Parsed allow-list of origins (empty list => enforce same-origin)."""
        return [o.strip().rstrip("/") for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    def validate(self) -> None:
        if not self.MONGODB_URL:
            raise RuntimeError(
                "MONGODB_URL is not set. Copy .env.example to .env and fill in "
                "your MongoDB connection string."
            )


settings = Settings()
