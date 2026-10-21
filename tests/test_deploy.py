import re
from pathlib import Path

import pytest
import yaml

from app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def blueprint():
    return yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def service(blueprint):
    assert len(blueprint["services"]) == 1
    return blueprint["services"][0]


@pytest.fixture(scope="module")
def database(blueprint):
    assert len(blueprint["databases"]) == 1
    return blueprint["databases"][0]


@pytest.fixture(scope="module")
def env(service):
    return {item["key"]: item for item in service["envVars"]}


def test_service_builds_the_repository_dockerfile(service):
    assert service["type"] == "web"
    assert service["runtime"] == "docker"
    assert (ROOT / service["dockerfilePath"]).is_file()


def test_production_is_the_final_dockerfile_stage():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages = re.findall(r"^FROM\s+\S+\s+AS\s+(\w+)", text, re.MULTILINE | re.IGNORECASE)
    assert stages[-1] == "production"  # a host that ignores build targets builds the last stage


def test_start_command_migrates_then_serves_with_gunicorn(service):
    command = service["dockerCommand"]
    assert "flask --app run db upgrade" in command
    assert command.index("db upgrade") < command.index("gunicorn")
    assert "exec gunicorn" in command
    assert "gunicorn.conf.py" in command
    assert "wsgi:app" in command
    assert "run.py" not in command


def test_health_check_path_is_a_real_healthy_endpoint(service):
    path = service["healthCheckPath"]
    assert path == "/health"
    assert create_app("testing").test_client().get(path).status_code == 200


def test_secret_key_is_generated_and_never_committed(env):
    assert env["SECRET_KEY"] == {"key": "SECRET_KEY", "generateValue": True}


def test_database_url_comes_from_the_managed_database(env, database):
    reference = env["DATABASE_URL"]["fromDatabase"]
    assert reference["name"] == database["name"]
    assert reference["property"] == "connectionString"
    assert "value" not in env["DATABASE_URL"]


def test_base_url_is_entered_by_the_operator_not_committed(env):
    assert env["BASE_URL"].get("sync") is False
    assert "value" not in env["BASE_URL"]


def test_proxy_and_worker_settings_match_the_hosting_setup(env):
    assert env["PROXY_COUNT"]["value"] == "1"  # Render puts one proxy in front
    # One worker keeps in-memory rate limits accurate (the preflight warns otherwise)
    assert env["WEB_CONCURRENCY"]["value"] == "1"


def test_no_secrets_appear_in_the_blueprint(blueprint):
    text = yaml.safe_dump(blueprint).lower()
    assert "postgres://" not in text
    assert "postgresql://" not in text
    assert "password" not in text


def test_database_is_private_and_in_the_service_region(service, database):
    assert database["ipAllowList"] == []
    assert database["region"] == service["region"]


def test_every_configured_variable_is_documented(env):
    docs = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert [key for key in env if f"`{key}`" not in docs] == []


def test_deployment_guide_covers_the_render_workflow():
    docs = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    for topic in (
        "render.yaml",
        "smoke_test.py",
        "BASE_URL",
        "30 days",
        "spins down",
        "preDeployCommand",
    ):
        assert topic in docs
