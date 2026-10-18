from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import URL


def _utc(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def test_defaults_are_applied(make_url):
    url = make_url()
    assert url.id is not None
    assert url.click_count == 0
    assert url.created_at is not None
    assert url.updated_at is not None
    assert url.expires_at is None


def test_short_code_must_be_unique(make_url):
    make_url(code="dupe99")
    db.session.add(URL(short_code="dupe99", original_url="https://other.com"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_original_url_is_required(app):
    db.session.add(URL(short_code="nourl1", original_url=None))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_negative_click_count_is_rejected(app):
    db.session.add(URL(short_code="neg001", original_url="https://x.com", click_count=-1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_updated_at_changes_on_update(make_url):
    url = make_url(updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    url.original_url = "https://example.com/changed"
    db.session.commit()
    db.session.refresh(url)
    assert url.updated_at.replace(tzinfo=None) > datetime(2020, 1, 1)


@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [
        (None, False),
        (_utc(days=1), False),
        (_utc(days=-1), True),
        (_utc(seconds=-1), True),
        # SQLite returns naive datetimes; they must be treated as UTC
        (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1), True),
        (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1), False),
    ],
)
def test_is_expired(expires_at, expected):
    assert URL(short_code="x", original_url="https://x.com", expires_at=expires_at).is_expired is expected


def test_repr_contains_code(make_url):
    assert "abc123" in repr(make_url())