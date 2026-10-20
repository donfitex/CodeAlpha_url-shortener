# Deployment guide

This is the provider-neutral baseline: any host that runs a container and offers PostgreSQL works.

## Production architecture

```
Internet
   |
   v
HTTPS / domain
   |
   v
Reverse proxy or hosting platform
   |
   v
Gunicorn  (wsgi.py -> create_app())
   |
   v
Flask
   |
   v
PostgreSQL
```

Run Gunicorn, never the Flask development server. TLS terminates at the proxy or platform.

## Build the image

```powershell
docker build --target production -t linkforge .
```

The image runs as a non-root user, sets `APP_ENV=production`, contains no `.env` or tests, and starts
`gunicorn --config gunicorn.conf.py wsgi:app`. It includes a Docker health check on `/health`.

## Required configuration

Set these in the host's environment, never in the image or the repository.

| Variable | Notes |
|---|---|
| `SECRET_KEY` | At least 32 characters. The app refuses to boot otherwise |
| `DATABASE_URL` | PostgreSQL connection string. Both `postgresql://` and legacy `postgres://` work |
| `BASE_URL` | The public https address, for example `https://lnk.example`. Used in every short link |
| `PROXY_COUNT` | Number of trusted proxies in front of the app, usually `1` |
| `RATELIMIT_STORAGE_URI` | A shared store such as Redis when running more than one worker |

Redis storage needs the `redis` Python package, which is not in `requirements.txt` yet. Add it when
you adopt Redis. Optional tuning: `BLOCKED_DOMAINS`, `SHORT_CODE_LENGTH`, `MAX_URL_LENGTH`,
`RATELIMIT_CREATE` and `RATELIMIT_REDIRECT` (see [development](development.md)).

## Gunicorn settings

Defaults suit a small instance: 2 workers with 4 threads each, a 30 second timeout, workers recycled
after about 1000 requests, logs on stdout and stderr.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Listening port. Many platforms set this for you |
| `GUNICORN_BIND` | `0.0.0.0:$PORT` | Full bind address override |
| `WEB_CONCURRENCY` | `2` | Worker processes |
| `GUNICORN_THREADS` | `4` | Threads per worker |
| `GUNICORN_TIMEOUT` | `30` | Seconds before a hung worker is killed |
| `GUNICORN_LOG_LEVEL` | `info` | Log verbosity |

## Release procedure

1. Back up the database.
2. Build and push the new image.
3. Apply migrations as a separate step, before starting the new version:

```powershell
   docker run --rm --env-file prod.env linkforge flask --app run db upgrade
```

4. Start the new version and wait for `GET /health/ready` to return `200`.
5. Smoke test: create a link through the API, follow it, confirm the `302`, and open `/api/v1/docs`.
6. Check the logs for errors.

Production never runs migrations automatically on start.

## Health checks

- `GET /health` is liveness. Use it for container restarts.
- `GET /health/ready` is readiness and checks the database. Use it for load balancer decisions.

## Logging

Logs go to stdout and stderr and are collected by the platform. Unexpected errors are logged with
tracebacks, and clients only ever see generic messages. Never log secrets or full connection strings.

## Backups and recovery

Take regular PostgreSQL backups and test restoring them.

```powershell
pg_dump --format=custom --file linkforge.dump "$env:DATABASE_URL"
pg_restore --clean --if-exists --dbname "$env:DATABASE_URL" linkforge.dump
```

To recover: provision a database, restore the latest dump, apply migrations, start the app and check
`/health/ready`. To roll back a bad release, redeploy the previous image. If the release included a
migration, run `flask --app run db downgrade` first (after a backup), or restore the dump.

## Checklist

- [ ] `APP_ENV=production` (the image default) and debug is off
- [ ] Strong `SECRET_KEY`, real `DATABASE_URL`, correct `BASE_URL`
- [ ] HTTPS in front of the app, `PROXY_COUNT` matches the proxies you control
- [ ] Shared rate-limit store if more than one worker
- [ ] Migrations applied, health checks green
- [ ] Backups scheduled and a restore tested
- [ ] `pip-audit -r requirements.txt` is clean
