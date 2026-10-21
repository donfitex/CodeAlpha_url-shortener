"""Production readiness checks.

Run with the production environment variables set:

    python -m app.preflight [--check-db] [--strict]

Exit status is 1 if any check fails (or warns, with --strict).
"""

import argparse
import importlib.util
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask
from sqlalchemy import select

from app import create_app
from app.extensions import db

ROOT = Path(__file__).resolve().parent.parent

PASS, WARN, FAIL = "pass", "warn", "fail"
LABELS = {PASS: "[PASS]", WARN: "[WARN]", FAIL: "[FAIL]"}
PLACEHOLDER_KEYS = {"change-me", "dev-only-insecure-key", "test-secret-key"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _check(name: str, problems: list[str], warnings: list[str], ok_detail: str) -> Check:
    if problems:
        return Check(name, FAIL, "; ".join(problems))
    if warnings:
        return Check(name, WARN, "; ".join(warnings))
    return Check(name, PASS, ok_detail)


def _workers(env: Mapping[str, str]) -> int:
    try:
        return int(env.get("WEB_CONCURRENCY", 2))
    except ValueError:
        return 2


# ---------- configuration checks (no database needed) ----------


def check_environment(app: Flask, env: Mapping[str, str]) -> Check:
    problems = []
    if app.debug:
        problems.append("debug mode is on")
    if app.testing:
        problems.append("testing mode is on")
    app_env = env.get("APP_ENV")
    if app_env not in (None, "production"):
        problems.append(f"APP_ENV is {app_env!r}; the server would not use the production config")
    return _check("environment", problems, [], "debug and testing off, production config selected")


def check_secret_key(app: Flask, env: Mapping[str, str]) -> Check:
    key = app.config.get("SECRET_KEY") or ""
    problems, warnings = [], []
    if len(key) < 32 or key in PLACEHOLDER_KEYS:
        problems.append("SECRET_KEY must be a strong random value of at least 32 characters")
    elif len(set(key)) < 10:
        warnings.append("SECRET_KEY has very little variety; use secrets.token_hex(32)")
    return _check("secret key", problems, warnings, f"{len(key)} characters")


def check_database_config(app: Flask, env: Mapping[str, str]) -> Check:
    scheme = urlsplit(str(app.config["SQLALCHEMY_DATABASE_URI"])).scheme
    problems, warnings = [], []
    if not scheme.startswith("postgresql"):
        problems.append(f"database is {scheme or 'unknown'}; production needs PostgreSQL")
    if not app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}).get("pool_pre_ping"):
        warnings.append("pool_pre_ping is off; dead connections will not be replaced")
    return _check("database", problems, warnings, "PostgreSQL with connection health checks")


def check_public_url(app: Flask, env: Mapping[str, str]) -> Check:
    parts = urlsplit(app.config["BASE_URL"])
    host = parts.hostname or ""
    problems = []
    if parts.scheme != "https":
        problems.append("BASE_URL must use https")
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        problems.append("BASE_URL points at a local address")
    return _check("public URL", problems, [], f"short links use {parts.scheme}://{host}")


def check_cookies(app: Flask, env: Mapping[str, str]) -> Check:
    config = app.config
    problems = []
    if not config.get("SESSION_COOKIE_SECURE"):
        problems.append("SESSION_COOKIE_SECURE is off")
    if not config.get("SESSION_COOKIE_HTTPONLY"):
        problems.append("SESSION_COOKIE_HTTPONLY is off")
    if config.get("SESSION_COOKIE_SAMESITE") not in ("Lax", "Strict"):
        problems.append("SESSION_COOKIE_SAMESITE must be Lax or Strict")
    return _check("cookies", problems, [], "Secure, HttpOnly and SameSite are set")


def check_https(app: Flask, env: Mapping[str, str]) -> Check:
    problems, warnings = [], []
    if not app.config.get("HSTS_ENABLED"):
        problems.append("HSTS is disabled")
    if not app.config.get("PROXY_COUNT"):
        warnings.append(
            "PROXY_COUNT is 0; behind a TLS-terminating proxy the app cannot see https "
            "or the real client IP"
        )
    return _check(
        "https", problems, warnings, f"HSTS on, {app.config['PROXY_COUNT']} trusted proxy"
    )


def check_rate_limiting(app: Flask, env: Mapping[str, str]) -> Check:
    config = app.config
    if not config.get("RATELIMIT_ENABLED"):
        return Check("rate limiting", FAIL, "rate limiting is disabled")
    scheme = urlsplit(str(config.get("RATELIMIT_STORAGE_URI", "memory://"))).scheme
    workers = _workers(env)
    if scheme == "memory":
        if workers > 1:
            return Check(
                "rate limiting",
                WARN,
                f"limits are stored per process, so with {workers} workers the effective "
                f"limit is {workers}x. Use a shared store",
            )
        return Check("rate limiting", PASS, "in-memory limits with a single worker")
    if scheme in ("redis", "rediss") and importlib.util.find_spec("redis") is None:
        return Check(
            "rate limiting", FAIL, "storage is Redis but the 'redis' package is not installed"
        )
    return Check("rate limiting", PASS, f"shared {scheme} storage")


