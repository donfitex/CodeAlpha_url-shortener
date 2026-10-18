from flask import Blueprint, abort, current_app, redirect, request

from app.extensions import db
from app.services import (
    ShortCodeExpired,
    ShortCodeNotFound,
    record_click,
    resolve_short_code,
)
from app.utils import URLValidationError, validate_url

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    return "LinkForge is running"


@main_bp.get("/<short_code>")
def redirect_to_url(short_code: str):
    try:
        url = resolve_short_code(short_code)
    except ShortCodeNotFound:
        abort(404)
    except ShortCodeExpired:
        abort(410)

    # Defense in depth: never redirect to anything that fails validation
    try:
        destination = validate_url(
            url.original_url, max_length=current_app.config["MAX_URL_LENGTH"]
        )
    except URLValidationError:
        current_app.logger.error(
            "Blocked unsafe stored destination for code %s", url.short_code
        )
        abort(404)

        # Only real visits count; HEAD requests (previews, uptime checks) do not
    if request.method != "HEAD":
        try:
            record_click(url)
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to record click for %s", url.short_code)

    response = redirect(destination, code=302)
    response.headers["Cache-Control"] = "no-store"
    return response