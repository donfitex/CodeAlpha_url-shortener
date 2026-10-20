# API guide

The full, always-current reference is generated from the running application:

- Interactive reference: `/api/v1/docs`
- OpenAPI 3.1 specification: `/api/v1/openapi.json` (import into Postman, Swagger Editor or a client generator)

This page is a short guide. No authentication is required.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/urls` | Create a short URL |
| `GET` | `/{short_code}` | Redirect to the original URL |
| `GET` | `/health` | Liveness check |
| `GET` | `/health/ready` | Readiness check (verifies the database) |

## Create a short URL

```powershell
curl -X POST http://127.0.0.1:5000/api/v1/urls -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/very/long/url\"}"
```

Response `201`:

```json
{
  "short_code": "aB92xK",
  "short_url": "http://127.0.0.1:5000/aB92xK",
  "original_url": "https://example.com/very/long/url"
}
```

### What is accepted

Only `http` and `https` URLs up to `MAX_URL_LENGTH` characters (2048 by default) with a valid public host.
These are rejected with `422`:

- a missing, empty or non-string `url`
- malformed URLs, spaces or control characters, invalid ports
- other schemes, including `javascript:`, `data:` and `file:`
- embedded credentials such as `https://user:pass@example.com`
- `localhost`, private and link-local addresses
- domains on the blocklist, and LinkForge's own domain

The request body is limited to 16 KB.

## Follow a short URL

```powershell
curl -i http://127.0.0.1:5000/aB92xK
```

Returns `302` with a `Location` header and `Cache-Control: no-store`. Each `GET` counts one click.
`HEAD` requests redirect too but are not counted. Browsers get an HTML page for errors. Send
`Accept: application/json` to receive the JSON error structure instead.

## Status codes

| Status | Meaning | Where |
|---|---|---|
| `201` | Short URL created | `POST /api/v1/urls` |
| `302` | Redirect to the original URL | `GET /{short_code}` |
| `400` | Body missing, not valid JSON, or not a JSON object | `POST /api/v1/urls` |
| `404` | Unknown or malformed code, or a destination that fails safety checks | `GET /{short_code}` |
| `405` | Method not allowed (an `Allow` header lists valid methods) | any |
| `410` | Link has expired | `GET /{short_code}` |
| `413` | Request body larger than 16 KB | `POST /api/v1/urls` |
| `422` | URL missing or failed validation; the message says why | `POST /api/v1/urls` |
| `429` | Rate limit exceeded | `POST /api/v1/urls`, `GET /{short_code}` |
| `500` | Unexpected error; details are logged, never returned | any |
| `503` | A unique code could not be generated, or the database is unreachable on `/health/ready` | `POST /api/v1/urls`, `/health/ready` |

## Error structure

Every JSON error has the same shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Only http:// and https:// URLs are supported."
  }
}
```

`message` is safe to show to users. `code` is stable and meant for programs:

| Code | Status |
|---|---|
| `bad_request` | 400 |
| `invalid_json` | 400 |
| `validation_error` | 422 |
| `not_found` | 404 |
| `method_not_allowed` | 405 |
| `gone` | 410 |
| `payload_too_large` | 413 |
| `too_many_requests` | 429 |
| `internal_error` | 500 |
| `service_unavailable` | 503 |
| `error` | any other 4xx |

## Rate limits

Limits apply per client IP. Defaults, configurable with `RATELIMIT_CREATE` and `RATELIMIT_REDIRECT`:

- creating links: 10 per minute and 200 per day
- following links: 120 per minute
- health endpoints are not limited

When a limit is hit the API returns `429`. Wait before retrying.

## Health checks

```json
{"status": "ok"}
```

`GET /health/ready` returns `{"status": "ok", "checks": {"database": "ok"}}`, or `503` with
`"unavailable"` values when the database cannot be reached. Responses contain no configuration or
error details and are never cached.
