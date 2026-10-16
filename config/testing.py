import logging

from .base import BaseConfig


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = False
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    BASE_URL = "http://localhost"
    SHORT_CODE_LENGTH = 6
    LOG_LEVEL = logging.WARNING