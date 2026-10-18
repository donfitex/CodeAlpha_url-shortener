import secrets
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import URL
from app.utils import validate_url

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
        original_url, max_length=current_app.config["MAX_URL_LENGTH"]
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