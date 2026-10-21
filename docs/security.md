# Security guide

LinkForge is a public service that redirects visitors to user-supplied addresses, so most of its
security work is about not becoming a tool for abuse.

## Controls

| Area | Control | Where |

|---|---|---|
| Secrets | Production refuses to start without a strong `SECRET_KEY` (32+ characters, not a placeholder) and a `DATABASE_URL`. `.env` is gitignored | `config/production.py` |
| Input validation | Only `http` and `https`; rejects `javascript:`, `data:`, `file:` and others, credentials, private and local hosts, control characters, oversized input | `app/utils/validators.py` |
| Safe redirects | Destinations come only from the database and are validated again before every redirect | `app/routes/main.py` |
| Request size | Bodies over 16 KB are rejected with `413` | `config/base.py` |
| Database safety | SQLAlchemy parameterized queries only. A test forbids raw SQL building | `tests/test_security.py` |
| Output escaping | Jinja auto-escaping is on. JavaScript never uses `innerHTML` or similar sinks | templates, `app.js` |
| Security headers | Content Security Policy (CSP) with no inline code, `nosniff`, frame denial, referrer and permissions policies, HSTS over HTTPS in production | `app/security.py` |
| Rate limiting | Per client IP on link creation and redirects | `app/routes/` |
| Error handling | Generic messages only, tracebacks stay in logs, API responses are never cached | `app/errors.py` |
| Cookies | `HttpOnly` and `SameSite=Lax` everywhere, `Secure` in production | `config/` |
| Debug | Disabled in production. The dev server refuses to start there | `run.py`, `wsgi.py` |
| Containers | Non-root user, no `.env` or tests in the image | `Dockerfile`, `.dockerignore` |

## CSRF

The app sets no session cookies and its only state-changing endpoint accepts JSON. A cross-site
browser request cannot send `application/json` without a preflight, and no cross-origin access is
allowed. If forms or sessions are added later, add CSRF protection (for example Flask-WTF) first.

## Rate limiting and proxies

Limits are per client IP. Behind a reverse proxy, set `PROXY_COUNT` to the exact number of proxies
you control, so the real client address is used. A wrong value lets clients fake `X-Forwarded-For`
and dodge limits. The default `memory://` store is per process, so with several workers the effective
limit is multiplied. Use a shared store through `RATELIMIT_STORAGE_URI` in production.

## Abuse handling

To take a domain down, use the blocklist:

1. Add it to `BLOCKED_DOMAINS` (comma-separated, subdomains are included) and restart.
2. New links to it are refused with `422`. Existing links to it stop redirecting and return `404`.
3. Optionally delete the rows: `DELETE FROM urls WHERE original_url LIKE '%bad.example%';`. Back up first.

LinkForge refuses to shorten its own domain, which prevents redirect loops.

## Dependency review

Versions are pinned. Check for known vulnerabilities regularly and before each release:

```powershell
pip-audit -r requirements.txt
```

Update a flagged package in `requirements.txt` and re-run the full test suite.

## Not covered yet

- A public form for reporting malicious links
- Automated phishing and malware scanning of destinations (for example Google Safe Browsing)
- User accounts and API keys, so abuse cannot be tied to a person
- A shared rate-limit store (a deployment choice, see [deployment](deployment.md))

## Reporting a vulnerability

Please report security issues privately to the maintainer rather than opening a public issue.
Include steps to reproduce and the affected version.