def check_server(app: Flask, env: Mapping[str, str]) -> Check:
    problems = [
        f"{name} is missing"
        for name in ("wsgi.py", "gunicorn.conf.py")
        if not (ROOT / name).is_file()
    ]
    if importlib.util.find_spec("gunicorn") is None:
        problems.append("gunicorn is not installed")
    return _check("server", problems, [], f"Gunicorn via wsgi.py, {_workers(env)} workers")


def check_logging(app: Flask, env: Mapping[str, str]) -> Check:
    level = app.logger.getEffectiveLevel()
    warnings = []
    if level < logging.INFO:
        warnings.append("log level is below INFO; debug logs may expose sensitive details")
    return _check(
        "logging", [], warnings, f"{logging.getLevelName(level)}, logs go to stdout/stderr"
    )


def check_static_assets(app: Flask, env: Mapping[str, str]) -> Check:
    css = Path(app.static_folder or "") / "css" / "styles.css"
    problems, warnings = [], []
    if not css.is_file() or css.stat().st_size == 0:
        problems.append("app/static/css/styles.css is missing or empty; rebuild the Tailwind CSS")
    if app.config.get("SEND_FILE_MAX_AGE_DEFAULT") is None:
        warnings.append("static files have no cache lifetime")
    return _check("static assets", problems, warnings, "compiled CSS present, cache lifetime set")


def check_migration_files(app: Flask, env: Mapping[str, str]) -> Check:
    count = len(list((ROOT / "migrations" / "versions").glob("*.py")))
    problems = []
    if not (ROOT / "migrations" / "env.py").is_file():
        problems.append("migrations/ is missing")
    elif count == 0:
        problems.append("no migration files found")
    return _check("migrations", problems, [], f"{count} migration file(s)")


def check_runtime_behaviour(app: Flask, env: Mapping[str, str]) -> Check:
    client = app.test_client()
    problems = []
    missing = client.get("/api/v1/__preflight__")
    body = missing.get_json(silent=True) or {}
    if missing.status_code != 404 or "error" not in body:
        problems.append("unknown API paths do not return the JSON error structure")
    health = client.get("/health")
    if health.status_code != 200:
        problems.append(f"/health returned {health.status_code}")
    if "Content-Security-Policy" not in health.headers:
        problems.append("security headers are missing")
    return _check("runtime", problems, [], "JSON errors, /health and security headers work")


STATIC_CHECKS: tuple[Callable[[Flask, Mapping[str, str]], Check], ...] = (
    check_environment,
    check_secret_key,
    check_database_config,
    check_public_url,
    check_cookies,
    check_https,
    check_rate_limiting,
    check_server,
    check_logging,
    check_static_assets,
    check_migration_files,
    check_runtime_behaviour,
)


# ---------- database checks (--check-db) ----------


def check_database_connection(app: Flask) -> Check:
    with app.app_context():
        try:
            db.session.execute(select(1))
        except Exception as exc:
            db.session.rollback()
            return Check("database connection", FAIL, f"cannot connect ({type(exc).__name__})")
    return Check("database connection", PASS, "connected")


def check_migrations_applied(app: Flask) -> Check:
    try:
        config = Config()
        config.set_main_option("script_location", str(ROOT / "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with app.app_context(), db.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:
        return Check("migrations applied", FAIL, f"could not read revisions ({type(exc).__name__})")
    if current != head:
        return Check(
            "migrations applied",
            FAIL,
            f"database is at {current or 'no revision'}, expected {head}; "
            "run flask --app run db upgrade",
        )
    return Check("migrations applied", PASS, f"database is at head ({head})")


def check_readiness(app: Flask) -> Check:
    status = app.test_client().get("/health/ready").status_code
    if status != 200:
        return Check("readiness", FAIL, f"/health/ready returned {status}")
    return Check("readiness", PASS, "/health/ready returned 200")


# ---------- running and reporting ----------


def run_checks(app: Flask, env: Mapping[str, str], check_db: bool = False) -> list[Check]:
    results = [check(app, env) for check in STATIC_CHECKS]
    if check_db:
        connection = check_database_connection(app)
        results.append(connection)
        if connection.status == PASS:
            results.extend([check_migrations_applied(app), check_readiness(app)])
    return results


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"{LABELS[c.status]} {c.name.ljust(width)}  {c.detail}" for c in checks]
    counts = {s: sum(c.status == s for c in checks) for s in (PASS, WARN, FAIL)}
    lines += ["", f"{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed"]
    return "\n".join(lines)


def exit_code(checks: list[Check], strict: bool = False) -> int:
    blocking = {FAIL, WARN} if strict else {FAIL}
    return 1 if any(c.status in blocking for c in checks) else 0


def build_production_app() -> tuple[Flask | None, str | None]:
    try:
        return create_app("production"), None
    except RuntimeError as exc:
        return None, str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LinkForge production readiness checks")
    parser.add_argument(
        "--check-db", action="store_true", help="also check the database and migrations"
    )
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    app, error = build_production_app()
    if app is None:
        checks = [Check("configuration", FAIL, error or "could not build the application")]
    else:
        checks = run_checks(app, os.environ, check_db=args.check_db)

    print(format_report(checks))
    return exit_code(checks, args.strict)


if __name__ == "__main__":
    sys.exit(main())
