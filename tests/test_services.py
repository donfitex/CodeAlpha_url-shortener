from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import URL
from app.services import (
    ClickEvent,
    ShortCodeExpired,
    ShortCodeGenerationError,
    ShortCodeNotFound,
    create_short_url,
    generate_short_code,
    get_click_stats,
    record_click,
    register_click_listener,
    resolve_short_code,
)
from app.services.url_shortener import ALPHABET, MAX_ATTEMPTS
from app.utils import URLValidationError


@pytest.fixture(autouse=True)
def isolated_listeners(monkeypatch):
    monkeypatch.setattr("app.services.analytics._listeners", [])


def _past(days=1):
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------- short-code generation ----------


@pytest.mark.parametrize("length", [4, 6, 12, 32])
def test_generated_code_has_requested_length(length):
    assert len(generate_short_code(length)) == length


def test_generated_code_uses_only_allowed_characters():
    code = generate_short_code(32)
    assert set(code) <= set(ALPHABET)


def test_alphabet_excludes_lookalike_characters():
    assert not set("0O1lI") & set(ALPHABET)


@pytest.mark.parametrize("length", [-1, 0, 3, 33])
def test_invalid_length_is_rejected(length):
    with pytest.raises(ValueError):
        generate_short_code(length)


def test_generated_codes_are_unique():
    codes = {generate_short_code(12) for _ in range(1000)}
    assert len(codes) == 1000


# ---------- URL creation ----------


def test_create_persists_the_url(app):
    created = create_short_url("https://example.com/page")
    stored = db.session.get(URL, created.id)
    assert stored.original_url == "https://example.com/page"
    assert stored.short_code == created.short_code
    assert stored.click_count == 0


def test_create_uses_configured_length(app):
    assert (
        len(create_short_url("https://example.com").short_code) == app.config["SHORT_CODE_LENGTH"]
    )
    app.config["SHORT_CODE_LENGTH"] = 8
    assert len(create_short_url("https://example.com").short_code) == 8


def test_create_accepts_custom_length(app):
    assert len(create_short_url("https://example.com", length=10).short_code) == 10


def test_create_strips_whitespace(app):
    assert create_short_url("  https://example.com/x  ").original_url == "https://example.com/x"


def test_create_stores_expiry(app):
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    url = create_short_url("https://example.com", expires_at=expires)
    assert url.expires_at is not None
    assert url.is_expired is False


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "", "not a url", None])
def test_create_rejects_invalid_url_and_stores_nothing(app, bad):
    with pytest.raises(URLValidationError):
        create_short_url(bad)
    assert URL.query.count() == 0


def test_create_honours_configured_max_url_length(app):
    app.config["MAX_URL_LENGTH"] = 30
    with pytest.raises(URLValidationError, match="at most 30"):
        create_short_url("https://example.com/" + "a" * 20)


def test_collision_is_retried(app, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code") as gen:
        gen.side_effect = [existing, existing, "Fresh77"]
        url = create_short_url("https://example.com/new")
    assert url.short_code == "Fresh77"
    assert gen.call_count == 3
    assert URL.query.count() == 2


def test_exhausted_retries_raise_and_session_stays_usable(app, make_url):
    existing = make_url(code="taken1").short_code
    with patch("app.services.url_shortener.generate_short_code", return_value=existing) as gen:
        with pytest.raises(ShortCodeGenerationError):
            create_short_url("https://example.com/doomed")
    assert gen.call_count == MAX_ATTEMPTS
    assert URL.query.count() == 1
    assert create_short_url("https://example.com/ok").id is not None


def test_database_error_propagates_and_rolls_back(app):
    def boom():
        raise OperationalError("INSERT", {}, Exception("db down"))

    with patch.object(db.session, "commit", side_effect=boom):
        with pytest.raises(OperationalError):
            create_short_url("https://example.com")
    assert URL.query.count() == 0
    assert create_short_url("https://example.com").id is not None


# ---------- lookup and expiration ----------


def test_resolve_returns_existing_url(make_url):
    make_url(code="found1")
    assert resolve_short_code("found1").short_code == "found1"


def test_resolve_unknown_code(app):
    with pytest.raises(ShortCodeNotFound):
        resolve_short_code("zzzzzz")


@pytest.mark.parametrize("code", ["", "ab", "has space", "a/b/c", "x" * 33, "bad-code", "ünï123"])
def test_resolve_rejects_malformed_codes(app, code):
    with pytest.raises(ShortCodeNotFound):
        resolve_short_code(code)


def test_resolve_expired_link(make_url):
    make_url(code="old123", expires_at=_past())
    with pytest.raises(ShortCodeExpired):
        resolve_short_code("old123")


def test_resolve_link_expiring_in_future(make_url):
    make_url(code="soon12", expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    assert resolve_short_code("soon12").short_code == "soon12"


# ---------- click counting ----------


def test_record_click_increments(make_url):
    url = make_url()
    record_click(url)
    db.session.refresh(url)
    assert url.click_count == 1
    for _ in range(4):
        record_click(url)
    db.session.refresh(url)
    assert url.click_count == 5


def test_record_click_does_not_touch_updated_at(make_url):
    url = make_url(updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    record_click(url)
    db.session.refresh(url)
    assert url.updated_at.replace(tzinfo=None) == datetime(2020, 1, 1)


def test_click_stats_contain_no_visitor_data(make_url):
    url = make_url(code="stats1")
    record_click(url)
    stats = get_click_stats("stats1")
    assert stats["click_count"] == 1
    assert sorted(stats) == ["click_count", "created_at", "expires_at", "is_expired", "short_code"]


def test_click_stats_for_unknown_code(app):
    assert get_click_stats("nope1234") is None


def test_listener_receives_event_without_visitor_data(make_url):
    events = []
    register_click_listener(events.append)
    record_click(make_url(code="event1"))
    assert len(events) == 1
    assert isinstance(events[0], ClickEvent)
    assert events[0].short_code == "event1"
    assert sorted(vars(events[0])) == ["occurred_at", "short_code"]


def test_failing_listener_does_not_break_counting(make_url):
    received = []
    register_click_listener(lambda event: 1 / 0)
    register_click_listener(received.append)
    url = make_url()
    record_click(url)
    db.session.refresh(url)
    assert url.click_count == 1
    assert len(received) == 1
