import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import URL


def utc_in(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def clicks(row):
    db.session.refresh(row)
    return row.click_count


def assert_json_error(response, status, code):
    assert response.status_code == status
    assert response.is_json
    assert response.get_json()["error"]["code"] == code


# ---------- successful redirects ----------

def test_existing_code_redirects_to_original_url(client, make_url):
    make_url(code="found1", url="https://example.com/destination")

    response = client.get("/found1")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/destination"


def test_redirect_is_temporary_and_not_cacheable(client, make_url):
    make_url(code="found1")

    response = client.get("/found1")

    assert response.status_code == 302  # 301 would be cached and skip click counting
    assert response.headers["Cache-Control"] == "no-store"


def test_query_string_and_fragment_are_preserved(client, make_url):
    target = "https://example.com/search?q=flask&page=2#results"
    make_url(code="query1", url=target)

    assert client.get("/query1").headers["Location"] == target


def test_each_code_redirects_to_its_own_destination(client, make_url):
    make_url(code="first1", url="https://example.com/one")
    make_url(code="second", url="https://example.com/two")

    assert client.get("/first1").headers["Location"] == "https://example.com/one"
    assert client.get("/second").headers["Location"] == "https://example.com/two"


def test_codes_are_case_sensitive(client, make_url):
    make_url(code="AbC123", url="https://example.com/case")

    assert client.get("/AbC123").status_code == 302
    assert client.get("/abc123").status_code == 404
    assert client.get("/ABC123").status_code == 404


def test_link_expiring_in_the_future_still_works(client, make_url):
    make_url(code="later1", expires_at=utc_in(days=7))
    assert client.get("/later1").status_code == 302


def test_full_flow_create_via_api_then_follow_redirect(client):
    created = client.post("/api/v1/urls", json={"url": "https://example.com/e2e"}).get_json()

    response = client.get(f"/{created['short_code']}")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/e2e"
    assert clicks(URL.query.filter_by(short_code=created["short_code"]).one()) == 1


# ---------- click counting ----------

def test_click_count_increments_on_each_redirect(client, make_url):
    row = make_url(code="count1")
    assert clicks(row) == 0

    client.get("/count1")
    assert clicks(row) == 1

    for _ in range(4):
        client.get("/count1")
    assert clicks(row) == 5


def test_clicks_are_counted_per_link(client, make_url):
    first = make_url(code="first1")
    second = make_url(code="second")

    client.get("/first1")
    client.get("/first1")
    client.get("/second")

    assert clicks(first) == 2
    assert clicks(second) == 1


def test_head_requests_redirect_but_are_not_counted(client, make_url):
    row = make_url(code="head11", url="https://example.com/head")

    response = client.head("/head11")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/head"
    assert clicks(row) == 0


def test_click_recording_does_not_change_updated_at(client, make_url):
    row = make_url(code="stamp1", updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc))

    client.get("/stamp1")

    db.session.refresh(row)
    assert row.updated_at.replace(tzinfo=None) == datetime(2020, 1, 1)


def test_redirect_still_works_if_click_recording_fails(client, make_url, caplog):
    row = make_url(code="resil1", url="https://example.com/resilient")

    with caplog.at_level(logging.ERROR), patch(
        "app.routes.main.record_click", side_effect=RuntimeError("counter down")
    ):
        response = client.get("/resil1")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/resilient"
    assert "Failed to record click" in caplog.text
    assert clicks(row) == 0


# ---------- missing and malformed codes (404) ----------

def test_unknown_code_returns_404_page(client):
    response = client.get("/zzzzzz")

    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert "This link doesn" in response.get_data(as_text=True)
    assert "Location" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/ab",                 # too short
        "/abc",                # too short
        "/" + "x" * 33,        # too long
        "/bad-code",           # hyphen
        "/has.dot",
        "/favicon.ico",
        "/robots.txt",
        "/a%20b123",           # encoded space
        "/%C3%BCn%C3%AF123",   # non-ASCII
        "/a/b/c",              # multiple segments
    ],
)
def test_malformed_codes_return_404(client, path):
    assert client.get(path).status_code == 404


def test_malformed_codes_never_reach_the_database(client):
    with patch.object(db.session, "scalar", side_effect=AssertionError("database was queried")):
        assert client.get("/ab").status_code == 404
        assert client.get("/bad-code").status_code == 404


def test_trailing_slash_is_not_a_valid_short_link(client, make_url):
    make_url(code="slash1")
    assert client.get("/slash1/").status_code == 404


def test_missing_code_does_not_create_or_change_rows(client, make_url):
    row = make_url(code="exists")

    client.get("/nosuch1")

    assert URL.query.count() == 1
    assert clicks(row) == 0


def test_missing_code_returns_json_when_requested(client):
    response = client.get("/zzzzzz", headers={"Accept": "application/json"})
    assert_json_error(response, 404, "not_found")


# ---------- expired links (410) ----------

def test_expired_link_returns_410_page(client, make_url):
    make_url(code="old123", expires_at=utc_in(days=-1))

    response = client.get("/old123")

    assert response.status_code == 410
    assert response.mimetype == "text/html"
    assert "Link expired" in response.get_data(as_text=True)
    assert "Location" not in response.headers


def test_link_that_expired_one_second_ago_is_gone(client, make_url):
    make_url(code="edge11", expires_at=utc_in(seconds=-1))
    assert client.get("/edge11").status_code == 410


def test_expired_link_is_not_counted(client, make_url):
    row = make_url(code="old123", expires_at=utc_in(days=-1))

    client.get("/old123")
    client.head("/old123")

    assert clicks(row) == 0


def test_expired_link_returns_json_when_requested(client, make_url):
    make_url(code="old123", expires_at=utc_in(days=-1))
    response = client.get("/old123", headers={"Accept": "application/json"})
    assert_json_error(response, 410, "gone")


def test_expired_link_is_not_deleted(client, make_url):
    make_url(code="old123", expires_at=utc_in(days=-1))
    client.get("/old123")
    assert URL.query.filter_by(short_code="old123").count() == 1


# ---------- safe redirects ----------

@pytest.mark.parametrize(
    "tampered",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "https://user:pass@example.com",
    ],
)
def test_unsafe_stored_destination_is_never_served(client, make_url, caplog, tampered):
    row = make_url(code="evil99", url=tampered)  # bypasses validation on purpose

    with caplog.at_level(logging.ERROR):
        response = client.get("/evil99")

    assert response.status_code == 404
    assert "Location" not in response.headers
    assert "Blocked unsafe stored destination" in caplog.text
    assert clicks(row) == 0


def test_destination_is_revalidated_against_current_length_limit(client, make_url, app):
    make_url(code="long11", url="https://example.com/" + "a" * 100)
    app.config["MAX_URL_LENGTH"] = 30

    assert client.get("/long11").status_code == 404


# ---------- routing precedence and methods ----------

def test_home_page_is_not_treated_as_a_short_code(client):
    assert client.get("/").status_code == 200


def test_api_path_is_not_treated_as_a_short_code(client):
    assert_json_error(client.get("/api/v1/urls"), 405, "method_not_allowed")


def test_non_get_methods_on_short_code_return_405(client, make_url):
    row = make_url(code="found1")

    for method in ("post", "put", "delete"):
        response = getattr(client, method)("/found1")
        assert response.status_code == 405
        assert "GET" in response.headers["Allow"]

    assert clicks(row) == 0