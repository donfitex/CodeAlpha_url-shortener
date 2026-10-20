import logging
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import URL

ENDPOINT = "/api/v1/urls"


def post_json(client, payload):
    return client.post(ENDPOINT, json=payload)


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.is_json
    body = response.get_json()
    assert list(body) == ["error"]
    assert sorted(body["error"]) == ["code", "message"]
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body


# ---------- success ----------


def test_valid_request_returns_201_with_expected_structure(client):
    response = post_json(client, {"url": "https://example.com/very/long/url"})

    assert response.status_code == 201
    assert response.is_json
    body = response.get_json()
    assert sorted(body) == ["original_url", "short_code", "short_url"]
    assert body["original_url"] == "https://example.com/very/long/url"
    assert len(body["short_code"]) == 6
    assert body["short_url"] == f"http://localhost/{body['short_code']}"


def test_valid_request_is_persisted(client):
    body = post_json(client, {"url": "https://example.com/stored"}).get_json()

    row = URL.query.filter_by(short_code=body["short_code"]).one()
    assert row.original_url == "https://example.com/stored"
    assert row.click_count == 0
    assert URL.query.count() == 1


def test_short_url_uses_configured_base_url(app, client):
    app.config["BASE_URL"] = "https://lnk.example"
    body = post_json(client, {"url": "https://example.com"}).get_json()
    assert body["short_url"] == f"https://lnk.example/{body['short_code']}"


def test_surrounding_whitespace_is_trimmed(client):
    body = post_json(client, {"url": "  https://example.com/x  "}).get_json()
    assert body["original_url"] == "https://example.com/x"


def test_extra_json_fields_are_ignored(client):
    response = post_json(
        client, {"url": "https://example.com", "admin": True, "short_code": "hack12"}
    )
    assert response.status_code == 201
    assert response.get_json()["short_code"] != "hack12"


def test_same_url_twice_creates_two_distinct_links(client):
    first = post_json(client, {"url": "https://example.com"}).get_json()
    second = post_json(client, {"url": "https://example.com"}).get_json()
    assert first["short_code"] != second["short_code"]
    assert URL.query.count() == 2


def test_international_domain_is_accepted(client):
    assert post_json(client, {"url": "https://bücher.de/seite"}).status_code == 201


# ---------- validation errors (422) ----------


def test_missing_url_field(client):
    body = assert_error(post_json(client, {}), 422, "validation_error")
    assert "required" in body["error"]["message"]


@pytest.mark.parametrize("value", [None, "", "   ", 123, True, ["https://example.com"], {"a": 1}])
def test_url_with_wrong_type_or_empty_value(client, value):
    assert_error(post_json(client, {"url": value}), 422, "validation_error")


@pytest.mark.parametrize(
    "value",
    ["not a url", "example.com", "http://", "https://exa mple.com", "https://example.com:99999"],
)
def test_invalid_urls_are_rejected(client, value):
    assert_error(post_json(client, {"url": value}), 422, "validation_error")
    assert URL.query.count() == 0


@pytest.mark.parametrize("value", ["ftp://example.com", "mailto:a@example.com", "ws://example.com"])
def test_unsupported_schemes_are_rejected(client, value):
    body = assert_error(post_json(client, {"url": value}), 422, "validation_error")
    assert "http" in body["error"]["message"]
    assert URL.query.count() == 0


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>1</script>",
        "file:///etc/passwd",
    ],
)
def test_dangerous_schemes_are_rejected(client, value):
    body = assert_error(post_json(client, {"url": value}), 422, "validation_error")
    assert "not allowed" in body["error"]["message"]
    assert URL.query.count() == 0


@pytest.mark.parametrize(
    "value",
    ["http://localhost/admin", "http://127.0.0.1", "http://192.168.0.1", "http://169.254.169.254/"],
)
def test_private_and_local_targets_are_rejected(client, value):
    assert_error(post_json(client, {"url": value}), 422, "validation_error")
    assert URL.query.count() == 0


