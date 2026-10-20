from flask import Blueprint, current_app, jsonify, request

from app.services import ShortCodeGenerationError, create_short_url
from app.utils import URLValidationError

from app.extensions import limiter

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def error_response(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


@api_bp.post("/urls")
@limiter.limit(lambda: current_app.config["RATELIMIT_CREATE"])
def create_url():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response(
            "invalid_json", "Request body must be a valid JSON object.", 400
        )

    try:
        url = create_short_url(data.get("url"))
    except URLValidationError as exc:
        return error_response("validation_error", str(exc), 422)
    except ShortCodeGenerationError:
        return error_response(
            "service_unavailable",
            "Could not create a short link right now. Please try again.",
            503,
        )
    except Exception:
        current_app.logger.exception("Unexpected error while creating short URL")
        return error_response("internal_error", "Something went wrong.", 500)

    base_url = current_app.config["BASE_URL"]
    return (
        jsonify(
            {
                "short_code": url.short_code,
                "short_url": f"{base_url}/{url.short_code}",
                "original_url": url.original_url,
            }
        ),
        201,
    )