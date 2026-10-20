import re
from datetime import datetime, timedelta, timezone
from threading import Thread
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import URL

pytestmark = pytest.mark.e2e

DESTINATION = "https://example.com/destination"


# ---------- fixtures ----------


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """Run LinkForge on a free port with a throwaway SQLite file."""
    db_file = tmp_path_factory.mktemp("e2e") / "e2e.db"
    flask_app = create_app(
        "testing",
        config_overrides={"SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file.as_posix()}"},
    )
    with flask_app.app_context():
        db.create_all()

    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    base_url = f"http://127.0.0.1:{server.server_port}"
    flask_app.config["BASE_URL"] = base_url

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(app=flask_app, url=base_url)

    server.shutdown()
    thread.join(timeout=5)
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "permissions": ["clipboard-read", "clipboard-write"]}


@pytest.fixture(autouse=True)
def stub_external_site(context):
    """Serve a fake page for example.com so tests work offline."""
    context.route(
        "https://example.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><body><h1>Destination reached</h1></body></html>",
        ),
    )


@pytest.fixture
def home(page, live):
    page.goto(live.url)
    return page


def click_count(live, code):
    with live.app.app_context():
        return URL.query.filter_by(short_code=code).one().click_count


# ---------- the complete workflow ----------


def test_complete_shortening_workflow(home, live, context):
    page = home

    # Open homepage: empty state, no result yet
    expect(page.get_by_role("heading", name="Shorten your links.")).to_be_visible()
    expect(page.locator("#empty-state")).to_be_visible()
    expect(page.locator("#result-card")).to_be_hidden()

    # Slow the API a little so the loading state can be observed
    def slow_api(route):
        page.wait_for_timeout(600)
        route.continue_()

    page.route("**/api/v1/urls", slow_api)

    # Enter URL and submit
    page.locator("#url-input").fill(DESTINATION)
    page.locator("#submit-btn").click()

    # Loading state
    expect(page.locator("#submit-btn")).to_be_disabled()
    expect(page.locator("#submit-btn")).to_contain_text("Shortening")
    expect(page.locator("#submit-spinner")).to_be_visible()

    # Short URL returned
    card = page.locator("#result-card")
    expect(card).to_be_visible()
    expect(page.locator("#empty-state")).to_be_hidden()
    expect(page.locator("#submit-btn")).to_be_enabled()
    expect(page.locator("#submit-btn")).to_contain_text("Shorten")
    expect(page.locator("#result-original")).to_have_text(DESTINATION)

    link = page.locator("#result-link")
    expect(link).to_have_text(re.compile(rf"^{re.escape(live.url)}/[A-Za-z0-9]{{6}}$"))
    short_url = link.inner_text()
    code = short_url.rsplit("/", 1)[1]
    assert click_count(live, code) == 0

    # Copy URL
    page.locator("#copy-btn").click()
    expect(page.locator("#copy-btn")).to_have_text("Copied!")
    expect(page.locator("#copy-status")).to_contain_text("copied")
    assert page.evaluate("navigator.clipboard.readText()") == short_url

    # Open short URL: it redirects to the original destination
    with context.expect_page() as popup_info:
        link.click()
    popup = popup_info.value
    popup.wait_for_url(DESTINATION)
    expect(popup.get_by_role("heading", name="Destination reached")).to_be_visible()

    assert click_count(live, code) == 1


def test_enter_key_submits_the_form(home):
    home.locator("#url-input").fill(DESTINATION)
    home.locator("#url-input").press("Enter")
    expect(home.locator("#result-card")).to_be_visible()


def test_second_submission_replaces_the_result(home):
    page = home
    page.locator("#url-input").fill("https://example.com/first")
    page.locator("#submit-btn").click()
    expect(page.locator("#result-original")).to_have_text("https://example.com/first")
    first = page.locator("#result-link").inner_text()

    page.locator("#url-input").fill("https://example.com/second")
    page.locator("#submit-btn").click()
    expect(page.locator("#result-original")).to_have_text("https://example.com/second")
    assert page.locator("#result-link").inner_text() != first


# ---------- validation and error states ----------


