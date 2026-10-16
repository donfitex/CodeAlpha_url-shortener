import logging
import os

from .base import BaseConfig


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    LOG_LEVEL = logging.INFO

    # Secure cookies (requires HTTPS)
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY:
            raise RuntimeError("SECRET_KEY must be set in production.")
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL must be set in production.")