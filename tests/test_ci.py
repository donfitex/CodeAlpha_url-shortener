import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
BLOCKING_JOBS = ["lint", "test", "e2e", "build"]


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def run_commands(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_workflow_defines_every_expected_job(workflow):
    assert {"lint", "test", "e2e", "build", "audit", "ci-pass"} <= set(workflow["jobs"])


def test_workflow_runs_on_pushes_and_pull_requests(workflow):
    # PyYAML (YAML 1.1) parses the bare key `on` as the boolean True
    triggers = workflow.get("on", workflow.get(True))
    assert "push" in triggers
    assert "pull_request" in triggers


def test_permissions_are_read_only_and_no_secrets_are_used(workflow):
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in text
    assert "${{secrets." not in text


def test_every_job_has_a_timeout(workflow):
    for name, job in workflow["jobs"].items():
        if name != "ci-pass":
            assert job.get("timeout-minutes"), name


def test_actions_are_pinned_to_a_version(workflow):
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            if "uses" in step:
                assert re.match(r"^[\w.-]+/[\w.-]+@v\d+", step["uses"]), (name, step["uses"])


def test_lint_job_checks_lint_and_formatting(workflow):
    commands = run_commands(workflow["jobs"]["lint"])
    assert "ruff check ." in commands
    assert "ruff format --check ." in commands


def test_test_job_runs_coverage_on_the_supported_python_versions(workflow):
    job = workflow["jobs"]["test"]
    assert "pytest --cov" in run_commands(job)
    assert "3.10" in job["strategy"]["matrix"]["python-version"]
    assert "3.12" in job["strategy"]["matrix"]["python-version"]


def test_ci_covers_the_minimum_python_version_the_code_targets():
    target = re.search(r'target-version\s*=\s*"py(\d)(\d+)"', (ROOT / "pyproject.toml").read_text())
    minimum = f"{target.group(1)}.{target.group(2)}"
    matrix = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]["test"]["strategy"][
        "matrix"
    ]
    assert minimum in matrix["python-version"]


def test_a_coverage_floor_is_enforced():
    match = re.search(
        r"^fail_under\s*=\s*(\d+)", (ROOT / "pyproject.toml").read_text(), re.MULTILINE
    )
    assert match and int(match.group(1)) >= 80


def test_e2e_job_installs_the_browser_and_runs_marked_tests(workflow):
    commands = run_commands(workflow["jobs"]["e2e"])
    assert "playwright install" in commands
    assert "pytest -m e2e" in commands


def test_build_job_builds_and_smoke_tests_the_production_image(workflow):
    job = workflow["jobs"]["build"]
    build_step = next(
        s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action")
    )
    assert build_step["with"]["target"] == "production"
    commands = run_commands(job)
    for expected in (
        "flask --app run db upgrade",
        "gunicorn",
        "/health",
        "/api/v1/urls",
        "whoami",
        "SECRET_KEY=short",
    ):
        assert expected in commands


def test_audit_job_does_not_block_merges(workflow):
    assert workflow["jobs"]["audit"]["continue-on-error"] is True
    assert "audit" not in workflow["jobs"]["ci-pass"]["needs"]


def test_gate_job_requires_every_blocking_job_and_always_runs(workflow):
    gate = workflow["jobs"]["ci-pass"]
    assert sorted(gate["needs"]) == sorted(BLOCKING_JOBS)
    assert gate["if"] == "always()"


def test_ci_uses_the_same_tools_as_the_documented_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in ("ruff check .", "ruff format .", "pytest -m e2e"):
        assert command in readme


def test_build_job_runs_the_production_preflight(workflow):
    assert "app.preflight --strict" in run_commands(workflow["jobs"]["build"])


def test_build_job_runs_the_smoke_test_script(workflow):
    assert "scripts/smoke_test.py" in run_commands(workflow["jobs"]["build"])
