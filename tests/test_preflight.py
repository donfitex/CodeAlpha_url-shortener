import logging
import secrets
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app import create_app
from app.extensions import db
from app.preflight import FAIL, PASS, WARN, Check, exit_code, format_report, main, run_checks
from config.production import ProductionConfig

ROOT = Path(__file__).resolve().parent.parent
ENV = {"APP_ENV": "production", "WEB_CONCURRENCY": "1"}
PG_URI = "postgresql://linkforge:s3cret@db.example:5432/linkforge"


@pytest.fixture
def make_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", PG_URI)
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", secrets.token_hex(32))

    def _make(**overrides):
        defaults = {
            "SQLALCHEMY_DATABASE_URI": PG_URI,
            "BASE_URL": "https://lnk.example",
            "PROXY_COUNT": 1,
        }
        return create_app("production", config_overrides={**defaults, **overrides})

    return _make


def statuses(app, env=ENV, **kwargs):
    return {c.name: c.status for c in run_checks(app, env, **kwargs)}


def stamp_head(app):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    with app.app_context(), db.engine.begin() as connection:
        MigrationContext.configure(connection).stamp(script, "head")


# ---------- configuration checks ----------


def test_healthy_production_configuration_passes_everything(make_app):
    checks = run_checks(make_app(), ENV)
    assert [(c.name, c.status) for c in checks if c.status != PASS] == []
    assert len(checks) == 12


@pytest.mark.parametrize(
    ("overrides", "check", "expected"),
    [
        ({"DEBUG": True}, "environment", FAIL),
        ({"TESTING": True}, "environment", FAIL),
        ({"SECRET_KEY": None}, "secret key", FAIL),
        ({"SECRET_KEY": "short"}, "secret key", FAIL),
        ({"SECRET_KEY": "a" * 40}, "secret key", WARN),
        ({"SQLALCHEMY_DATABASE_URI": "sqlite:///x.db"}, "database", FAIL),
        ({"SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u:p@h/db"}, "database", PASS),
        ({"SQLALCHEMY_ENGINE_OPTIONS": {}}, "database", WARN),
        ({"BASE_URL": "http://lnk.example"}, "public URL", FAIL),
        ({"BASE_URL": "https://localhost"}, "public URL", FAIL),
        ({"SESSION_COOKIE_SECURE": False}, "cookies", FAIL),
        ({"SESSION_COOKIE_HTTPONLY": False}, "cookies", FAIL),
        ({"SESSION_COOKIE_SAMESITE": None}, "cookies", FAIL),
        ({"HSTS_ENABLED": False}, "https", FAIL),
        ({"PROXY_COUNT": 0}, "https", WARN),
        ({"RATELIMIT_ENABLED": False}, "rate limiting", FAIL),
        ({"SEND_FILE_MAX_AGE_DEFAULT": None}, "static assets", WARN),
    ],
)
def test_configuration_problems_are_detected(make_app, overrides, check, expected):
    app = make_app()
    app.config.update(overrides)
    assert statuses(app)[check] == expected


def test_wrong_app_env_fails_and_unset_app_env_is_fine(make_app):
    app = make_app()
    assert statuses(app, {"APP_ENV": "development", "WEB_CONCURRENCY": "1"})["environment"] == FAIL
    assert statuses(app, {"WEB_CONCURRENCY": "1"})["environment"] == PASS


@pytest.mark.parametrize(("workers", "expected"), [("1", PASS), ("4", WARN), ("abc", WARN)])
def test_in_memory_rate_limits_are_flagged_with_several_workers(make_app, workers, expected):
    env = {"APP_ENV": "production", "WEB_CONCURRENCY": workers}
    assert statuses(make_app(), env)["rate limiting"] == expected


