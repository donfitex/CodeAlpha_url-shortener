import re
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db, limiter
from app.openapi import ERROR_CODES, build_openapi_spec

CREATE = "/api/v1/urls"
REDIRECT = "/{short_code}"
INTERNAL_ENDPOINTS = {"static", "main.index", "api.openapi_json", "api.api_docs"}

# ---------- helpers ----------


def resolve(node, spec):
    """Inline every local $ref so jsonschema can validate against it."""
    if isinstance(node, dict):
        if "$ref" in node:
            target = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return resolve(target, spec)
        return {key: resolve(value, spec) for key, value in node.items()}
    if isinstance(node, list):
        return [resolve(item, spec) for item in node]
    return node


def assert_valid(spec, schema, body, label=""):
    errors = list(Draft202012Validator(resolve(schema, spec)).iter_errors(body))
    assert not errors, f"{label}: {[e.message for e in errors]}"


def assert_conforms(spec, response, path, method):
    """The response uses a documented status, matches its schema, and has its headers."""
    responses = spec["paths"][path][method]["responses"]
    status = str(response.status_code)
    assert status in responses, f"{response.status_code} is not documented for {method} {path}"
    declared = responses[status]
    if response.is_json:
        schema = declared["content"]["application/json"]["schema"]
        assert_valid(spec, schema, response.get_json(), f"{method} {path} {status}")
    for header in declared.get("headers", {}):
        assert header in response.headers, f"missing documented header {header}"


def iter_media(spec):
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            for media_type, media in op.get("requestBody", {}).get("content", {}).items():
                yield f"{method} {path} request", media_type, media
            for status, response in op["responses"].items():
                for media_type, media in response.get("content", {}).items():
                    yield f"{method} {path} {status}", media_type, media


@pytest.fixture
def spec(app):
    return build_openapi_spec(app)


# ---------- the spec itself ----------


def test_spec_is_valid_openapi(spec):
    validate(spec)


def test_spec_metadata(spec):
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "LinkForge API"
    assert spec["servers"] == [{"url": "http://localhost"}]
    assert set(spec["paths"]) == {"/api/v1/urls", "/{short_code}", "/health", "/health/ready"}


def test_every_operation_is_fully_described(spec):
    operation_ids = []
    for methods in spec["paths"].values():
        for op in methods.values():
            operation_ids.append(op["operationId"])
            assert op["summary"] and op["description"] and op["tags"]
            assert op["responses"]
    assert len(operation_ids) == len(set(operation_ids))


def test_all_examples_match_their_schemas(spec):
    checked = 0
    for label, _media_type, media in iter_media(spec):
        if "example" in media and "schema" in media:
            assert_valid(spec, media["schema"], media["example"], label)
            checked += 1
    assert checked >= 10


def test_spec_reflects_configuration(app):
    app.config["BASE_URL"] = "https://lnk.example"
    app.config["MAX_URL_LENGTH"] = 100
    app.config["RATELIMIT_CREATE"] = "7 per minute;50 per day"

    spec = build_openapi_spec(app)

    assert spec["servers"][0]["url"] == "https://lnk.example"
    description = spec["paths"]["/api/v1/urls"]["post"]["description"]
    assert "100 characters" in description
    assert "7 per minute, 50 per day" in description
    example = spec["paths"]["/api/v1/urls"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["example"]
    assert example["short_url"].startswith("https://lnk.example/")


# ---------- spec and code stay in sync ----------


@pytest.mark.parametrize(
    ("spec_path", "concrete_path", "method", "endpoint"),
    [
        ("/api/v1/urls", "/api/v1/urls", "post", "api.create_url"),
        (REDIRECT, "/abc123", "get", "main.redirect_to_url"),
        ("/health", "/health", "get", "health.health"),
        ("/health/ready", "/health/ready", "get", "health.ready"),
    ],
)
def test_documented_operations_map_to_real_routes(
    app, spec, spec_path, concrete_path, method, endpoint
):
    assert method in spec["paths"][spec_path]
    matched, _ = app.url_map.bind("localhost").match(concrete_path, method=method.upper())
    assert matched == endpoint


def test_every_public_route_is_documented(app, spec):
    for rule in app.url_map.iter_rules():
        if rule.endpoint in INTERNAL_ENDPOINTS:
            continue
        path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule)
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            assert method.lower() in spec["paths"].get(path, {}), f"{method} {path} is undocumented"


# ---------- real responses match the documentation ----------


