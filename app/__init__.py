import logging

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.security import register_security_headers
from config import get_config


def create_app(config_name: str | None = None, config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    if config_overrides:
        app.config.update(config_overrides)

    _configure_logging(app)
    _configure_proxy(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    register_security_headers(app)

    storage = app.config.get("RATELIMIT_STORAGE_URI", "")
    if not (app.debug or app.testing) and storage.startswith("memory://"):
        app.logger.warning(
            "Rate limits are stored in process memory. Use a shared store such as "
            "Redis (RATELIMIT_STORAGE_URI) when running multiple workers."
        )

    app.logger.info("LinkForge started (debug=%s)", app.debug)
    return app


def _configure_logging(app: Flask) -> None:
    level = app.config.get("LOG_LEVEL", logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)


def _configure_proxy(app: Flask) -> None:
    """Trust X-Forwarded-For/Proto from exactly PROXY_COUNT reverse proxies."""
    count = app.config.get("PROXY_COUNT", 0)
    if count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=count, x_proto=count)


def _init_extensions(app: Flask) -> None:
    from app import models  # noqa: F401  (registers models for migrations)
    from app.extensions import db, limiter, migrate

    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from app.routes import register_blueprints

    register_blueprints(app)


def _register_error_handlers(app: Flask) -> None:
    from app.errors import register_error_handlers

    register_error_handlers(app)