import logging
import os
from datetime import timedelta

from .base import BaseConfig

_PLACEHOLDER_KEYS = {"change-me", "dev-only-insecure-key"}


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    LOG_LEVEL = logging.INFO
    HSTS_ENABLED = True

    # Static files are not fingerprinted, so cache them only briefly
    SEND_FILE_MAX_AGE_DEFAULT = timedelta(hours=1)

    # Secure cookies (requires HTTPS)
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def validate(cls):
        key = cls.SECRET_KEY
        if not key or len(key) < 32 or key in _PLACEHOLDER_KEYS:
            raise RuntimeError(
                "SECRET_KEY must be set to a strong value (at least 32 characters) in production."
            )
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL must be set in production.")
