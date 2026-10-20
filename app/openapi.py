from flask import Flask

API_VERSION = "1.0.0"
JSON = "application/json"
HTML = "text/html"

ERROR_CODES = [
    "bad_request",
    "invalid_json",
    "validation_error",
    "not_found",
    "method_not_allowed",
    "gone",
    "payload_too_large",
    "too_many_requests",
    "internal_error",
    "service_unavailable",
    "error",
]


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _error(description: str, code: str, message: str, *, html: bool = False) -> dict:
    content = {
        JSON: {
            "schema": _ref("Error"),
            "example": {"error": {"code": code, "message": message}},
        }
    }
    if html:
        content[HTML] = {"schema": {"type": "string"}}
    return {"description": description, "content": content}


def build_openapi_spec(app: Flask) -> dict:
    config = app.config
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "LinkForge API",
            "version": API_VERSION,
            "description": (
                "Create short links and follow them. No authentication is required. "
                "Every JSON error uses the same structure: "
                '{"error": {"code": "...", "message": "..."}}.'
            ),
        },
        "servers": [{"url": config["BASE_URL"]}],
        "tags": [
            {"name": "URLs", "description": "Create short links."},
            {"name": "Redirect", "description": "Follow short links."},
            {"name": "Health", "description": "Monitoring endpoints."},
        ],
        "paths": _paths(config),
        "components": {"schemas": _schemas(config)},
    }


def _paths(config) -> dict:
    base_url = config["BASE_URL"]
    max_len = config["MAX_URL_LENGTH"]
    create_limit = config["RATELIMIT_CREATE"].replace(";", ", ")
    redirect_limit = config["RATELIMIT_REDIRECT"].replace(";", ", ")
    long_url = "https://example.com/very/long/url"

    return {
        "/api/v1/urls": {
            "post": {
                "operationId": "createShortUrl",
                "tags": ["URLs"],
                "summary": "Create a short URL",
                "description": (
                    "Validates the URL and stores it under a new unique short code. "
                    f"Only http and https URLs up to {max_len} characters are accepted. "
                    "These are rejected: missing, empty or non-string values, malformed URLs, "
                    "other schemes (including javascript:, data: and file:), embedded "
                    "credentials, private or local addresses, and blocked domains. "
                    f"Rate limited per client IP ({create_limit})."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        JSON: {
                            "schema": _ref("CreateUrlRequest"),
                            "example": {"url": long_url},
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "The short URL was created.",
                        "content": {
                            JSON: {
                                "schema": _ref("CreatedUrl"),
                                "example": {
                                    "short_code": "aB92xK",
                                    "short_url": f"{base_url}/aB92xK",
                                    "original_url": long_url,
                                },
                            }
                        },
                    },
                    "400": _error(
                        "The body is missing, is not valid JSON, or is not a JSON object.",
                        "invalid_json",
                        "Request body must be a valid JSON object.",
                    ),
                    "413": _error(
                        "The request body is larger than 16 KB.",
                        "payload_too_large",
                        "The request was too large.",
                    ),
                    "422": _error(
                        "The URL is missing or failed validation. The message explains why.",
                        "validation_error",
                        "Only http:// and https:// URLs are supported.",
                    ),
                    "429": _error(
                        "Rate limit exceeded. Wait before retrying.",
                        "too_many_requests",
                        "Please slow down and try again shortly.",
                    ),
                    "500": _error(
                        "Unexpected server error. Details are logged server-side and never returned.",
                        "internal_error",
                        "Something went wrong.",
                    ),
                    "503": _error(
                        "A unique short code could not be generated. Safe to retry.",
                        "service_unavailable",
                        "Could not create a short link right now. Please try again.",
                    ),
                },
            }
        },
        "/{short_code}": {
            "get": {
                "operationId": "redirectToUrl",
                "tags": ["Redirect"],
                "summary": "Follow a short URL",
                "description": (
                    "Redirects to the original URL with a temporary 302 and counts one click. "
                    "HEAD requests redirect too but are not counted. Browsers receive an HTML "
                    "page for errors; send Accept: application/json to get the JSON error "
                    "structure instead. A link also returns 404 if its stored destination no "
                    "longer passes safety checks, for example after its domain was blocked. "
                    f"Rate limited per client IP ({redirect_limit})."
                ),
                "parameters": [
                    {
                        "name": "short_code",
                        "in": "path",
                        "required": True,
                        "description": "The code returned when the link was created. Case-sensitive.",
                        "schema": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9]{4,32}$",
                            "examples": ["aB92xK"],
                        },
                    }
                ],
                "responses": {
                    "302": {
                        "description": "Redirect to the original URL.",
                        "headers": {
                            "Location": {
                                "description": "The original URL.",
                                "schema": {"type": "string", "format": "uri"},
                            },
                            "Cache-Control": {
                                "description": "Always no-store, so every visit is counted.",
                                "schema": {"type": "string", "enum": ["no-store"]},
                            },
                        },
                    },
                    "404": _error(
                        "No link exists for this code, or the code is malformed.",
                        "not_found",
                        "The page or link you're looking for doesn't exist.",
                        html=True,
                    ),
                    "410": _error(
                        "The link has expired. It is kept but no longer redirects.",
                        "gone",
                        "This link has expired and is no longer available.",
                        html=True,
                    ),
                    "429": _error(
                        "Rate limit exceeded. Wait before retrying.",
                        "too_many_requests",
                        "Please slow down and try again shortly.",
                        html=True,
                    ),
                },
            }
        },
        "/health": {
            "get": {
                "operationId": "getHealth",
                "tags": ["Health"],
                "summary": "Liveness check",
                "description": (
                    "Confirms the process is running. Never touches the database. "
                    "Never cached or rate limited."
                ),
                "responses": {
                    "200": {
                        "description": "The application is running.",
                        "content": {
                            JSON: {
                                "schema": _ref("Health"),
                                "example": {"status": "ok"},
                            }
                        },
                    }
                },
            }
        },
        "/health/ready": {
            "get": {
                "operationId": "getReadiness",
                "tags": ["Health"],
                "summary": "Readiness check",
                "description": (
                    "Confirms the application can reach its database. Responses contain "
                    "only fixed strings, never connection details."
                ),
                "responses": {
                    "200": {
                        "description": "The database is reachable.",
                        "content": {
                            JSON: {
                                "schema": _ref("Readiness"),
                                "example": {"status": "ok", "checks": {"database": "ok"}},
                            }
                        },
                    },
                    "503": {
                        "description": "The database is unreachable.",
                        "content": {
                            JSON: {
                                "schema": _ref("Readiness"),
                                "example": {
                                    "status": "unavailable",
                                    "checks": {"database": "unavailable"},
                                },
                            }
                        },
                    },
                },
            }
        },
    }