def test_empty_submission_shows_message_without_calling_the_api(home):
    requests = []
    home.on("request", lambda r: requests.append(r.url) if "/api/v1/urls" in r.url else None)

    home.locator("#submit-btn").click()

    expect(home.locator("#form-error")).to_have_text("Please enter a URL to shorten.")
    expect(home.locator("#url-input")).to_have_attribute("aria-invalid", "true")
    expect(home.locator("#url-input")).to_be_focused()
    expect(home.locator("#result-card")).to_be_hidden()
    assert requests == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("javascript:alert(1)", "scheme is not allowed"),
        ("ftp://example.com/file", "Only http:// and https://"),
        ("http://localhost/admin", "Private or local addresses"),
        ("http://", "valid host"),
    ],
)
def test_server_validation_message_is_shown(home, value, message):
    home.locator("#url-input").fill(value)
    home.locator("#submit-btn").click()

    expect(home.locator("#form-error")).to_contain_text(message)
    expect(home.locator("#url-input")).to_have_attribute("aria-invalid", "true")
    expect(home.locator("#result-card")).to_be_hidden()
    expect(home.locator("#submit-btn")).to_be_enabled()


def test_error_clears_when_the_user_types(home):
    home.locator("#url-input").fill("http://")
    home.locator("#submit-btn").click()
    expect(home.locator("#form-error")).to_be_visible()

    home.locator("#url-input").press_sequentially("x")

    expect(home.locator("#form-error")).to_be_hidden()
    expect(home.locator("#url-input")).not_to_have_attribute("aria-invalid", "true")


def test_error_replaces_a_previous_result(home):
    home.locator("#url-input").fill(DESTINATION)
    home.locator("#submit-btn").click()
    expect(home.locator("#result-card")).to_be_visible()

    home.locator("#url-input").fill("javascript:alert(1)")
    home.locator("#submit-btn").click()

    expect(home.locator("#form-error")).to_be_visible()
    expect(home.locator("#result-card")).to_be_hidden()
    expect(home.locator("#empty-state")).to_be_visible()


def test_server_error_shows_friendly_message(home):
    home.route(
        "**/api/v1/urls",
        lambda route: route.fulfill(
            status=500,
            json={"error": {"code": "internal_error", "message": "Something went wrong."}},
        ),
    )
    home.locator("#url-input").fill(DESTINATION)
    home.locator("#submit-btn").click()

    expect(home.locator("#form-error")).to_contain_text("Something went wrong on our side")
    expect(home.locator("#result-card")).to_be_hidden()
    expect(home.locator("#submit-btn")).to_be_enabled()


def test_network_failure_shows_connection_message(home):
    home.route("**/api/v1/urls", lambda route: route.abort("connectionrefused"))
    home.locator("#url-input").fill(DESTINATION)
    home.locator("#submit-btn").click()

    expect(home.locator("#form-error")).to_contain_text("reach the server")
    expect(home.locator("#submit-btn")).to_be_enabled()


# ---------- redirect edge cases in a real browser ----------


def test_expired_link_shows_gone_page(page, live):
    with live.app.app_context():
        db.session.add(
            URL(
                short_code="old123",
                original_url=DESTINATION,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        db.session.commit()

    response = page.goto(f"{live.url}/old123")

    assert response.status == 410
    expect(page.get_by_role("heading", name="Link expired")).to_be_visible()
    assert click_count(live, "old123") == 0


def test_unknown_link_shows_not_found_page(page, live):
    response = page.goto(f"{live.url}/zzzzzz")

    assert response.status == 404
    expect(page.get_by_role("heading", name="This link doesn't exist")).to_be_visible()
    page.get_by_role("link", name="Back to home").click()
    expect(page.get_by_role("heading", name="Shorten your links.")).to_be_visible()


def test_homepage_is_keyboard_accessible(home):
    home.keyboard.press("Tab")
    expect(home.get_by_role("link", name="Skip to main content")).to_be_focused()


def test_strict_csp_does_not_break_the_page(home, live):
    messages = []
    home.on("console", lambda m: messages.append(m.text))
    home.on("pageerror", lambda e: messages.append(str(e)))

    response = home.goto(live.url)
    assert "default-src 'self'" in response.headers["content-security-policy"]

    home.locator("#url-input").fill(DESTINATION)
    home.locator("#submit-btn").click()
    expect(home.locator("#result-card")).to_be_visible()
    home.locator("#copy-btn").click()
    expect(home.locator("#copy-btn")).to_have_text("Copied!")

    violations = [m for m in messages if "Content Security Policy" in m or "Refused to" in m]
    assert violations == []


def test_api_docs_page_loads_under_strict_csp(page, live):
    messages = []
    page.on("console", lambda m: messages.append(m.text))

    response = page.goto(f"{live.url}/api/v1/docs")

    assert response.status == 200
    expect(page.get_by_role("heading", name="LinkForge API")).to_be_visible()
    expect(page.get_by_role("heading", name=re.compile("/api/v1/urls"))).to_be_visible()
    assert [m for m in messages if "Content Security Policy" in m] == []
