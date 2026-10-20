import runpy
from pathlib import Path

import pytest
from flask import Flask

from config.production import ProductionConfig

ROOT = Path(__file__).resolve().parent.parent


def run(name, run_name="loaded"):
    return runpy.run_path(str(ROOT / name), run_name=run_name)


# ---------- wsgi.py ----------


def test_wsgi_exposes_a_flask_app(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    app = run("wsgi.py")["app"]
    assert isinstance(app, Flask)
    assert app.config["TESTING"] is True


def test_wsgi_defaults_to_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/linkforge")
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "k" * 40)

    app = run("wsgi.py")["app"]

    assert app.debug is False
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["HSTS_ENABLED"] is True


def test_wsgi_refuses_to_boot_with_weak_secret_by_default(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/linkforge")
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", None)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        run("wsgi.py")


# ---------- run.py ----------


def test_run_py_exposes_app_when_imported(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    assert isinstance(run("run.py")["app"], Flask)


def test_run_py_refuses_the_dev_server_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(SystemExit) as exc:
        run("run.py", run_name="__main__")

    assert "Gunicorn" in str(exc.value)


# ---------- gunicorn.conf.py ----------


@pytest.fixture
def conf(monkeypatch):
    for var in (
        "PORT",
        "WEB_CONCURRENCY",
        "GUNICORN_BIND",
        "GUNICORN_THREADS",
        "GUNICORN_TIMEOUT",
        "GUNICORN_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    return lambda: run("gunicorn.conf.py")


def test_gunicorn_defaults(conf):
    settings = conf()
    assert settings["bind"].endswith(":8000")
    assert settings["workers"] == 2
    assert settings["worker_class"] == "gthread"
    assert settings["threads"] == 4
    assert settings["timeout"] == 30
    assert settings["accesslog"] == "-"
    assert settings["errorlog"] == "-"
    assert settings["max_requests"] > 0
    assert settings["max_requests_jitter"] > 0


def test_gunicorn_environment_overrides(conf, monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    monkeypatch.setenv("GUNICORN_THREADS", "8")
    monkeypatch.setenv("GUNICORN_TIMEOUT", "60")

    settings = conf()

    assert settings["bind"].endswith(":9000")
    assert settings["workers"] == 3
    assert settings["threads"] == 8
    assert settings["timeout"] == 60


# ---------- Dockerfile ----------


def test_production_image_runs_gunicorn_not_the_dev_server():
    stage = (ROOT / "Dockerfile").read_text(encoding="utf-8").split("AS production")[1]
    assert '"gunicorn"' in stage
    assert "wsgi:app" in stage
    assert "gunicorn.conf.py" in stage
    assert '"flask"' not in stage
    assert "run.py" not in stage
    assert "USER app" in stage
