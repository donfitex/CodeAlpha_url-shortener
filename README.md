# LinkForge

Production-ready URL shortener built with Flask.

## Setup

python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python run.py

## Database migrations

Never change the schema by hand. Always use migrations.

1. Edit a model in `app/models/`.
2. Generate: `flask --app run db migrate -m "describe the change"`
3. Review the new file in `migrations/versions/`.
4. Apply: `flask --app run db upgrade`
5. Roll back one step if needed: `flask --app run db downgrade`
6. Commit the migration file together with the model change.

In production, run `flask --app run db upgrade` on deploy, before starting the new version.
Back up the database before any migration that drops or alters data.

## Frontend styles

Tailwind is compiled with the standalone CLI (download `tailwindcss.exe` from the Tailwind releases page).

- Build: `.\tailwindcss.exe -i app/static/css/input.css -o app/static/css/styles.css --minify`
- Watch: same command with `--watch`

`styles.css` is committed, so deployment does not need a build step. Rebuild after changing templates.

## End-to-end tests

Browser tests use Playwright and are excluded from a plain `pytest` run.

- One-time setup: `playwright install chromium`
- Run: `pytest -m e2e`
- Watch the browser: `pytest -m e2e --headed --slowmo 300`
- Everything: `pytest -m "e2e or not e2e"`

## Code quality

- Lint: `ruff check .` (auto-fix: `ruff check --fix .`)
- Format: `ruff format .` (verify only: `ruff format --check .`)
- Pre-commit hooks (one-time): `pre-commit install`; run on everything: `pre-commit run --all-files`

Configuration lives in `pyproject.toml`. CI runs the same checks.
