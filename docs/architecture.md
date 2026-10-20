# Architecture

LinkForge is a Flask application built with the application factory pattern. Routes stay thin,
business rules live in a service layer, and validation is a standalone utility.

## Overview

```
Browser / API client
        |
        v
  Flask app  (created by create_app)
   |- security headers   (after_request)
   |- rate limiter       (Flask-Limiter)
   |- routes             main | api | health
   |      |
   |      v
   |- services           url_shortener | analytics
   |      |        |
   |      v        v
   |- validators   URL model
   |- error handlers
        |
        v
  SQLAlchemy --> SQLite (development, tests) / PostgreSQL (production)
```

Production adds a reverse proxy in front and Gunicorn as the WSGI server
(see [deployment](deployment.md)).

## Modules

| Path | Responsibility |
|---|---|
| `app/__init__.py` | `create_app()`: selects config, wires logging, proxy handling, extensions, blueprints, error handlers and security headers |
| `config/` | One class per environment (`development`, `testing`, `production`); production validates its secrets at startup |
| `app/extensions.py` | Shared `db`, `migrate` and `limiter` objects, kept separate to avoid circular imports |
| `app/models/url.py` | The `URL` model |
| `app/services/url_shortener.py` | Short-code generation, URL creation, lookup and expiry checks |
| `app/services/analytics.py` | Atomic click counting, click statistics, listener hook |
| `app/utils/validators.py` | `validate_url()` and `URLValidationError` |
| `app/routes/main.py` | Home page and the short-link redirect |
| `app/routes/api.py` | `POST /api/v1/urls`, OpenAPI spec and API docs page |
| `app/routes/health.py` | Liveness and readiness checks |
| `app/errors.py` | One handler for all HTTP errors: JSON for API clients, HTML for browsers |
| `app/security.py` | Security headers |
| `app/openapi.py` | The OpenAPI specification, built from the running app's configuration |

## Request flows

**Creating a link** (`POST /api/v1/urls`)

1. The route parses the JSON body and rejects anything that is not an object.
2. `create_short_url()` validates the URL (scheme, host, length, blocklist, own domain).
3. It generates a code from a 57-character alphabet with `secrets`, then inserts the row.
4. If the unique index rejects the code, it rolls back and retries, up to 10 attempts.
5. The route returns `201` with `short_code`, `short_url` and `original_url`.

**Following a link** (`GET /<short_code>`)

1. A code that does not match `[A-Za-z0-9]{4,32}` gets a `404` without touching the database.
2. The row is looked up. A missing row is `404`. An expired row is `410`.
3. The stored destination is validated again, so a tampered row or a newly blocked domain is never served.
4. The click is counted (not for `HEAD` requests), then the route returns `302` with `Cache-Control: no-store`.

## Data model

Table `urls`:

| Column | Type | Notes |
|---|---|---|
| `id` | integer | Primary key |
| `short_code` | varchar(32) | Unique index, not null |
| `original_url` | varchar(2048) | Not null |
| `created_at` | timestamp with time zone | Set on insert, UTC |
| `updated_at` | timestamp with time zone | Set on insert and update, UTC |
| `click_count` | integer | Default 0, check constraint `>= 0` |
| `expires_at` | timestamp with time zone | Optional |

SQLite returns naive datetimes, so the model treats them as UTC when checking expiry.

## Design decisions

- **Uniqueness comes from the database.** Generate-then-insert-then-retry stays correct under concurrent requests. A check-then-insert approach would not.
- **`302`, not `301`.** A permanent redirect is cached by browsers, so later visits would never reach the server and never be counted.
- **Atomic click counting.** One `UPDATE ... SET click_count = click_count + 1`, so simultaneous clicks are never lost. `updated_at` is deliberately left alone.
- **A failed counter never blocks a redirect.** The error is logged and the visitor still gets through.
- **Analytics stores no visitor data.** Click events carry only the short code and a timestamp. Listeners can be registered for future features.
- **Validation runs twice.** Once on creation and again on every redirect.
- **Liveness and readiness are separate.** `/health` never touches the database, so a database outage does not cause container restarts.
- **No inline scripts or styles.** That allows a strict Content Security Policy. API docs are rendered server-side for the same reason.
- **Timezone-aware UTC everywhere.**