def test_url_longer_than_limit_is_rejected(client):
    body = assert_error(
        post_json(client, {"url": "https://example.com/" + "a" * 3000}), 422, "validation_error"
    )
    assert "at most 2048" in body["error"]["message"]


# ---------- malformed requests (400 / 413) ----------


def test_malformed_json(client):
    response = client.post(ENDPOINT, data="{bad json", content_type="application/json")
    assert_error(response, 400, "invalid_json")


def test_empty_body(client):
    assert_error(client.post(ENDPOINT), 400, "invalid_json")


def test_empty_body_with_json_content_type(client):
    assert_error(
        client.post(ENDPOINT, data="", content_type="application/json"), 400, "invalid_json"
    )


@pytest.mark.parametrize("payload", [["https://example.com"], "https://example.com", 42, None])
def test_json_body_that_is_not_an_object(client, payload):
    assert_error(post_json(client, payload), 400, "invalid_json")


def test_non_json_content_type_is_rejected(client):
    response = client.post(
        ENDPOINT, data="url=https://example.com", content_type="application/x-www-form-urlencoded"
    )
    assert_error(response, 400, "invalid_json")
    assert URL.query.count() == 0


def test_oversized_body_returns_json_413(client):
    response = client.post(ENDPOINT, data="x" * 20000, content_type="application/json")
    assert_error(response, 413, "payload_too_large")


# ---------- routing and methods ----------


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_other_methods_return_json_405(client, method):
    response = getattr(client, method)(ENDPOINT)
    assert_error(response, 405, "method_not_allowed")
    assert "POST" in response.headers["Allow"]


def test_unknown_api_path_returns_json_404(client):
    assert_error(client.get("/api/v1/nothing"), 404, "not_found")


# ---------- collisions ----------


def test_collision_is_retried_transparently(client, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code") as gen:
        gen.side_effect = [existing, existing, "Fresh77"]
        response = post_json(client, {"url": "https://example.com/new"})

    assert response.status_code == 201
    assert response.get_json()["short_code"] == "Fresh77"
    assert gen.call_count == 3
    assert URL.query.count() == 2


def test_exhausted_collisions_return_503(client, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code", return_value=existing):
        response = post_json(client, {"url": "https://example.com/doomed"})

    body = assert_error(response, 503, "service_unavailable")
    assert "try again" in body["error"]["message"].lower()
    assert URL.query.count() == 1


def test_service_recovers_after_collision_failure(client, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code", return_value=existing):
        assert post_json(client, {"url": "https://example.com/a"}).status_code == 503
    assert post_json(client, {"url": "https://example.com/b"}).status_code == 201


# ---------- server errors ----------


def test_database_failure_returns_500_without_leaking(client, caplog):
    error = OperationalError("INSERT INTO urls", {}, Exception("password=hunter2 host=db.internal"))
    with caplog.at_level(logging.ERROR), patch.object(db.session, "commit", side_effect=error):
        response = post_json(client, {"url": "https://example.com"})

    assert_error(response, 500, "internal_error")
    text = response.get_data(as_text=True)
    for secret in ("hunter2", "db.internal", "INSERT", "Traceback", "OperationalError"):
        assert secret not in text
    assert "Unexpected error while creating short URL" in caplog.text
    assert URL.query.count() == 0


def test_service_recovers_after_database_failure(client):
    error = OperationalError("INSERT", {}, Exception("db down"))
    with patch.object(db.session, "commit", side_effect=error):
        assert post_json(client, {"url": "https://example.com"}).status_code == 500
    assert post_json(client, {"url": "https://example.com"}).status_code == 201


def test_unexpected_exception_returns_generic_500(client):
    with patch("app.routes.api.create_short_url", side_effect=RuntimeError("secret=abc123")):
        response = post_json(client, {"url": "https://example.com"})

    body = assert_error(response, 500, "internal_error")
    assert "abc123" not in str(body)
    assert body["error"]["message"] == "Something went wrong."


# ---------- response headers ----------


def test_success_and_error_responses_are_json(client):
    assert post_json(client, {"url": "https://example.com"}).mimetype == "application/json"
    assert post_json(client, {}).mimetype == "application/json"
