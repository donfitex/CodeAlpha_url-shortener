# Development guide

## Prerequisites

- Python 3.10 or newer (the Docker images use 3.12)
- Git
- Docker Desktop (optional, for the PostgreSQL stack and production image)
- Visual Studio Code with the Python, Ruff and Docker extensions (recommended)

## Setup

```powershell
python -m venv venv
venv\Scripts\Activate.ps1          # macOS/Linux: source venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env             # macOS/Linux: cp .env.example .env
```

In VS Code, press `Ctrl+Shift+P`, choose **Python: Select Interpreter** and pick `venv`.

## Environment variables

`.env` holds real local values and is never committed. `.env.example` documents every variable safely.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Selects the config: `development`, `testing` or `production` |
| `SECRET_KEY` | none | Signs sessions. Required in production, at least 32 characters |
| `DATABASE_URL` | local SQLite file | Database connection string. PostgreSQL in production |
| `BASE_URL` | `http://127.0.0.1:5000` | Public address used to build short links. Also blocks shortening LinkForge's own domain |
| `SHORT_CODE_LENGTH` | `6` | Length of generated codes (4 to 32) |
| `MAX_URL_LENGTH` | `2048` | Longest accepted URL |
| `BLOCKED_DOMAINS` | empty | Comma-separated domains that cannot be shortened or followed |
| `PROXY_COUNT` | `0` | Number of trusted reverse proxies in front of the app |
| `RATELIMIT_STORAGE_URI` | `memory://` | Rate-limit storage. Use a shared store for several workers |
| `RATELIMIT_CREATE` | `10 per minute;200 per day` | Limit for creating links |
| `RATELIMIT_REDIRECT` | `120 per minute` | Limit for following links |
| `POSTGRES_PASSWORD` | `linkforge` | Used only by `docker-compose.yml` |

Generate a secret key:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

## Running locally

```powershell
flask --app run db upgrade
python run.py
```

The app is at http://127.0.0.1:5000. Debug mode comes from the development config. `python run.py`
refuses to start when `APP_ENV=production`.

## Database

Development and tests use SQLite. Production uses PostgreSQL. Never change the schema by hand.

1. Edit a model in `app/models/`.
2. Generate a migration: `flask --app run db migrate -m "describe the change"`
3. Open the new file in `migrations/versions/` and review it.
4. Apply it: `flask --app run db upgrade`
5. Roll back one step if needed: `flask --app run db downgrade`
6. Commit the migration together with the model change.

Check the current revision with `flask --app run db current`. Back up production data before any
migration that drops or alters columns.

## Frontend styles

Tailwind is compiled with the standalone CLI. Download `tailwindcss.exe` from the Tailwind releases
page into the project root (it is gitignored).

```powershell
.\tailwindcss.exe -i app/static/css/input.css -o app/static/css/styles.css --minify
```

Use `--watch` instead of `--minify` while designing. `styles.css` is committed, so deployment needs
no build step. Rebuild after changing templates. JavaScript lives in `app/static/js/app.js`. No
inline scripts or styles are allowed, because the Content Security Policy forbids them.

## Code quality

```powershell
ruff check .
ruff format .
```

Rules and limits are in `pyproject.toml`. Install the optional commit hooks once with
`pre-commit install`, and run them on everything with `pre-commit run --all-files`.

## Docker

```powershell
docker compose up --build        # app + PostgreSQL on http://localhost:5000
docker compose down              # stop, keep data
docker compose down -v           # stop and delete the database volume
docker compose run --rm web pytest -q
docker compose exec db psql -U linkforge
```

Compose runs migrations on start. The production image is built with
`docker build --target production -t linkforge .` (see [deployment](deployment.md)).

## Workflow

Each change follows the same loop: understand, plan, implement, explain, test, fix, review, commit.
Commit messages use the Conventional Commits style, for example `feat(api): add URL shortening endpoint`.
Run the full test suite (see [testing](testing.md)) before committing.

## Troubleshooting

- **PowerShell blocks activation:** run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
- **`no such table: urls`:** run `flask --app run db upgrade`.
- **Styles look stale:** rebuild the CSS and hard refresh with `Ctrl+Shift+R`.
- **429 while testing by hand:** raise `RATELIMIT_CREATE` in `.env`.
- **Docker errors about a pipe or engine:** Docker Desktop is not running yet.
- **`password authentication failed` in Docker:** the password changed after the volume was created. Run `docker compose down -v`.
- **Gunicorn on Windows:** it needs Unix-only modules. Use the Flask dev server, or Docker.
