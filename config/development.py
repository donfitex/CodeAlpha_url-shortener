import logging

from .base import BASE_DIR, BaseConfig


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SECRET_KEY = BaseConfig.SECRET_KEY or "dev-only-insecure-key"
    SQLALCHEMY_DATABASE_URI = BaseConfig.SQLALCHEMY_DATABASE_URI
    LOG_LEVEL = logging.DEBUG