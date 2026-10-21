"""Post-deploy smoke test for a running LinkForge instance.

Usage: python scripts/smoke_test.py https://your-domain.example [--wait 90]

Creates one real short link, so expect a single extra row afterwards.
Exit status: 0 all checks passed, 1 a check failed, 2 bad usage.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TARGET = "https://example.com/smoke-test"
SECURITY_HEADERS = ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options")


class SmokeFailure(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # report 3xx responses instead of following them


OPENER = urllib.request.build_opener(_NoRedirect)


def expect(condition, message):
    if not condition:
        raise SmokeFailure(message)


def fetch(method, url, body=None, headers=None, timeout=30):
    request_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def wait_until_up(base, wait):
    """Free hosting spins down when idle, so allow time for a cold start."""
    deadline = time.monotonic() + wait
    while True:
        try:
            status, _, _ = fetch("GET", f"{base}/health", timeout=10)
            if status == 200:
                return True
        except OSError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(3, remaining))


def check_health(ctx):
    status, _, body = fetch("GET", f"{ctx['base']}/health")
    expect(status == 200, f"returned {status}")
    expect(json.loads(body) == {"status": "ok"}, "unexpected body")
    return "liveness ok"


def check_readiness(ctx):
    status, _, body = fetch("GET", f"{ctx['base']}/health/ready")
    expect(status == 200, f"returned {status}; the database may be unreachable")
    expect(json.loads(body)["checks"]["database"] == "ok", "database not ok")
    return "database reachable"


def check_homepage(ctx):
    status, headers, body = fetch("GET", ctx["base"] + "/")
    expect(status == 200, f"returned {status}")
    expect("text/html" in headers.get("Content-Type", ""), "not an HTML page")
    expect(b"LinkForge" in body, "page content missing")
    ctx["homepage_headers"] = headers
    return "home page served"


def check_security_headers(ctx):
    headers = ctx["homepage_headers"]
    required = list(SECURITY_HEADERS)
    if ctx["base"].startswith("https://"):
        required.append("Strict-Transport-Security")
    missing = [name for name in required if name not in headers]
    expect(not missing, f"missing headers: {', '.join(missing)}")
    return f"{len(required)} headers present"


def check_create(ctx):
    base = ctx["base"]
    status, _, body = fetch("POST", f"{base}/api/v1/urls", {"url": TARGET})
    expect(status == 201, f"returned {status}")
    data = json.loads(body)
    expect(sorted(data) == ["original_url", "short_code", "short_url"], "unexpected fields")
    expect(
        data["short_url"] == f"{base}/{data['short_code']}",
        "short_url does not match this address; check the BASE_URL setting",
    )
    ctx["code"] = data["short_code"]
    return "short link created, BASE_URL matches"


def check_redirect(ctx):
    status, headers, _ = fetch("GET", f"{ctx['base']}/{ctx['code']}")
    expect(status == 302, f"returned {status}")
    expect(headers.get("Location") == TARGET, "wrong redirect destination")
    expect(headers.get("Cache-Control") == "no-store", "redirect may be cached")
    return "302 to the original URL"


def check_api_docs(ctx):
    status, _, body = fetch("GET", f"{ctx['base']}/api/v1/openapi.json")
    expect(status == 200, f"returned {status}")
    expect("openapi" in json.loads(body), "not an OpenAPI document")
    return "OpenAPI spec served"


def check_not_found(ctx):
    status, _, body = fetch("GET", f"{ctx['base']}/zzzzzz", headers={"Accept": "application/json"})
    expect(status == 404, f"returned {status}")
    expect(json.loads(body)["error"]["code"] == "not_found", "unexpected error body")
    return "unknown codes return 404"


CHECKS = (
    ("health", check_health),
    ("readiness", check_readiness),
    ("home page", check_homepage),
    ("security headers", check_security_headers),
    ("create link", check_create),
    ("redirect", check_redirect),
    ("api docs", check_api_docs),
    ("not found", check_not_found),
)


def run_checks(base):
    ctx = {"base": base}
    passed = True
    width = max(len(name) for name, _ in CHECKS)
    for name, check in CHECKS:
        try:
            detail, ok = check(ctx), True
        except SmokeFailure as failure:
            detail, ok = str(failure), False
        except (OSError, ValueError, KeyError) as error:
            detail, ok = f"{type(error).__name__}", False
        print(f"{'[PASS]' if ok else '[FAIL]'} {name.ljust(width)}  {detail}")
        passed &= ok
        if not ok and name in ("health", "create link"):
            break  # later checks depend on these
    return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description="LinkForge post-deploy smoke test")
    parser.add_argument("url", help="public address, e.g. https://lnk.example")
    parser.add_argument("--wait", type=int, default=90, help="seconds to wait for a cold start")
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        print("The address must start with http:// or https://", file=sys.stderr)
        return 2

    print(f"Smoke testing {base}")
    if not wait_until_up(base, args.wait):
        print(f"[FAIL] startup  no healthy response from {base}/health within {args.wait}s")
        return 1
    return 0 if run_checks(base) else 1


if __name__ == "__main__":
    sys.exit(main())
