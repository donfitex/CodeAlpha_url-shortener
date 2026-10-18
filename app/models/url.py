from datetime import datetime, timezone

from sqlalchemy import CheckConstraint

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class URL(db.Model):
    __tablename__ = "urls"
    __table_args__ = (
        CheckConstraint("click_count >= 0", name="ck_urls_click_count_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # unique=True + index=True creates one unique index for fast lookups
    short_code = db.Column(db.String(32), unique=True, index=True, nullable=False)
    original_url = db.Column(db.String(2048), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    click_count = db.Column(db.Integer, default=0, server_default="0", nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = self.expires_at
        # SQLite returns naive datetimes; treat them as UTC
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= utcnow()

    def __repr__(self) -> str:
        return f"<URL {self.short_code} -> {self.original_url[:40]}>"