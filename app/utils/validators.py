import ipaddress
import re
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
DANGEROUS_SCHEMES = {"javascript", "data", "file", "vbscript", "blob"}

# Any whitespace or control character inside the URL
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


class URLValidationError(ValueError):
    """Raised when a submitted URL is invalid. The message is safe to show users."""


def validate_url(raw, max_length: int = 2048, blocked_domains=()) -> str:
    if raw is None or not isinstance(raw, str) or not raw.strip():
        raise URLValidationError("A URL is required.")

    url = raw.strip()

    if len(url) > max_length:
        raise URLValidationError(f"URL must be at most {max_length} characters.")

    if _CONTROL_OR_SPACE.search(url):
        raise URLValidationError("URL must not contain spaces or control characters.")

    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError if out of range or not numeric
    except ValueError:
        raise URLValidationError("URL is malformed.") from None

    scheme = parts.scheme.lower()
    if not scheme:
        raise URLValidationError("URL must start with http:// or https://.")
    if scheme in DANGEROUS_SCHEMES:
        raise URLValidationError(f"The '{scheme}:' scheme is not allowed.")
    if scheme not in ALLOWED_SCHEMES:
        raise URLValidationError("Only http:// and https:// URLs are supported.")

    if parts.username is not None or parts.password is not None:
        raise URLValidationError("URLs containing credentials are not allowed.")

    host = parts.hostname
    if not host:
        raise URLValidationError("URL must include a valid host.")
    _validate_host(host)

    if _is_blocked(host, blocked_domains):
        raise URLValidationError("Links to this domain are not allowed.")

    if port is not None and not (1 <= port <= 65535):
        raise URLValidationError("URL has an invalid port.")

    return url


def _validate_host(host: str) -> None:
    # IP address hosts
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise URLValidationError("Private or local addresses are not allowed.")
        return

    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise URLValidationError("Private or local addresses are not allowed.")

    # Domain names (internationalized names are converted to ASCII first)
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise URLValidationError("URL has an invalid host.") from None

    if len(ascii_host) > 253 or "." not in ascii_host:
        raise URLValidationError("URL has an invalid host.")

    labels = ascii_host.rstrip(".").split(".")
    if not all(_LABEL.match(label) for label in labels):
        raise URLValidationError("URL has an invalid host.")


def _is_blocked(host: str, blocked_domains) -> bool:
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    host = host.lower().rstrip(".")
    for blocked in blocked_domains:
        blocked = blocked.lower().strip(".")
        if blocked and (host == blocked or host.endswith("." + blocked)):
            return True
    return False
