from flask import Blueprint, current_app, jsonify
from sqlalchemy import select

from app.extensions import db

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    """Liveness: the process is running. Deliberately does not touch the database."""
    return jsonify({"status": "ok"})


@health_bp.get("/health/ready")
def ready():
    """Readiness: the application can reach its database."""
    try:
        db.session.execute(select(1))
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Readiness check failed: database unavailable")
        return jsonify({"status": "unavailable", "checks": {"database": "unavailable"}}), 503
    return jsonify({"status": "ok", "checks": {"database": "ok"}})
