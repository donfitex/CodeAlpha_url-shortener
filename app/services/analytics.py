from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select, update

from app.extensions import db
from app.models import URL


@dataclass(frozen=True)
class ClickEvent:
    """Deliberately contains no visitor information."""

    short_code: str
    occurred_at: datetime


_listeners: list[Callable[[ClickEvent], None]] = []


def register_click_listener(listener: Callable[[ClickEvent], None]) -> None:
    _listeners.append(listener)


def record_click(url: URL) -> None:
    """Atomically increment the click count without touching updated_at."""
    db.session.execute(
        update(URL)
        .where(URL.id == url.id)
        .values(click_count=URL.click_count + 1, updated_at=URL.updated_at)
    )
    db.session.commit()

    event = ClickEvent(short_code=url.short_code, occurred_at=datetime.now(timezone.utc))
    for listener in _listeners:
        try:
            listener(event)
        except Exception:
            current_app.logger.exception("Click listener failed")


def get_click_stats(short_code: str) -> dict | None:
    url = db.session.scalar(select(URL).where(URL.short_code == short_code))
    if url is None:
        return None
    return {
        "short_code": url.short_code,
        "click_count": url.click_count,
        "created_at": url.created_at,
        "expires_at": url.expires_at,
        "is_expired": url.is_expired,
    }