import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _database_url(default: str) -> str:
    url = os.environ.get("DATABASE_URL", default)
    # Some hosts still emit the legacy "postgres://" scheme
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def _csv(name: str) -> tuple[str, ...]:
        raw = os.environ.get(name, "")
        return tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    
class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = _database_url(f"sqlite:///{BASE_DIR / 'linkforge.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Application settings
    BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    SHORT_CODE_LENGTH = int(os.environ.get("SHORT_CODE_LENGTH", 6))
    MAX_URL_LENGTH = int(os.environ.get("MAX_URL_LENGTH", 2048))

    # Shared Flask settings
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 16 * 1024  # 16 KB request limit
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    
        # Abuse prevention
    BLOCKED_DOMAINS = _csv("BLOCKED_DOMAINS")

    # Number of trusted reverse proxies in front of the app (0 = none)
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", 0))

    # Rate limiting
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_CREATE = os.environ.get("RATELIMIT_CREATE", "10 per minute;200 per day")
    RATELIMIT_REDIRECT = os.environ.get("RATELIMIT_REDIRECT", "120 per minute")

    HSTS_ENABLED = False