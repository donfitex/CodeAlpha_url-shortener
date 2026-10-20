import re
import secrets
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.extensions import db
from app.models import URL
from app.utils import validate_url
from urllib.parse import urlsplit

# No look-alike characters: 0/O, 1/l/I
ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
MIN_CODE_LENGTH = 4
MAX_CODE_LENGTH = 32  # matches the short_code column size
MAX_ATTEMPTS = 10


class ShortCodeGenerationError(RuntimeError):
    """Raised when a unique short code could not be created."""


def generate_short_code(length: int) -> str:
    if not MIN_CODE_LENGTH <= length <= MAX_CODE_LENGTH:
        raise ValueError(
            f"Short code length must be between {MIN_CODE_LENGTH} and {MAX_CODE_LENGTH}."
        )
    return "".join(secrets.choice(ALPHABET) for _ in range(length))

def blocked_domains() -> tuple[str, ...]:
    """Configured blocklist plus this service's own host."""
    configured = tuple(current_app.config.get("BLOCKED_DOMAINS", ()))
    own_host = urlsplit(current_app.config["BASE_URL"]).hostname
    return configured + ((own_host,) if own_host else ())

def create_short_url(
    original_url: str,
    expires_at: datetime | None = None,
    length: int | None = None,
) -> URL:
    """Validate the URL, then insert it with a unique short code.

    Raises URLValidationError for bad input and ShortCodeGenerationError
    if every attempt collided.
    """
    cleaned = validate_url(
        original_url,
        max_length=current_app.config["MAX_URL_LENGTH"],
        blocked_domains=blocked_domains(),
    )
    length = length or current_app.config["SHORT_CODE_LENGTH"]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        url = URL(
            short_code=generate_short_code(length),
            original_url=cleaned,
            expires_at=expires_at,
        )
        db.session.add(url)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            current_app.logger.warning(
                "Short code collision (attempt %d/%d)", attempt, MAX_ATTEMPTS
            )
            continue
        except Exception:
            db.session.rollback()
            raise
        return url

    current_app.logger.error("Could not generate a unique short code")
    raise ShortCodeGenerationError("Could not generate a unique short code.")

SHORT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{4,32}$")


class ShortCodeNotFound(LookupError):
    """No link exists for this short code."""


class ShortCodeExpired(LookupError):
    """The link exists but has expired."""


def resolve_short_code(short_code: str) -> URL:
    if not SHORT_CODE_PATTERN.match(short_code):
        raise ShortCodeNotFound(short_code)

    url = db.session.scalar(select(URL).where(URL.short_code == short_code))
    if url is None:
        raise ShortCodeNotFound(short_code)
    if url.is_expired:
        raise ShortCodeExpired(short_code)
    return url

