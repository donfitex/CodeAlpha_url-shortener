# Testing guide

Tests use pytest, pytest-flask, Coverage.py and Playwright. Every test gets a fresh in-memory SQLite
database from the `testing` config, so tests never touch real data and never depend on each other.

## Running tests

```powershell
pytest                                          # fast suite (browser tests are skipped)
pytest -m e2e                                   # browser tests only
pytest --cov --cov-report=term-missing          # with coverage
pytest --cov --cov-report=html                  # browsable report in htmlcov/
pytest tests/test_api.py -v                     # one file
pytest -k redirect                              # by name
```

One-time setup for browser tests: `playwright install chromium` (about 150 MB).

## Layout

| File | Covers |
|---|---|
| `tests/conftest.py` | Shared fixtures: `app`, `client` (from pytest-flask) and `make_url` |
| `test_validators.py` | URL validation: schemes, hosts, length, credentials, private addresses |
| `test_models.py` | Defaults, uniqueness, constraints, timestamps, expiry logic |
| `test_services.py` | Short-code generation, collisions, creation, lookup, click counting |
| `test_api.py` | `POST /api/v1/urls`: statuses, response shape, errors, no leaks |
| `test_routes.py` | `GET /<short_code>`: redirects, 404, 410, safe redirects, counting |
| `test_security.py` | Headers, CSP, production config, rate limits, blocklist, static safety checks |
| `test_wsgi.py` | `wsgi.py`, `run.py`, Gunicorn settings, Dockerfile |
| `test_health.py` | Liveness and readiness endpoints |
| `test_openapi.py` | The spec is valid, matches the routes, and real responses match it |
| `test_docs.py` | Documentation files, links and environment variables stay in sync |
| `test_e2e.py` | Real browser flows against a live server |

## Browser tests

Playwright drives Chromium against LinkForge running on a free local port with its own temporary
database. LinkForge rejects `localhost` targets, so tests shorten `https://example.com/...` and
Playwright serves a stub page for that domain. They run offline.

```powershell
pytest -m e2e --headed --slowmo 400                          # watch the browser
pytest -m e2e --screenshot only-on-failure --tracing retain-on-failure
```

Failure artifacts land in `test-results/`. Open a trace with
`playwright show-trace test-results/<folder>/trace.zip`.

## Writing tests

- Add or update tests in the same commit as the behavior change.
- Use `make_url` to insert rows directly, including deliberately invalid ones.
- Simulate collisions and database failures with `unittest.mock.patch`.
- Assert that error responses never contain secrets, SQL or tracebacks.
- Check that a new test can fail: break the code on purpose, watch it fail, then restore it.

## Coverage

Coverage is measured for `app` and `config` with branch tracking. Read the report for untested
branches, not just the percentage. Routes, services, validators and models should stay near full
coverage.

## Continuous integration

The GitHub Actions workflow runs Ruff, the test suite and coverage on every push and pull request.
Browser tests are opt-in locally and can be added to CI as a separate job.
