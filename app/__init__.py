import logging

from flask import Flask

from config import get_config


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    _configure_logging(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    app.logger.info("LinkForge started (debug=%s)", app.debug)
    return app


def _configure_logging(app: Flask) -> None:
    level = app.config.get("LOG_LEVEL", logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)


def _init_extensions(app: Flask) -> None:
    from app import models  # noqa: F401  (registers models for migrations)
    from app.extensions import db, migrate

    db.init_app(app)
    migrate.init_app(app, db)


def _register_blueprints(app: Flask) -> None:
    from app.routes import register_blueprints

    register_blueprints(app)


def _register_error_handlers(app: Flask) -> None:
    from app.errors import register_error_handlers

    register_error_handlers(app)