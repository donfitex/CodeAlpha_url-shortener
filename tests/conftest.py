import pytest

from app import create_app
from app.extensions import db
from app.models import URL


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def make_url(app):
    """Insert a URL row directly, bypassing the service layer."""

    def _make(code="abc123", url="https://example.com", **kwargs):
        row = URL(short_code=code, original_url=url, **kwargs)
        db.session.add(row)
        db.session.commit()
        return row

    return _make