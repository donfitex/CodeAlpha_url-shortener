# LinkForge

A production-ready URL shortener built with Flask. Paste a long link, get a short one, share it.

## Features

- Secure, collision-safe short codes (6 characters by default)
- Responsive web interface and a JSON REST API with OpenAPI documentation
- Click counting and optional link expiration
- URL validation that rejects dangerous schemes, private addresses and embedded credentials
- Strict Content Security Policy and other security headers, rate limiting, domain blocklist
- Health endpoints, Docker images, Gunicorn, migrations, tests and CI

## Quick start

Requires Python 3.10 or newer (3.12 recommended).

```powershell
git clone <repository-url>
cd url-shortener
python -m venv venv
venv\Scripts\Activate.ps1          # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env             # macOS/Linux: cp .env.example .env
```

Put a real secret in `.env`:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Then create the database and start the development server:

```powershell
flask --app run db upgrade
python run.py
```

Open http://127.0.0.1:5000. The API reference is at `/api/v1/docs`.

## With Docker

Requires Docker Desktop. This starts the app and PostgreSQL, applies migrations and serves on port 5000:

```powershell
docker compose up --build
```

Stop with `docker compose down`. Add `-v` to also delete the database volume.

## Everyday commands

| Task | Command |
|---|---|
| Run the app | `python run.py` |
| Apply migrations | `flask --app run db upgrade` |
| Create a migration | `flask --app run db migrate -m "describe the change"` |
| Run tests | `pytest` |
| Run browser tests | `pytest -m e2e` |
| Coverage report | `pytest --cov --cov-report=term-missing` |
| Lint | `ruff check .` |
| Format | `ruff format .` |
| Rebuild CSS | `.\tailwindcss.exe -i app/static/css/input.css -o app/static/css/styles.css --minify` |
| Production image | `docker build --target production -t linkforge .` |
| Production preflight | `python -m app.preflight --check-db --strict` |
| Back up the database | `sh scripts/backup.sh` |
| Smoke test a deployment | `python scripts/smoke_test.py https://your-domain.example` |
| Release audit | `python scripts/release_audit.py --full` |

## Example

```powershell
curl -X POST http://127.0.0.1:5000/api/v1/urls -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/very/long/url\"}"
```

## Documentation

- [Architecture](docs/architecture.md): layers, request flows, data model
- [API guide](docs/api.md): endpoints, errors, examples
- [Development](docs/development.md): setup, environment variables, migrations, tooling
- [Testing](docs/testing.md): test suites and how to run them
- [Security](docs/security.md): controls and abuse handling
- [Deployment](docs/deployment.md): production configuration and operations
- [Release audit](docs/release-audit.md): the checklist as runnable evidence

## Project layout

```
app/        application package (routes, services, models, utils, templates, static)
config/     environment-specific configuration
migrations/ database migrations
tests/      unit, API, redirect, security, end-to-end and documentation tests
docs/       project documentation
```
