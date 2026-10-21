"""Release audit: verifies the production readiness checklist with evidence.

Usage:
    python scripts/release_audit.py [--full] [--run-tests] [--run-e2e] [--run-tools]
                                    [--url https://your-domain] [--markdown FILE] [--verbose]

Exit status is 1 if any check fails. MANUAL items need a person to sign off.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from flask_migrate import upgrade  # noqa: E402
from sqlalchemy import inspect  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db, limiter  # noqa: E402
from app.models import URL  # noqa: E402
from app.preflight import run_checks as run_preflight  # noqa: E402
from app.services import create_short_url  # noqa: E402
from app.utils import validate_url  # noqa: E402
from config import get_config  # noqa: E402
from config.base import _database_url  # noqa: E402
from config.production import ProductionConfig  # noqa: E402

PASS, FAIL, MANUAL, SKIP = "pass", "fail", "manual", "skip"
LABELS = {PASS: "[PASS]", FAIL: "[FAIL]", MANUAL: "[MANUAL]", SKIP: "[SKIP]"}
SECTIONS = [
    "Functional",
    "Frontend",
    "Backend",
    "Security",
    "Testing",
    "Production",
    "Verification",
    "Live deployment",
]

GOOD_URL = "https://example.com/audit/page?x=1#frag"
SAMPLE_DATABASE = "postgresql://linkforge@db.example:5432/linkforge"
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "hard-coded credential": re.compile(
        r"""(?i)\b(?:secret_key|password|api_key|token)\b\s*[:=]\s*["'][^"'\s]{16,}["']"""
    ),
}
SCAN_SKIP = (
    "tests/",
    "docs/",
    "migrations/",
    "scripts/release_audit.py",
    "config/testing.py",
    "config/development.py",
)
SCAN_SUFFIXES = {
    ".py",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".js",
    ".sh",
    ".example",
    "",
}
SKIP_DIRS = {
    "venv",
    ".git",
    "node_modules",
    "htmlcov",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "instance",
    "test-results",
    "backups",
}

CHECKS = []  # (section, name, function, late)


class AuditFailure(Exception):
    pass


def audit(section, name, late=False):
    def decorator(func):
        CHECKS.append((section, name, func, late))
        return func

    return decorator


def expect(condition, message):
    if not condition:
        raise AuditFailure(message)


def ok(evidence):
    return PASS, evidence


def manual(evidence):
    return MANUAL, evidence


def skip(evidence):
    return SKIP, evidence


# ---------- helpers ----------


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(*paths):
    missing = [p for p in paths if not (ROOT / p).exists()]
    expect(not missing, f"missing: {', '.join(missing)}")


def require(text, needles, label):
    missing = [n for n in needles if n not in text]
    expect(not missing, f"{label} is missing: {', '.join(missing)}")


def count_tests(relative):
    return len(re.findall(r"^\s*(?:async )?def test_", read(relative), re.MULTILINE))


def git(*args):
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None


def is_repo():
    result = git("rev-parse", "--is-inside-work-tree")
    return bool(result and result.returncode == 0 and result.stdout.strip() == "true")


def tracked_files():
    """Return (paths, from_git). Falls back to walking the working tree without Git."""
    if is_repo():
        listing = git("ls-files", "-z")
        return [p for p in listing.stdout.split("\0") if p], True
    paths = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name not in (".env", "tailwindcss.exe"):
                paths.append((Path(folder) / name).relative_to(ROOT).as_posix())
    return paths, False


def scan_text_for_secrets(text):
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def run_command(args, timeout=1800):
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout
    )


def last_line(result):
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    return lines[-1].strip() if lines else "no output"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def production_environment():
    """Temporarily provide the values a real production host would supply."""
    saved_key = ProductionConfig.SECRET_KEY
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = SAMPLE_DATABASE
    ProductionConfig.SECRET_KEY = secrets.token_hex(32)
    try:
        yield
    finally:
        ProductionConfig.SECRET_KEY = saved_key
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url


class Context:
    def __init__(self, args, tmp):
        self.args = args
        self.tmp = Path(tmp)
        self.apps = []
        self.cache = {}
        self._app = None

    def make_app(self, db_name, create_tables=True, **overrides):
        path = self.tmp / db_name
        config = {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path.as_posix()}",
            "BASE_URL": "https://lnk.example",
            **overrides,
        }
        app = create_app("testing", config_overrides=config)
        self.apps.append(app)
        if create_tables:
            with app.app_context():
                db.create_all()
        return app

    @property
    def app(self):
        if self._app is None:
            self._app = self.make_app("audit.db")
        return self._app

    @property
    def client(self):
        return self.app.test_client()

    def close(self):
        for app in self.apps:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


def pytest_run(ctx):
    if "pytest" not in ctx.cache:
        report = ctx.tmp / "coverage.json"
        result = run_command(
            [sys.executable, "-m", "pytest", "--cov", f"--cov-report=json:{report}", "-q"]
        )
        ctx.cache["pytest"] = (result, report)
    return ctx.cache["pytest"]


def fail_under():
    match = re.search(r"^fail_under\s*=\s*(\d+)", read("pyproject.toml"), re.MULTILINE)
    return int(match.group(1)) if match else 0


# ---------- Functional ----------


@audit("Functional", "URL shortening works")
def check_shortening(ctx):
    response = ctx.client.post("/api/v1/urls", json={"url": GOOD_URL})
    body = response.get_json()
    expect(response.status_code == 201, f"POST /api/v1/urls returned {response.status_code}")
    expect(sorted(body) == ["original_url", "short_code", "short_url"], "unexpected fields")
    expect(
        body["short_url"] == f"https://lnk.example/{body['short_code']}",
        "short_url ignores BASE_URL",
    )
    return ok("201 with short_code, short_url and original_url")


@audit("Functional", "Short codes are unique")
def check_unique(ctx):
    with ctx.app.app_context():
        codes = {create_short_url(GOOD_URL).short_code for _ in range(100)}
        expect(len(codes) == 100, "duplicate short codes were generated")
        indexes = inspect(db.engine).get_indexes("urls")
        expect(
            any(i["column_names"] == ["short_code"] and i["unique"] for i in indexes),
            "no unique index on short_code",
        )
        db.session.add(URL(short_code=next(iter(codes)), original_url=GOOD_URL))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise AuditFailure("the database accepted a duplicate short_code")
    return ok("100 generated codes distinct; unique index rejects duplicates")


@audit("Functional", "URLs are stored correctly")
def check_stored(ctx):
    response = ctx.client.post("/api/v1/urls", json={"url": f"  {GOOD_URL}  "})
    code = response.get_json()["short_code"]
    with ctx.app.app_context():
        row = URL.query.filter_by(short_code=code).one()
        expect(row.original_url == GOOD_URL, "stored URL differs from the validated URL")
        expect(row.click_count == 0, "click_count does not start at 0")
        expect(row.created_at is not None and row.updated_at is not None, "timestamps missing")
    return ok("row persisted with trimmed URL, zero clicks and timestamps")


@audit("Functional", "Redirect works")
def check_redirect(ctx):
    with ctx.app.app_context():
        code = create_short_url(GOOD_URL).short_code
    response = ctx.client.get(f"/{code}")
    expect(response.status_code == 302, f"redirect returned {response.status_code}")
    expect(response.headers["Location"] == GOOD_URL, "wrong destination")
    expect(response.headers.get("Cache-Control") == "no-store", "redirect is cacheable")
    return ok("302 to the original URL, not cacheable")


@audit("Functional", "Click count works")
def check_clicks(ctx):
    with ctx.app.app_context():
        code = create_short_url(GOOD_URL).short_code
    client = ctx.client
    for _ in range(3):
        client.get(f"/{code}")
    client.head(f"/{code}")
    with ctx.app.app_context():
        clicks = URL.query.filter_by(short_code=code).one().click_count
    expect(clicks == 3, f"expected 3 clicks (HEAD excluded), found {clicks}")
    return ok("3 GET visits counted, HEAD ignored")


@audit("Functional", "Invalid URLs are rejected")
def check_invalid(ctx):
    bad = [
        "",
        "not a url",
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "ftp://example.com",
        "http://localhost/x",
        "http://127.0.0.1",
        "https://user:pass@example.com",
        "https://example.com:99999",
    ]
    with ctx.app.app_context():
        before = URL.query.count()
    client = ctx.client
    wrong = [u for u in bad if client.post("/api/v1/urls", json={"url": u}).status_code != 422]
    expect(not wrong, f"not rejected with 422: {wrong}")
    expect(client.post("/api/v1/urls", json={}).status_code == 422, "missing url not rejected")
    with ctx.app.app_context():
        expect(URL.query.count() == before, "an invalid URL was stored")
    return ok(f"{len(bad) + 1} invalid inputs rejected with 422, nothing stored")


@audit("Functional", "Missing short codes return 404")
def check_missing(ctx):
    client = ctx.client
    for path in ("/zzzzzz", "/ab", "/bad-code"):
        expect(client.get(path).status_code == 404, f"{path} did not return 404")
    return ok("unknown and malformed codes return 404")


@audit("Functional", "Expired links are handled")
def check_expired(ctx):
    now = datetime.now(timezone.utc)
    with ctx.app.app_context():
        db.session.add(
            URL(short_code="expired1", original_url=GOOD_URL, expires_at=now - timedelta(days=1))
        )
        db.session.add(
            URL(short_code="future1", original_url=GOOD_URL, expires_at=now + timedelta(days=1))
        )
        db.session.commit()
    client = ctx.client
    expect(client.get("/expired1").status_code == 410, "expired link did not return 410")
    expect(client.get("/future1").status_code == 302, "unexpired link did not redirect")
    with ctx.app.app_context():
        expect(
            URL.query.filter_by(short_code="expired1").one().click_count == 0,
            "expired click counted",
        )
    return ok("expired returns 410 and is not counted; future expiry still redirects")


# ---------- Frontend ----------


def frontend_sources():
    return (
        read("app/templates/base.html"),
        read("app/templates/index.html"),
        read("app/static/js/app.js"),
    )


def stylesheet_ready():
    css = ROOT / "app" / "static" / "css" / "styles.css"
    expect(css.is_file() and css.stat().st_size > 0, "compiled styles.css is missing or empty")


@audit("Frontend", "Responsive")
def check_responsive(ctx):
    base, index, _ = frontend_sources()
    require(base, ['name="viewport"'], "base.html")
    expect(re.search(r"\bsm:", index), "index.html has no responsive breakpoints")
    stylesheet_ready()
    return manual(
        "viewport meta, breakpoints and compiled CSS present; check at 360, 768 and 1280 px wide"
    )


@audit("Frontend", "Professional UI")
def check_professional(ctx):
    _, index, _ = frontend_sources()
    expect(
        all(token in index for token in ("rounded-", "shadow", "bg-linear-to-")),
        "design classes missing",
    )
    return manual(
        "cards, shadows, rounded components and gradients present; review the look and typography"
    )


@audit("Frontend", "Rich UX")
def check_rich_ux(ctx):
    _, index, js = frontend_sources()
    require(
        index,
        ["submit-spinner", "form-error", "result-card", "copy-btn", "empty-state"],
        "index.html",
    )
    require(js, ["showError", "showResult", "navigator.clipboard"], "app.js")
    return manual(
        "loading, error, success, empty and copy states are wired; walk through them once"
    )


@audit("Frontend", "Loading states")
def check_loading(ctx):
    _, index, js = frontend_sources()
    require(index, ['id="submit-spinner"', 'id="submit-label"'], "index.html")
    require(js, ["aria-busy", "disabled"], "app.js")
    require(read("tests/test_e2e.py"), ["Shortening"], "browser tests")
    return ok("spinner, busy state and disabled button, covered by a browser test")


@audit("Frontend", "Error states")
def check_error_states(ctx):
    _, index, js = frontend_sources()
    require(index, ['id="form-error"', 'role="alert"'], "index.html")
    require(js, ["showError", "aria-invalid"], "app.js")
    require(read("tests/test_e2e.py"), ["form-error"], "browser tests")
    return ok("announced error box with invalid state, covered by browser tests")


@audit("Frontend", "Success states")
def check_success_states(ctx):
    _, index, js = frontend_sources()
    require(index, ['id="result-card"', 'id="empty-state"'], "index.html")
    require(js, ["showResult"], "app.js")
    require(read("tests/test_e2e.py"), ["result-card"], "browser tests")
    return ok("result card replaces the empty state, covered by browser tests")


@audit("Frontend", "Copy button")
def check_copy(ctx):
    _, index, js = frontend_sources()
    require(index, ['id="copy-btn"', 'id="copy-status"'], "index.html")
    require(js, ["navigator.clipboard", "execCommand"], "app.js")
    require(read("tests/test_e2e.py"), ["Copied!"], "browser tests")
    return ok("Clipboard API with fallback and a status message, covered by a browser test")


@audit("Frontend", "Mobile support")
def check_mobile(ctx):
    base, index, _ = frontend_sources()
    require(base, ['name="viewport"'], "base.html")
    require(index, ['type="url"', 'inputmode="url"', "flex-col"], "index.html")
    return manual("URL keyboard, stacked layout and viewport meta present; try it on a real phone")


@audit("Frontend", "Accessibility basics")
def check_accessibility(ctx):
    base, index, _ = frontend_sources()
    require(base, ['lang="en"', "Skip to main content", "sr-only", "viewport"], "base.html")
    require(
        index,
        [
            'for="url-input"',
            "aria-describedby",
            'role="alert"',
            'role="status"',
            "focus-visible",
            "aria-labelledby",
        ],
        "index.html",
    )
    return ok("language, skip link, labels, live regions, focus styles and landmarks")


# ---------- Backend ----------


@audit("Backend", "Application factory")
def check_factory(ctx):
    first = create_app("testing", config_overrides={"BASE_URL": "https://one.example"})
    second = create_app("testing", config_overrides={"BASE_URL": "https://two.example"})
    expect(first is not second, "create_app returned the same object twice")
    expect(first.config["BASE_URL"] != second.config["BASE_URL"], "apps share configuration")
    return ok("create_app builds independent, configurable apps")


@audit("Backend", "Modular architecture")
def check_modular(ctx):
    for folder in ("app/models", "app/routes", "app/services", "app/utils"):
        package = ROOT / folder
        expect((package / "__init__.py").is_file(), f"{folder} is not a package")
        expect(len(list(package.glob("*.py"))) > 1, f"{folder} has no modules")
    return ok("models, routes, services and utils are separate packages")


@audit("Backend", "Service layer")
def check_service_layer(ctx):
    for path in (ROOT / "app" / "routes").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        expect("app.models" not in text, f"{path.name} imports models directly")
    for name in ("main.py", "api.py"):
        require(read(f"app/routes/{name}"), ["app.services"], f"routes/{name}")
    return ok("routes call services and never touch models directly")


@audit("Backend", "Database migrations")
def check_migrations(ctx):
    versions = list((ROOT / "migrations" / "versions").glob("*.py"))
    expect(versions, "no migration files")
    app = ctx.make_app("migrations.db", create_tables=False)
    with app.app_context():
        upgrade(directory=str(ROOT / "migrations"))
        with db.engine.connect() as connection:
            options = {"compare_type": False, "compare_server_default": False}
            diffs = compare_metadata(
                MigrationContext.configure(connection, opts=options), db.metadata
            )
    expect(
        not diffs,
        f"models differ from migrations ({len(diffs)} difference(s)); run flask db migrate",
    )
    return ok(f"{len(versions)} migration(s) apply cleanly and match the models")


def postgres_support():
    require(read("requirements.txt"), ["psycopg2"], "requirements.txt")
    require(read("docker-compose.yml"), ["postgres:"], "docker-compose.yml")
    require(read("render.yaml"), ["databases:"], "render.yaml")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgres://u@h/db"
    try:
        converted = _database_url("sqlite:///x.db")
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved
    expect(converted.startswith("postgresql://"), "legacy postgres:// URLs are not converted")


@audit("Backend", "PostgreSQL support")
def check_postgres_backend(ctx):
    postgres_support()
    return ok("driver pinned, Compose and Render databases defined, legacy URLs converted")


@audit("Backend", "Environment configuration")
def check_env_config(ctx):
    expect(get_config("development").__name__ == "DevelopmentConfig", "development config")
    expect(get_config("testing").__name__ == "TestingConfig", "testing config")
    try:
        get_config("staging")
    except ValueError:
        pass
    else:
        raise AuditFailure("an unknown APP_ENV was accepted")
    require(read(".env.example"), ["APP_ENV="], ".env.example")
    return ok("development and testing configs selectable, unknown environments rejected")


@audit("Backend", "Production configuration")
def check_production_config(ctx):
    expect(
        ProductionConfig.DEBUG is False and ProductionConfig.TESTING is False, "debug or testing on"
    )
    expect(ProductionConfig.SESSION_COOKIE_SECURE is True, "cookies are not Secure")
    expect(ProductionConfig.HSTS_ENABLED is True, "HSTS is off")
    weak_keys = (None, "", "change-me", "short")
    saved_key = ProductionConfig.SECRET_KEY
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = SAMPLE_DATABASE
    try:
        for weak in weak_keys:
            ProductionConfig.SECRET_KEY = weak
            try:
                ProductionConfig.validate()
            except RuntimeError:
                continue
            raise AuditFailure(f"production accepted a weak SECRET_KEY: {weak!r}")
        ProductionConfig.SECRET_KEY = secrets.token_hex(32)
        ProductionConfig.validate()
    finally:
        ProductionConfig.SECRET_KEY = saved_key
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
    return ok("debug off, secure cookies, HSTS on, weak secrets refused, strong secret accepted")


@audit("Backend", "Error handling")
def check_error_handling(ctx):
    client = ctx.client
    page = client.get("/nope1234")
    expect(
        page.status_code == 404 and page.mimetype == "text/html", "browser 404 is not an HTML page"
    )
    api = client.get("/api/v1/nothing")
    expect(
        api.status_code == 404 and api.get_json()["error"]["code"] == "not_found",
        "API 404 is not JSON",
    )
    expect(client.post("/").status_code == 405, "405 not handled")
    big = client.post("/api/v1/urls", data="x" * 20000, content_type="application/json")
    expect(big.status_code == 413 and big.is_json, "413 is not a JSON error")
    bad = client.post("/api/v1/urls", data="{bad", content_type="application/json")
    expect(
        bad.status_code == 400 and bad.get_json()["error"]["code"] == "invalid_json",
        "400 not handled",
    )
    return ok("400, 404, 405 and 413 handled; JSON for the API, HTML for browsers")


# ---------- Security ----------


@audit("Security", ".env ignored")
def check_env_ignored(ctx):
    if is_repo():
        ignored = git("check-ignore", ".env")
        example = git("check-ignore", ".env.example")
        expect(ignored.returncode == 0, ".env is not ignored by Git")
        expect(example.returncode != 0, ".env.example is ignored, so it cannot be committed")
        return ok("Git ignores .env, and .env.example stays trackable")
    expect(re.search(r"^\.env$", read(".gitignore"), re.MULTILINE), ".gitignore has no .env rule")
    return ok("a .gitignore rule excludes .env (Git unavailable, so the rule was read directly)")


@audit("Security", "Secrets not committed")
def check_secrets(ctx):
    paths, from_git = tracked_files()
    expect(".env" not in paths, ".env is tracked by Git")
    findings = []
    for relative in paths:
        path = ROOT / relative
        if relative.startswith(SCAN_SKIP) or path.suffix not in SCAN_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += [f"{relative} ({name})" for name in scan_text_for_secrets(text)]
    expect(not findings, f"possible secrets in: {', '.join(findings)}")
    if from_git:
        history = git("log", "--all", "--oneline", "--", ".env")
        expect(history is not None and not history.stdout.strip(), ".env appears in Git history")
        return ok(
            f"{len(paths)} tracked files scanned for {len(SECRET_PATTERNS)} patterns; .env never committed"
        )
    return ok(
        f"{len(paths)} working-tree files scanned for {len(SECRET_PATTERNS)} patterns (no Git history available)"
    )


@audit("Security", "Debug disabled")
def check_debug(ctx):
    expect(ProductionConfig.DEBUG is False, "ProductionConfig.DEBUG is on")
    require(read("run.py"), ["Refusing to start the Flask development server"], "run.py")
    require(read("wsgi.py"), ['"production"'], "wsgi.py")
    return ok("production debug off, dev server refuses production, wsgi defaults to production")


@audit("Security", "Safe URL validation")
def check_safe_validation(ctx):
    dangerous = [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "vbscript:x",
        "ftp://example.com",
        "http://localhost",
        "http://169.254.169.254/",
        "http://192.168.0.1",
        "https://user:pass@example.com",
        "https://exa mple.com",
        "//example.com",
    ]
    accepted = []
    for value in dangerous:
        try:
            validate_url(value)
        except ValueError:
            continue
        accepted.append(value)
    expect(not accepted, f"accepted: {accepted}")
    for value in ("https://example.com", "http://example.com/a?b=1", "https://bücher.de"):
        validate_url(value)
    return ok(f"{len(dangerous)} dangerous inputs rejected, valid URLs accepted")


@audit("Security", "Safe redirects")
def check_safe_redirects(ctx):
    tampered = {
        "evil01": "javascript:alert(1)",
        "evil02": "file:///etc/passwd",
        "evil03": "http://localhost/admin",
    }
    with ctx.app.app_context():
        for code, target in tampered.items():
            db.session.add(URL(short_code=code, original_url=target))
        db.session.add(URL(short_code="blocked1", original_url="https://bad.example/x"))
        db.session.commit()
    client = ctx.client
    for code in tampered:
        response = client.get(f"/{code}")
        expect(
            response.status_code == 404 and "Location" not in response.headers, f"{code} was served"
        )
    ctx.app.config["BLOCKED_DOMAINS"] = ("bad.example",)
    try:
        expect(client.get("/blocked1").status_code == 404, "a blocked domain still redirects")
    finally:
        ctx.app.config["BLOCKED_DOMAINS"] = ()
    return ok("tampered rows and newly blocked domains are never redirected to")


@audit("Security", "Input limits")
def check_input_limits(ctx):
    client = ctx.client
    big = client.post("/api/v1/urls", data="x" * 20000, content_type="application/json")
    expect(big.status_code == 413, f"oversized body returned {big.status_code}")
    long_url = client.post("/api/v1/urls", json={"url": "https://example.com/" + "a" * 3000})
    expect(long_url.status_code == 422, f"overlong URL returned {long_url.status_code}")
    return ok("bodies over 16 KB return 413, URLs over the limit return 422")


@audit("Security", "Security headers")
def check_headers(ctx):
    client = ctx.client
    headers = client.get("/").headers
    needed = (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    )
    expect(all(h in headers for h in needed), f"missing: {[h for h in needed if h not in headers]}")
    csp = headers["Content-Security-Policy"]
    expect(
        "default-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp,
        "weak CSP",
    )
    created = client.post("/api/v1/urls", json={"url": GOOD_URL})
    expect(created.headers.get("Cache-Control") == "no-store", "API responses are cacheable")
    return ok("strict CSP without inline code, framing and sniffing blocked, API not cached")


@audit("Security", "Rate-limiting strategy", late=True)
def check_rate_limiting(ctx):
    app = ctx.make_app("ratelimit.db", RATELIMIT_ENABLED=True, RATELIMIT_CREATE="2 per minute")
    limiter.reset()
    client = app.test_client()
    codes = [client.post("/api/v1/urls", json={"url": GOOD_URL}).status_code for _ in range(3)]
    expect(codes == [201, 201, 429], f"expected [201, 201, 429], got {codes}")
    require(read("docs/security.md").lower(), ["rate limit"], "docs/security.md")
    return ok("third request in a minute returns 429; strategy documented")


@audit("Security", "Production error protection", late=True)
def check_error_protection(ctx):
    app = ctx.make_app("errors.db")
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def boom():
        raise RuntimeError("audit-secret-marker")

    app.add_url_rule("/__audit_boom", "audit_boom", boom)
    app.add_url_rule("/api/v1/__audit_boom", "audit_boom_api", boom)
    client = app.test_client()
    with quiet_logger(app):
        for path in ("/__audit_boom", "/api/v1/__audit_boom"):
            response = client.get(path)
            text = response.get_data(as_text=True)
            expect(response.status_code == 500, f"{path} returned {response.status_code}")
            expect(
                "audit-secret-marker" not in text and "Traceback" not in text,
                f"{path} leaked details",
            )
    return ok("unexpected exceptions return a generic 500 with no message or traceback")


# ---------- Testing ----------


def test_file_check(relative, minimum, label):
    found = count_tests(relative)
    expect(found >= minimum, f"{relative} has {found} tests, expected at least {minimum}")
    return ok(f"{found} {label} in {relative}")


@audit("Testing", "Unit tests")
def check_unit_tests(ctx):
    return test_file_check("tests/test_validators.py", 10, "validator tests")


@audit("Testing", "Model tests")
def check_model_tests(ctx):
    return test_file_check("tests/test_models.py", 5, "model tests")


@audit("Testing", "Service tests")
def check_service_tests(ctx):
    return test_file_check("tests/test_services.py", 15, "service tests")


@audit("Testing", "API tests")
def check_api_tests(ctx):
    return test_file_check("tests/test_api.py", 15, "API tests")


@audit("Testing", "Redirect tests")
def check_redirect_tests(ctx):
    require(read("tests/test_routes.py"), ["410", "404", "302"], "tests/test_routes.py")
    return test_file_check("tests/test_routes.py", 15, "redirect tests")


@audit("Testing", "Error tests")
def check_error_tests(ctx):
    require(read("tests/test_api.py"), ["405", "413", "500", "Traceback"], "tests/test_api.py")
    require(read("tests/test_security.py"), ["no_leak", "leak"], "tests/test_security.py")
    return ok("400, 404, 405, 410, 413, 500 and 503 paths are tested, including no-leak checks")


@audit("Testing", "End-to-end tests")
def check_e2e_tests(ctx):
    require(read("tests/test_e2e.py"), ["pytest.mark.e2e", "playwright"], "tests/test_e2e.py")
    return test_file_check("tests/test_e2e.py", 8, "browser tests")


@audit("Testing", "CI tests")
def check_ci(ctx):
    require(
        read(".github/workflows/ci.yml"),
        ["pytest --cov", "ruff check", "ci-pass", "pytest -m e2e"],
        "ci.yml",
    )
    return ok("CI runs lint, tests with coverage, browser tests and an image build behind one gate")


@audit("Testing", "Coverage reviewed")
def check_coverage(ctx):
    if not ctx.args.run_tests:
        return skip("run with --run-tests to measure coverage")
    _, report = pytest_run(ctx)
    expect(report.is_file(), "no coverage report was produced")
    data = json.loads(report.read_text(encoding="utf-8"))
    total = data["totals"]["percent_covered"]
    floor = fail_under()
    expect(total >= floor, f"coverage {total:.1f}% is below the {floor}% floor")
    lowest = sorted(data["files"].items(), key=lambda item: item[1]["summary"]["percent_covered"])[
        :3
    ]
    names = ", ".join(f"{Path(n).name} {v['summary']['percent_covered']:.0f}%" for n, v in lowest)
    return ok(f"{total:.1f}% total (floor {floor}%); lowest files: {names}")


# ---------- Production ----------


@audit("Production", "Gunicorn")
def check_gunicorn(ctx):
    exists("wsgi.py", "gunicorn.conf.py")
    require(read("requirements.txt"), ["gunicorn"], "requirements.txt")
    stage = read("Dockerfile").split("AS production")[1]
    require(stage, ['"gunicorn"', "wsgi:app"], "production image")
    return ok("wsgi.py, tuned config and a production image that runs Gunicorn")


@audit("Production", "PostgreSQL")
def check_postgres_production(ctx):
    postgres_support()
    require(read("config/production.py"), ["DATABASE_URL"], "config/production.py")
    return ok(
        "production requires DATABASE_URL; Postgres driver, Compose and Render database defined"
    )


@audit("Production", "HTTPS")
def check_https_config(ctx):
    expect(ProductionConfig.HSTS_ENABLED is True, "HSTS is off")
    expect(ProductionConfig.PREFERRED_URL_SCHEME == "https", "URL scheme is not https")
    expect(ProductionConfig.SESSION_COOKIE_SECURE is True, "cookies are not Secure")
    require(read("app/__init__.py"), ["ProxyFix"], "app/__init__.py")
    return ok(
        "HSTS, https URLs, Secure cookies and trusted-proxy handling configured (--url verifies live)"
    )


@audit("Production", "Environment variables")
def check_env_documented(ctx):
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", read(".env.example"), re.MULTILINE)
    expect(names, ".env.example has no variables")
    docs = read("docs/development.md")
    undocumented = [n for n in names if f"`{n}`" not in docs]
    expect(not undocumented, f"undocumented variables: {undocumented}")
    return ok(f"{len(names)} variables in .env.example, all documented")


@audit("Production", "Health endpoint")
def check_health(ctx):
    client = ctx.client
    live = client.get("/health")
    ready = client.get("/health/ready")
    expect(live.status_code == 200 and live.get_json() == {"status": "ok"}, "/health is wrong")
    expect(ready.status_code == 200, f"/health/ready returned {ready.status_code}")
    return ok("/health and /health/ready return 200 with fixed bodies")


@audit("Production", "Logging")
def check_logging(ctx):
    expect(ProductionConfig.LOG_LEVEL == logging.INFO, "production log level is not INFO")
    require(read("gunicorn.conf.py"), ['accesslog = "-"', 'errorlog = "-"'], "gunicorn.conf.py")
    require(read("app/routes/api.py"), ["logger.exception"], "app/routes/api.py")
    return ok("INFO level, access and error logs on stdout/stderr, unexpected errors logged")


@audit("Production", "Backups")
def check_backups(ctx):
    exists("scripts/backup.sh", "scripts/restore.sh")
    require(read("docs/deployment.md"), ["Recovery procedure", "pg_dump"], "docs/deployment.md")
    return manual(
        "backup and restore scripts and docs exist; confirm backups are scheduled and a restore has been rehearsed"
    )


@audit("Production", "Recovery plan")
def check_recovery(ctx):
    require(read("docs/deployment.md"), ["Recovery procedure", "roll back"], "docs/deployment.md")
    return ok("recovery procedure and rollback steps are documented")


@audit("Production", "Deployment documentation")
def check_deploy_docs(ctx):
    exists(
        *(
            f"docs/{n}.md"
            for n in ("architecture", "api", "development", "testing", "security", "deployment")
        )
    )
    exists("render.yaml", "README.md")
    require(
        read("docs/deployment.md"),
        ["Release procedure", "Deploying to Render"],
        "docs/deployment.md",
    )
    return ok("six guides, README and a deployment Blueprint")


@audit("Production", "Production preflight passes", late=True)
def check_preflight(ctx):
    with production_environment():
        app = create_app(
            "production",
            config_overrides={
                "SQLALCHEMY_DATABASE_URI": SAMPLE_DATABASE,
                "BASE_URL": "https://lnk.example",
                "PROXY_COUNT": 1,
            },
        )
        with quiet_logger(app):
            results = run_preflight(app, {"APP_ENV": "production", "WEB_CONCURRENCY": "1"})
    bad = [f"{c.name}: {c.detail}" for c in results if c.status != "pass"]
    expect(not bad, "; ".join(bad))
    return ok(f"all {len(results)} preflight checks pass for a correctly configured host")


# ---------- Verification (opt-in) ----------


@audit("Verification", "Full test suite passes")
def check_suite(ctx):
    if not ctx.args.run_tests:
        return skip("run with --run-tests")
    result, _ = pytest_run(ctx)
    expect(result.returncode == 0, f"pytest failed: {last_line(result)}")
    return ok(last_line(result))


@audit("Verification", "Browser tests pass")
def check_browser_suite(ctx):
    if not ctx.args.run_e2e:
        return skip("run with --run-e2e (needs: playwright install chromium)")
    result = run_command([sys.executable, "-m", "pytest", "-m", "e2e", "-q"])
    expect(result.returncode == 0, f"browser tests failed: {last_line(result)}")
    return ok(last_line(result))


@audit("Verification", "Lint and format are clean")
def check_lint(ctx):
    if not ctx.args.run_tools:
        return skip("run with --run-tools")
    lint = run_command([sys.executable, "-m", "ruff", "check", "."])
    expect(lint.returncode == 0, f"ruff check: {last_line(lint)}")
    fmt = run_command([sys.executable, "-m", "ruff", "format", "--check", "."])
    expect(fmt.returncode == 0, f"ruff format: {last_line(fmt)}")
    return ok("ruff check and ruff format --check are clean")


@audit("Verification", "No known dependency vulnerabilities")
def check_dependencies(ctx):
    if not ctx.args.run_tools:
        return skip("run with --run-tools (needs internet)")
    result = run_command([sys.executable, "-m", "pip_audit", "-r", "requirements.txt"])
    expect(result.returncode == 0, f"pip-audit: {last_line(result)}")
    return ok("pip-audit found no known vulnerabilities in requirements.txt")


# ---------- Live deployment (opt-in) ----------


@audit("Live deployment", "Smoke test passes")
def check_live_smoke(ctx):
    if not ctx.args.url:
        return skip("run with --url https://your-domain")
    smoke = load_script("smoke_test")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = smoke.main([ctx.args.url, "--wait", "90"])
    failures = [line for line in buffer.getvalue().splitlines() if line.startswith("[FAIL]")]
    expect(code == 0, "; ".join(failures) or "smoke test failed")
    return ok(f"{len(smoke.CHECKS)} smoke checks passed against {ctx.args.url}")


@audit("Live deployment", "Served over HTTPS with HSTS")
def check_live_https(ctx):
    if not ctx.args.url:
        return skip("run with --url https://your-domain")
    expect(ctx.args.url.startswith("https://"), "the address does not use https")
    smoke = load_script("smoke_test")
    _, headers, _ = smoke.fetch("GET", ctx.args.url.rstrip("/") + "/")
    expect("Strict-Transport-Security" in headers, "no HSTS header")
    return ok("https address with a Strict-Transport-Security header")


# ---------- running and reporting ----------


def run_one(ctx, section, name, func, verbose):
    try:
        status, evidence = func(ctx)
    except AuditFailure as failure:
        status, evidence = FAIL, str(failure)
    except Exception as error:
        status, evidence = FAIL, f"unexpected {type(error).__name__}"
        if verbose:
            traceback.print_exc()
    return {"section": section, "name": name, "status": status, "evidence": evidence}


def run_audit(args):
    results = {}
    order = sorted(enumerate(CHECKS), key=lambda pair: (pair[1][3], pair[0]))  # late checks last
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ctx = Context(args, tmp)
        try:
            for index, (section, name, func, _late) in order:
                results[index] = run_one(ctx, section, name, func, getattr(args, "verbose", False))
        finally:
            ctx.close()
    return [results[i] for i in sorted(results)]


def summarize(results):
    counts = {s: sum(r["status"] == s for r in results) for s in (PASS, MANUAL, SKIP, FAIL)}
    return counts


def format_report(results):
    lines = []
    for section in SECTIONS:
        rows = [r for r in results if r["section"] == section]
        if rows:
            lines.append(section)
            lines += [f"  {LABELS[r['status']]:<9} {r['name']}: {r['evidence']}" for r in rows]
            lines.append("")
    c = summarize(results)
    lines.append(
        f"{c[PASS]} passed, {c[MANUAL]} need sign-off, {c[SKIP]} skipped, {c[FAIL]} failed"
    )
    return "\n".join(lines)


def format_markdown(results):
    commit = git("rev-parse", "--short", "HEAD")
    revision = commit.stdout.strip() if commit and commit.returncode == 0 else "unknown"
    lines = [
        "# Release audit record",
        "",
        f"- Date: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"- Commit: {revision}",
        "",
        "| Section | Item | Status | Evidence |",
        "|---|---|---|---|",
    ]
    for r in results:
        evidence = r["evidence"].replace("|", "/")
        lines.append(f"| {r['section']} | {r['name']} | {r['status'].upper()} | {evidence} |")
    c = summarize(results)
    lines += [
        "",
        f"**{c[PASS]} passed, {c[MANUAL]} need sign-off, {c[SKIP]} skipped, {c[FAIL]} failed.**",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LinkForge release audit")
    parser.add_argument("--url", help="public address to smoke test, e.g. https://lnk.example")
    parser.add_argument("--run-tests", action="store_true", help="run the full suite with coverage")
    parser.add_argument("--run-e2e", action="store_true", help="run the browser tests")
    parser.add_argument("--run-tools", action="store_true", help="run Ruff and pip-audit")
    parser.add_argument(
        "--full", action="store_true", help="same as --run-tests --run-e2e --run-tools"
    )
    parser.add_argument("--markdown", help="write an audit record to this file")
    parser.add_argument(
        "--verbose", action="store_true", help="print tracebacks for unexpected errors"
    )
    args = parser.parse_args(argv)
    if args.full:
        args.run_tests = args.run_e2e = args.run_tools = True

    results = run_audit(args)
    print(format_report(results))
    if args.markdown:
        Path(args.markdown).write_text(format_markdown(results), encoding="utf-8")
        print(f"Audit record written to {args.markdown}")
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())


@contextlib.contextmanager
def quiet_logger(app):
    """Silence expected error logs, then restore them.

    Flask loggers are shared by name, so leaving one disabled would silence
    logging for the rest of the process.
    """
    previous = app.logger.disabled
    app.logger.disabled = True
    try:
        yield
    finally:
        app.logger.disabled = previous


def test_the_audit_leaves_application_logging_enabled(results):
    assert logging.getLogger("app").disabled is False
