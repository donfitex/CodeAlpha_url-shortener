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