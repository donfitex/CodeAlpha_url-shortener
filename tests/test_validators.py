import pytest

from app.utils import URLValidationError, validate_url


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.com",
        "https://example.com",
        "HTTPS://Example.com/Path",
        "https://example.com/path/to/page?q=1&x=2#section",
        "https://sub.domain.example.co.uk:8443/a/b",
        "https://8.8.8.8/dns",
        "https://bücher.de",
    ],
)
def test_valid_urls_are_accepted(raw):
    assert validate_url(raw) == raw


def test_surrounding_whitespace_is_stripped():
    assert validate_url("  https://example.com/x \n") == "https://example.com/x"


@pytest.mark.parametrize("raw", [None, "", "   ", 123, [], {}])
def test_missing_or_non_string_url_is_rejected(raw):
    with pytest.raises(URLValidationError, match="required"):
        validate_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "not a url",
        "example.com",
        "//example.com/path",
        "http://",
        "https:///path",
        "https://exa mple.com",
        "https://exa\tmple.com",
        "https://example.com/\x00",
        "https://example.com:99999",
        "https://example.com:abc",
    ],
)
def test_malformed_urls_are_rejected(raw):
    with pytest.raises(URLValidationError):
        validate_url(raw)


@pytest.mark.parametrize(
    "raw",
    ["ftp://example.com", "mailto:someone@example.com", "ws://example.com", "gopher://example.com"],
)
def test_unsupported_schemes_are_rejected(raw):
    with pytest.raises(URLValidationError, match="Only http"):
        validate_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "blob:https://example.com/some-id",
    ],
)
def test_dangerous_schemes_are_rejected(raw):
    with pytest.raises(URLValidationError, match="not allowed"):
        validate_url(raw)


def test_excessive_length_is_rejected():
    with pytest.raises(URLValidationError, match="at most 2048"):
        validate_url("https://example.com/" + "a" * 3000)


def test_length_limit_boundary():
    prefix = "https://example.com/"
    at_limit = prefix + "a" * (2048 - len(prefix))
    assert validate_url(at_limit) == at_limit
    with pytest.raises(URLValidationError):
        validate_url(at_limit + "a")


def test_custom_max_length():
    with pytest.raises(URLValidationError, match="at most 30"):
        validate_url("https://example.com/" + "a" * 20, max_length=30)


@pytest.mark.parametrize(
    "raw",
    [
        "https://localhost/admin",
        "http://app.localhost",
        "http://127.0.0.1",
        "http://0.0.0.0",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
    ],
)
def test_private_and_local_hosts_are_rejected(raw):
    with pytest.raises(URLValidationError, match="Private or local"):
        validate_url(raw)


@pytest.mark.parametrize("raw", ["https://user:pass@example.com", "https://user@example.com"])
def test_embedded_credentials_are_rejected(raw):
    with pytest.raises(URLValidationError, match="credentials"):
        validate_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://-bad-.com",
        "https://singlelabel",
        "https://exa_mple.com",
        "https://example..com",
        "https://" + "a" * 64 + ".com",
    ],
)
def test_invalid_hosts_are_rejected(raw):
    with pytest.raises(URLValidationError, match="invalid host"):
        validate_url(raw)


def test_port_zero_is_rejected():
    with pytest.raises(URLValidationError, match="port"):
        validate_url("https://example.com:0")