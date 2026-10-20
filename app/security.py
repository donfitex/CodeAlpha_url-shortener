from flask import Flask, request

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def add_security_headers(response):
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        # The Werkzeug debugger needs inline scripts, so CSP is skipped in debug mode only
        if not app.debug:
            headers.setdefault("Content-Security-Policy", CSP)

        if app.config.get("HSTS_ENABLED") and request.is_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if request.path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        return response
