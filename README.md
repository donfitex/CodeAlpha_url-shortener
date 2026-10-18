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
