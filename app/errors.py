from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, MethodNotAllowed

# status -> (machine code, page title, user-facing message)
ERROR_INFO = {
    400: ("bad_request", "Bad request", "The request could not be understood."),
    404: ("not_found", "Page not found", "The page or link you're looking for doesn't exist."),
    405: ("method_not_allowed", "Method not allowed", "That action isn't supported here."),
    410: ("gone", "Link expired", "This link has expired and is no longer available."),
    413: ("payload_too_large", "Request too large", "The request was too large."),
    429: ("too_many_requests", "Too many requests", "Please slow down and try again shortly."),
    500: (
        "internal_error",
        "Something went wrong",
        "An unexpected error occurred. Please try again later.",
    ),
}
_GENERIC_CLIENT = ("error", "Request failed", "The request could not be completed.")


def _wants_json() -> bool:
    path = request.path
    if path == "/api" or path.startswith("/api/"):
        return True
    return request.accept_mimetypes.best == "application/json"


def _info_for(status: int):
    if status in ERROR_INFO:
        return ERROR_INFO[status]
    return ERROR_INFO[500] if status >= 500 else _GENERIC_CLIENT


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        status = error.code or 500

        # Let redirects (3xx) pass through untouched
        if status < 400:
            return error

        code, title, message = _info_for(status)

        # Flask already logs unhandled exceptions; log manual server errors too
        if status >= 500 and getattr(error, "original_exception", None) is None:
            app.logger.error("Server error %s on %s %s", status, request.method, request.path)

        if _wants_json():
            response = jsonify({"error": {"code": code, "message": message}})
        else:
            template = "404.html" if status == 404 else "error.html"
            try:
                body = render_template(template, status=status, title=title, message=message)
            except Exception:
                app.logger.exception("Error page failed to render")
                body = f"<h1>{status}</h1><p>{message}</p>"
            response = app.response_class(body, mimetype="text/html")

        response.status_code = status
        if isinstance(error, MethodNotAllowed) and error.valid_methods:
            response.headers["Allow"] = ", ".join(error.valid_methods)
        return response