def test_redis_storage_requires_the_redis_package(make_app, monkeypatch):
    app = make_app()
    app.config["RATELIMIT_STORAGE_URI"] = "redis://cache:6379/0"

    monkeypatch.setattr("app.preflight.importlib.util.find_spec", lambda name: None)
    assert statuses(app)["rate limiting"] == FAIL

    monkeypatch.setattr("app.preflight.importlib.util.find_spec", lambda name: object())
    assert statuses(app)["rate limiting"] == PASS


def test_debug_log_level_is_flagged(make_app):
    app = make_app()
    app.logger.setLevel(logging.DEBUG)
    try:
        assert statuses(app)["logging"] == WARN
    finally:
        app.logger.setLevel(logging.INFO)


def test_missing_or_empty_stylesheet_fails(make_app, tmp_path):
    app = make_app()
    app.static_folder = str(tmp_path)
    assert statuses(app)["static assets"] == FAIL

    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "styles.css").write_text("")
    assert statuses(app)["static assets"] == FAIL


def test_missing_migrations_fail(make_app, monkeypatch, tmp_path):
    monkeypatch.setattr("app.preflight.ROOT", tmp_path)
    assert statuses(make_app())["migrations"] == FAIL


def test_runtime_check_detects_missing_security_headers(make_app):
    app = make_app()
    app.after_request_funcs.clear()
    assert statuses(app)["runtime"] == FAIL


def test_production_static_files_are_cacheable_for_an_hour(make_app):
    response = make_app().test_client().get("/static/css/styles.css")
    assert response.status_code == 200
    assert "max-age=3600" in response.headers["Cache-Control"]


# ---------- database checks ----------


@pytest.fixture
def sqlite_app(make_app, tmp_path):
    def _make(path):
        return make_app(SQLALCHEMY_DATABASE_URI=f"sqlite:///{path.as_posix()}")

    return _make


def test_unreachable_database_stops_the_database_checks(sqlite_app, tmp_path):
    app = sqlite_app(tmp_path / "missing-directory" / "x.db")
    results = statuses(app, check_db=True)
    assert results["database connection"] == FAIL
    assert "migrations applied" not in results
    assert "readiness" not in results


def test_database_without_migrations_fails(sqlite_app, tmp_path):
    results = statuses(sqlite_app(tmp_path / "empty.db"), check_db=True)
    assert results["database connection"] == PASS
    assert results["migrations applied"] == FAIL
    assert results["readiness"] == PASS


def test_migrated_database_passes(sqlite_app, tmp_path):
    app = sqlite_app(tmp_path / "ready.db")
    stamp_head(app)
    results = statuses(app, check_db=True)
    assert results["database connection"] == PASS
    assert results["migrations applied"] == PASS
    assert results["readiness"] == PASS


# ---------- report and exit codes ----------


def test_report_has_labels_and_never_contains_secrets(make_app):
    report = format_report(run_checks(make_app(), ENV))
    assert "[PASS] environment" in report
    assert "12 passed, 0 warnings, 0 failed" in report
    for secret in ("s3cret", "linkforge:s3cret", PG_URI):
        assert secret not in report


def test_exit_codes():
    ok, warn, fail = Check("a", PASS, ""), Check("b", WARN, ""), Check("c", FAIL, "")
    assert exit_code([ok]) == 0
    assert exit_code([ok, warn]) == 0
    assert exit_code([ok, warn], strict=True) == 1
    assert exit_code([ok, fail]) == 1


def test_main_reports_invalid_configuration(monkeypatch, capsys):
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", secrets.token_hex(32))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert main([]) == 1

    out = capsys.readouterr().out
    assert "[FAIL] configuration" in out
    assert "DATABASE_URL" in out


def test_main_exit_status_follows_the_checks(make_app, monkeypatch, capsys):
    monkeypatch.setattr("app.preflight.build_production_app", lambda: (make_app(), None))
    monkeypatch.setenv("APP_ENV", "production")

    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert main(["--strict"]) == 0
    assert "0 failed" in capsys.readouterr().out

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert main([]) == 0
    assert main(["--strict"]) == 1
