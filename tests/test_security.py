import re
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db, limiter
from app.models import URL
from app.services import create_short_url
from app.utils import URLValidationError, validate_url
from config import get_config
from config.production import ProductionConfig

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ENDPOINT = "/api/v1/urls"


# ---------- security headers ----------

@pytest.mark.parametrize("path", ["/", "/nope1234", "/static/js/app.js"])
def test_security_headers_on_every_response(client, path):
    headers = client.get(path).headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["Permissions-Policy"]
    assert "Content-Security-Policy" in headers


def test_csp_is_strict(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    for directive in (
        "default-src 'self'", "script-src 'self'", "style-src 'self'",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'",
        "frame-ancestors 'none'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_api_and_error_responses_are_hardened_and_not_cached(client):
    responses = [
        client.post(ENDPOINT, json={"url": "https://example.com"}),
        client.post(ENDPOINT, json={}),
        client.get("/api/v1/nothing"),
    ]
    for response in responses:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_redirect_response_is_hardened(client, make_url):
    make_url(code="found1")
    response = client.get("/found1")
    assert response.status_code == 302
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_hsts_is_off_by_default(client):
    assert "Strict-Transport-Security" not in client.get("/", base_url="https://localhost").headers


def test_hsts_only_over_https_when_enabled():
    client = create_app("testing", config_overrides={"HSTS_ENABLED": True}).test_client()
    assert "Strict-Transport-Security" not in client.get("/").headers
    hsts = client.get("/", base_url="https://localhost").headers["Strict-Transport-Security"]
    assert "max-age=31536000" in hsts


def _proxied(count):
    app = create_app("testing", config_overrides={"HSTS_ENABLED": True, "PROXY_COUNT": count})
    return app.test_client()


def test_forwarded_proto_is_ignored_without_a_trusted_proxy():
    response = _proxied(0).get("/", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in response.headers


def test_forwarded_proto_is_honoured_with_a_trusted_proxy():
    response = _proxied(1).get("/", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in response.headers


# ---------- production configuration ----------

@pytest.mark.parametrize("secret", [None, "", "change-me", "dev-only-insecure-key", "too-short"])
def test_production_rejects_weak_secret_keys(monkeypatch, secret):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/linkforge")
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        get_config("production")


def test_production_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "k" * 40)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_config("production")


def test_production_accepts_valid_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/linkforge")
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "k" * 40)
    assert get_config("production") is ProductionConfig


def test_production_security_settings():
    assert ProductionConfig.DEBUG is False
    assert ProductionConfig.TESTING is False
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"
    assert ProductionConfig.HSTS_ENABLED is True


# ---------- rate limiting ----------

@pytest.fixture
def make_limited_app():
    contexts = []

    def _make(**overrides):
        app = create_app("testing", config_overrides={"RATELIMIT_ENABLED": True, **overrides})
        ctx = app.app_context()
        ctx.push()
        contexts.append(ctx)
        db.create_all()
        limiter.reset()
        return app

    yield _make
    for ctx in reversed(contexts):
        db.session.remove()
        db.drop_all()
        ctx.pop()


def _post(client, **kwargs):
    return client.post(ENDPOINT, json={"url": "https://example.com"}, **kwargs)


def test_create_endpoint_is_rate_limited(make_limited_app):
    client = make_limited_app(RATELIMIT_CREATE="3 per minute").test_client()

    responses = [_post(client) for _ in range(5)]

    assert [r.status_code for r in responses] == [201, 201, 201, 429, 429]
    body = responses[-1].get_json()
    assert responses[-1].is_json
    assert body["error"]["code"] == "too_many_requests"
    assert URL.query.count() == 3


def test_redirect_is_rate_limited(make_limited_app):
    app = make_limited_app(RATELIMIT_REDIRECT="3 per minute")
    db.session.add(URL(short_code="found1", original_url="https://example.com"))
    db.session.commit()
    client = app.test_client()

    codes = [client.get("/found1").status_code for _ in range(5)]

    assert codes == [302, 302, 302, 429, 429]


def test_rate_limit_is_tracked_per_client_ip_behind_a_proxy(make_limited_app):
    client = make_limited_app(RATELIMIT_CREATE="2 per minute", PROXY_COUNT=1).test_client()

    def hit(ip):
        return _post(client, headers={"X-Forwarded-For": ip}).status_code

    assert [hit("1.1.1.1"), hit("1.1.1.1"), hit("1.1.1.1")] == [201, 201, 429]
    assert hit("2.2.2.2") == 201


def test_rate_limiting_is_disabled_in_testing_config(client):
    assert all(_post(client).status_code == 201 for _ in range(15))


# ---------- abuse prevention ----------

@pytest.mark.parametrize(
    "url", ["https://bad.example/x", "https://BAD.EXAMPLE/x", "https://www.bad.example/x"]
)
def test_blocked_domains_and_subdomains_are_rejected(url):
    with pytest.raises(URLValidationError, match="not allowed"):
        validate_url(url, blocked_domains=("bad.example",))


@pytest.mark.parametrize(
    "url", ["https://notbad.example", "https://bad.example.org", "https://example.com/bad.example"]
)
def test_similar_looking_domains_are_not_blocked(url):
    assert validate_url(url, blocked_domains=("bad.example",)) == url


def test_api_rejects_configured_blocked_domains(app, client):
    app.config["BLOCKED_DOMAINS"] = ("bad.example",)
    response = client.post(ENDPOINT, json={"url": "https://x.bad.example/p"})
    assert response.status_code == 422
    assert URL.query.count() == 0


def test_service_refuses_to_shorten_its_own_domain(app):
    app.config["BASE_URL"] = "https://lnk.example"
    for url in ("https://lnk.example/abc123", "https://www.lnk.example/x"):
        with pytest.raises(URLValidationError, match="not allowed"):
            create_short_url(url)
    assert URL.query.count() == 0


def test_existing_link_stops_redirecting_once_its_domain_is_blocked(app, client, make_url):
    row = make_url(code="later1", url="https://bad.example/x")
    assert client.get("/later1").status_code == 302

    app.config["BLOCKED_DOMAINS"] = ("bad.example",)

    assert client.get("/later1").status_code == 404
    db.session.refresh(row)
    assert row.click_count == 1


# ---------- static safety checks ----------

def test_templates_do_not_disable_escaping_or_use_inline_code():
    for path in (APP_DIR / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        compact = text.replace(" ", "")
        assert "|safe" not in compact, path
        assert "autoescapefalse" not in compact, path
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), path
        assert not re.search(r"\son[a-z]+\s*=", text), path
        assert not re.search(r"\sstyle\s*=", text), path


def test_javascript_avoids_dangerous_sinks():
    js = (APP_DIR / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink


def test_no_raw_sql_string_building():
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bexecute\(\s*f[\"']", text), path
        assert not re.search(r"from sqlalchemy import[^\n]*\btext\b", text), path