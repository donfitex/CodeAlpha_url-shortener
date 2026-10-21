import importlib.util
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import URL

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "scripts" / "smoke_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_script()


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("smoke") / "smoke.db"
    flask_app = create_app(
        "testing",
        config_overrides={"SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file.as_posix()}"},
    )
    with flask_app.app_context():
        db.create_all()

    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}"
    flask_app.config["BASE_URL"] = url
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield SimpleNamespace(app=flask_app, url=url)

    server.shutdown()
    thread.join(timeout=5)
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


def row_count(app):
    with app.app_context():
        return URL.query.count()


def test_smoke_test_passes_against_a_healthy_deployment(live, capsys):
    assert smoke.main([live.url, "--wait", "5"]) == 0

    out = capsys.readouterr().out
    assert out.count("[PASS]") == len(smoke.CHECKS)
    assert "[FAIL]" not in out


def test_trailing_slash_in_the_address_is_accepted(live):
    assert smoke.main([live.url + "/", "--wait", "5"]) == 0


def test_smoke_test_creates_exactly_one_link(live):
    before = row_count(live.app)
    assert smoke.main([live.url, "--wait", "5"]) == 0
    assert row_count(live.app) == before + 1


def test_wrong_base_url_setting_is_detected(live, monkeypatch, capsys):
    monkeypatch.setitem(live.app.config, "BASE_URL", "https://wrong.example")

    assert smoke.main([live.url, "--wait", "5"]) == 1

    out = capsys.readouterr().out
    assert "[FAIL] create link" in out
    assert "BASE_URL" in out


def test_unreachable_server_fails(capsys):
    assert smoke.main(["http://127.0.0.1:9", "--wait", "1"]) == 1
    assert "[FAIL] startup" in capsys.readouterr().out


@pytest.mark.parametrize(
    "address", ["file:///etc/passwd", "ftp://example.com", "example.com", "http://"]
)
def test_non_http_addresses_are_refused(address, capsys):
    assert smoke.main([address]) == 2
    assert "http" in capsys.readouterr().err


def test_output_never_contains_secrets(live, capsys):
    smoke.main([live.url, "--wait", "5"])
    out = capsys.readouterr().out
    assert live.app.config["SECRET_KEY"] not in out
    assert "sqlite" not in out