def _schemas(config) -> dict:
    max_len = config["MAX_URL_LENGTH"]
    code_len = config["SHORT_CODE_LENGTH"]
    return {
        "CreateUrlRequest": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {
                    "type": "string",
                    "description": f"The http or https URL to shorten, at most {max_len} characters.",
                }
            },
        },
        "CreatedUrl": {
            "type": "object",
            "additionalProperties": False,
            "required": ["short_code", "short_url", "original_url"],
            "properties": {
                "short_code": {
                    "type": "string",
                    "description": f"Unique code, {code_len} characters by default.",
                },
                "short_url": {"type": "string", "description": "The full short link to share."},
                "original_url": {"type": "string", "description": "The validated destination."},
            },
        },
        "Error": {
            "type": "object",
            "additionalProperties": False,
            "required": ["error"],
            "properties": {"error": _ref("ErrorBody")},
        },
        "ErrorBody": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code", "message"],
            "properties": {
                "code": {
                    "type": "string",
                    "enum": ERROR_CODES,
                    "description": "Stable machine-readable error code.",
                },
                "message": {"type": "string", "description": "Safe, human-readable explanation."},
            },
        },
        "Health": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
        },
        "Readiness": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "checks"],
            "properties": {
                "status": {"type": "string", "enum": ["ok", "unavailable"]},
                "checks": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["database"],
                    "properties": {"database": {"type": "string", "enum": ["ok", "unavailable"]}},
                },
            },
        },
    }
