import argparse
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PLAN = {
    "Functional": [
        "URL shortening works",
        "Short codes are unique",
        "URLs are stored correctly",
        "Redirect works",
        "Click count works",
        "Invalid URLs are rejected",
        "Missing short codes return 404",
        "Expired links are handled",
    ],
    "Frontend": [
        "Responsive",
        "Professional UI",
        "Rich UX",
        "Loading states",
        "Error states",
        "Success states",
        "Copy button",
        "Mobile support",
        "Accessibility basics",
    ],
    "Backend": [
        "Application factory",
        "Modular architecture",
        "Service layer",
        "Database migrations",
        "PostgreSQL support",
        "Environment configuration",
        "Production configuration",
        "Error handling",
    ],
    "Security": [
        ".env ignored",
        "Secrets not committed",
        "Debug disabled",
        "Safe URL validation",
        "Safe redirects",
        "Input limits",
        "Security headers",
        "Rate-limiting strategy",
        "Production error protection",
    ],
    "Testing": [
        "Unit tests",
        "Model tests",
        "Service tests",
        "API tests",
        "Redirect tests",
        "Error tests",
        "End-to-end tests",
        "CI tests",
        "Coverage reviewed",
    ],
    "Production": [
        "Gunicorn",
        "PostgreSQL",
        "HTTPS",
        "Environment variables",
        "Health endpoint",
        "Logging",
        "Backups",
        "Recovery plan",
        "Deployment documentation",
    ],
}
MANUAL_ITEMS = {"Responsive", "Professional UI", "Rich UX", "Mobile support", "Backups"}


def load_audit():
    spec = importlib.util.spec_from_file_location(
        "release_audit", ROOT / "scripts" / "release_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_audit()


def options(**overrides):
    values = {
        "url": None,
        "run_tests": False,
        "run_e2e": False,
        "run_tools": False,
        "verbose": False,
    }
    return argparse.Namespace(**{**values, **overrides})


@pytest.fixture(scope="module")
def results():
    return audit.run_audit(options())


def test_every_checklist_item_from_the_plan_is_audited(results):
    audited = {(r["section"], r["name"]) for r in results}
    missing = [(s, n) for s, names in PLAN.items() for n in names if (s, n) not in audited]
    assert missing == []
    assert sum(len(names) for names in PLAN.values()) == 52


def test_the_audit_passes_on_this_repository(results):
    failures = [
        f"{r['section']}: {r['name']}: {r['evidence']}" for r in results if r["status"] == "fail"
    ]
    assert failures == []


def test_only_human_judgement_items_need_sign_off(results):
    manual = {r["name"] for r in results if r["status"] == "manual"}
    assert manual == MANUAL_ITEMS


def test_optional_checks_are_skipped_unless_requested(results):
    skipped = {r["name"] for r in results if r["status"] == "skip"}
    assert {"Coverage reviewed", "Full test suite passes", "Smoke test passes"} <= skipped
    assert {
        "Browser tests pass",
        "Lint and format are clean",
        "No known dependency vulnerabilities",
    } <= skipped


def test_every_result_has_evidence(results):
    assert all(r["evidence"].strip() for r in results)


def test_evidence_never_contains_secrets_or_connection_strings(results):
    text = " ".join(r["evidence"] for r in results)
    for forbidden in ("sqlite:///", "postgresql://", "audit-secret-marker", "SECRET_KEY="):
        assert forbidden not in text


def test_report_lists_every_section_and_a_summary(results):
    report = audit.format_report(results)
    for section in PLAN:
        assert section in report
    assert "failed" in report
    assert "[PASS]" in report


def test_markdown_record_is_a_table_with_every_item(results):
    record = audit.format_markdown(results)
    assert record.startswith("# Release audit record")
    assert "| Section | Item | Status | Evidence |" in record
    for names in PLAN.values():
        assert all(name in record for name in names)


def test_main_writes_the_record_and_exits_cleanly(tmp_path, capsys):
    target = tmp_path / "audit-record.md"

    code = audit.main(["--markdown", str(target)])

    assert code == 0
    assert target.read_text(encoding="utf-8").startswith("# Release audit record")
    assert "Audit record written" in capsys.readouterr().out


def test_secret_scanner_detects_credentials_without_exposing_them():
    leaked = "key = 'AKIA" + "ABCDEFGHIJKLMNOP'"
    assert audit.scan_text_for_secrets(leaked) == ["AWS access key"]
    assert audit.scan_text_for_secrets("-----BEGIN PRIVATE KEY-----") == ["private key"]
    assert audit.scan_text_for_secrets('password = "' + "x" * 20 + '"') == ["hard-coded credential"]
    assert audit.scan_text_for_secrets("SECRET_KEY=change-me\nname = 'short'") == []


def test_a_failing_item_is_reported_as_a_failure(monkeypatch):
    def broken(ctx):
        raise audit.AuditFailure("deliberate failure")

    monkeypatch.setattr(audit, "CHECKS", [("Functional", "Broken item", broken, False)])

    results = audit.run_audit(options())

    assert results == [
        {
            "section": "Functional",
            "name": "Broken item",
            "status": "fail",
            "evidence": "deliberate failure",
        }
    ]
    assert audit.main([]) == 1


def test_an_unexpected_error_is_a_failure_not_a_crash(monkeypatch):
    def explodes(ctx):
        raise KeyError("secret-detail")

    monkeypatch.setattr(audit, "CHECKS", [("Functional", "Exploding item", explodes, False)])

    (result,) = audit.run_audit(options())

    assert result["status"] == "fail"
    assert result["evidence"] == "unexpected KeyError"
    assert "secret-detail" not in result["evidence"]