def test_created_response_conforms(client, spec):
    response = client.post(CREATE, json={"url": "https://example.com/very/long/url"})
    assert response.status_code == 201
    assert_conforms(spec, response, CREATE, "post")


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({"json": {}}, 422, "validation_error"),
        ({"json": {"url": "javascript:alert(1)"}}, 422, "validation_error"),
        ({"json": {"url": "http://localhost/admin"}}, 422, "validation_error"),
        ({"data": "{bad json", "content_type": "application/json"}, 400, "invalid_json"),
        ({}, 400, "invalid_json"),
        ({"data": "x" * 20000, "content_type": "application/json"}, 413, "payload_too_large"),
    ],
)
def test_create_error_responses_conform(client, spec, kwargs, status, code):
    response = client.post(CREATE, **kwargs)

    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code
    assert_conforms(spec, response, CREATE, "post")


def test_service_unavailable_response_conforms(client, spec, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code", return_value=existing):
        response = client.post(CREATE, json={"url": "https://example.com/doomed"})

    assert response.status_code == 503
    assert_conforms(spec, response, CREATE, "post")


def test_internal_error_response_conforms(client, spec):
    with patch("app.routes.api.create_short_url", side_effect=RuntimeError("secret=abc")):
        response = client.post(CREATE, json={"url": "https://example.com"})

    assert response.status_code == 500
    assert_conforms(spec, response, CREATE, "post")


def test_rate_limited_response_conforms(spec):
    app = create_app(
        "testing",
        config_overrides={"RATELIMIT_ENABLED": True, "RATELIMIT_CREATE": "1 per minute"},
    )
    with app.app_context():
        db.create_all()
        limiter.reset()
        client = app.test_client()
        client.post(CREATE, json={"url": "https://example.com"})

        response = client.post(CREATE, json={"url": "https://example.com"})

        assert response.status_code == 429
        assert_conforms(spec, response, CREATE, "post")
        db.session.remove()
        db.drop_all()


def test_redirect_response_conforms(client, spec, make_url):
    make_url(code="found1", url="https://example.com/x")
    response = client.get("/found1")
    assert response.status_code == 302
    assert_conforms(spec, response, REDIRECT, "get")


def test_not_found_and_gone_conform_as_json(client, spec, make_url):
    from datetime import datetime, timedelta, timezone

    make_url(code="old123", expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    accept = {"Accept": "application/json"}

    missing = client.get("/zzzzzz", headers=accept)
    expired = client.get("/old123", headers=accept)

    assert (missing.status_code, expired.status_code) == (404, 410)
    assert_conforms(spec, missing, REDIRECT, "get")
    assert_conforms(spec, expired, REDIRECT, "get")


def test_browsers_get_the_documented_html_error_pages(client, spec):
    declared = spec["paths"][REDIRECT]["get"]["responses"]["404"]["content"]
    response = client.get("/zzzzzz")

    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert "text/html" in declared


def test_health_responses_conform(client, spec):
    live = client.get("/health")
    ready = client.get("/health/ready")
    assert live.status_code == ready.status_code == 200
    assert_conforms(spec, live, "/health", "get")
    assert_conforms(spec, ready, "/health/ready", "get")


def test_unready_response_conforms(client, spec):
    error = OperationalError("SELECT 1", {}, Exception("db down"))
    with patch.object(db.session, "execute", side_effect=error):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert_conforms(spec, response, "/health/ready", "get")


def test_every_emitted_error_code_is_documented(client):
    codes = {
        client.get("/api/v1/nothing").get_json()["error"]["code"],
        client.post("/health").get_json()["error"]["code"],
        client.post(CREATE).get_json()["error"]["code"],
    }
    assert codes <= set(ERROR_CODES)


# ---------- the served documentation ----------


def test_openapi_json_is_served_and_valid(client, app):
    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == build_openapi_spec(app)
    validate(response.get_json())


def test_docs_page_lists_every_endpoint_and_example(client):
    response = client.get("/api/v1/docs")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "<h1" in html and "LinkForge API" in html
    for expected in (
        "/api/v1/urls",
        "/{short_code}",
        "/health/ready",
        "aB92xK",
        "validation_error",
        "302",
        "410",
    ):
        assert expected in html


def test_docs_page_works_under_the_strict_csp(client):
    response = client.get("/api/v1/docs")
    html = response.get_data(as_text=True)

    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert all('src="/static/' in tag for tag in scripts)
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)


def test_homepage_links_to_the_docs(client):
    assert "/api/v1/docs" in client.get("/").get_data(as_text=True)
