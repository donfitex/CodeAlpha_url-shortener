from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db


def db_failure():
    return OperationalError("SELECT 1", {}, Exception("password=hunter2 host=db.internal"))


# ---------- liveness ----------


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"status": "ok"}


def test_health_does_not_touch_the_database(client):
    with patch.object(db.session, "execute", side_effect=AssertionError("database was queried")):
        assert client.get("/health").status_code == 200


def test_health_still_ok_when_database_is_down(client):
    with patch.object(db.session, "execute", side_effect=db_failure()):
        assert client.get("/health").status_code == 200


def test_health_exposes_no_configuration(client, app):
    text = client.get("/health").get_data(as_text=True)
    for value in (app.config["SECRET_KEY"], app.config["SQLALCHEMY_DATABASE_URI"], "sqlite"):
        assert value not in text


def test_health_supports_head(client):
    response = client.head("/health")
    assert response.status_code == 200
    assert response.get_data() == b""


def test_health_is_not_swallowed_by_the_short_code_route(client, make_url):
    row = make_url(code="health", url="https://example.com/hijack")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    db.session.refresh(row)
    assert row.click_count == 0


# ---------- readiness ----------


def test_ready_returns_ok_when_database_is_reachable(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "checks": {"database": "ok"}}


def test_ready_returns_503_when_database_is_down(client, caplog):
    with patch.object(db.session, "execute", side_effect=db_failure()):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "unavailable",
        "checks": {"database": "unavailable"},
    }
    assert "Readiness check failed" in caplog.text


def test_ready_does_not_leak_error_details(client):
    with patch.object(db.session, "execute", side_effect=db_failure()):
        text = client.get("/health/ready").get_data(as_text=True)

    for secret in ("hunter2", "db.internal", "SELECT", "OperationalError", "Traceback"):
        assert secret not in text


def test_ready_recovers_once_the_database_returns(client):
    with patch.object(db.session, "execute", side_effect=db_failure()):
        assert client.get("/health/ready").status_code == 503

    assert client.get("/health/ready").status_code == 200


# ---------- headers, methods, limits ----------


@pytest.mark.parametrize("path", ["/health", "/health/ready"])
def test_health_responses_are_hardened_and_not_cached(client, path):
    headers = client.get(path).headers
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("path", ["/health", "/health/ready"])
def test_other_methods_return_json_405(client, path):
    response = client.post(path)

    assert response.status_code == 405
    assert response.is_json
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["Allow"]


def test_unknown_health_path_returns_json_404(client):
    response = client.get("/health/nothing")
    assert response.status_code == 404
    assert response.is_json


def test_health_checks_are_not_rate_limited():
    app = create_app(
        "testing",
        config_overrides={"RATELIMIT_ENABLED": True, "RATELIMIT_REDIRECT": "1 per minute"},
    )
    client = app.test_client()

    assert [client.get("/health").status_code for _ in range(5)] == [200] * 5


# ---------- container wiring ----------


def test_production_image_has_a_health_check():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    stage = (root / "Dockerfile").read_text(encoding="utf-8").split("AS production")[1]
    assert "HEALTHCHECK" in stage
    assert "/health" in stage


def test_compose_web_service_has_a_health_check():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "healthcheck:" in compose.split("web:")[1]
    assert "/health" in compose
