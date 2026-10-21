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

## Preflight check

Before every release, run the readiness checks with the production environment variables set:

```powershell
python -m app.preflight --check-db --strict
```

Inside the image (the host's real variables must be passed in):

```powershell
docker run --rm --env-file prod.env linkforge python -m app.preflight --check-db --strict
```

Each line is `[PASS]`, `[WARN]` or `[FAIL]`. The exit status is 1 if anything fails, or warns with
`--strict`. Reports never print secrets or connection strings.

| Check | Verifies |
|---|---|
| environment | Debug and testing are off, `APP_ENV` is `production` or unset |
| secret key | At least 32 characters, not a placeholder |
| database | PostgreSQL, with connection health checks |
| public URL | `BASE_URL` uses https and is not a local address |
| cookies | Secure, HttpOnly and SameSite are set |
| https | HSTS on; warns if `PROXY_COUNT` is 0 |
| rate limiting | Enabled; warns if in-memory limits are used with several workers; fails if Redis is configured without its package |
| server | `wsgi.py`, `gunicorn.conf.py` and Gunicorn are present |
| logging | Level is INFO or higher |
| static assets | Compiled CSS exists, cache lifetime is set |
| migrations | Migration files exist |
| runtime | JSON errors, `/health` and security headers work |
| database connection, migrations applied, readiness | With `--check-db`: reachable, at the latest revision, `/health/ready` returns 200 |

## Static assets

The compiled stylesheet is committed, so deployment needs no build step. Static files are served
with a one hour cache lifetime, because file names are not fingerprinted. For heavy traffic, let the
reverse proxy or a CDN cache `/static/`.

## Rate limiting strategy

Limits are per client IP. The default `memory://` store is per process, so with several workers the
effective limit is multiplied by the worker count. Production should use a shared store: set
`RATELIMIT_STORAGE_URI` to a Redis URL and add the `redis` package to `requirements.txt`. Set
`PROXY_COUNT` to the number of trusted proxies, otherwise every visitor shares the proxy's address.
The preflight check warns about both problems.

## Backups and recovery

Back up with `scripts/backup.sh` (needs the PostgreSQL client tools, `pg_dump` 16 or newer for a
PostgreSQL 16 server):

```sh
DATABASE_URL=postgresql://... BACKUP_DIR=/var/backups/linkforge BACKUP_KEEP=14 sh scripts/backup.sh
```

It writes `linkforge-<UTC timestamp>.dump` in compressed custom format, readable only by its owner,
verifies the archive contains the `urls` table, and keeps the newest `BACKUP_KEEP` files (default 14).
A failed or unverified backup is discarded. Schedule it with cron or your platform's job scheduler,
at least daily, and copy the files off the machine.

Restore with `scripts/restore.sh`. It is destructive, so it refuses to run without
`CONFIRM_RESTORE=yes`, and it restores in a single transaction so a failure leaves the target untouched:

```sh
CONFIRM_RESTORE=yes sh scripts/restore.sh /var/backups/linkforge/linkforge-20261017-020000Z.dump postgresql://.../linkforge
```

**Recovery procedure**

1. Stop or isolate the application so nothing writes during the restore.
2. Provision or empty the target database and restore the newest good backup.
3. Run `python -m app.preflight --check-db --strict` and apply migrations if needed.
4. Start the application and confirm `GET /health/ready` returns `200`.
5. Smoke test: create a link, follow it, check the click count.

Rehearse the restore into a scratch database (pass its URL as the second argument) on a schedule. A
backup that has never been restored is not a backup. To roll back a bad release, redeploy the previous
image, and restore the pre-release backup if the release changed the schema.

## Deploying to Render

The repository includes a `render.yaml` Blueprint that defines the web service and its PostgreSQL
database. Render builds the production image from the `Dockerfile` (its last stage is `production`),
provides HTTPS, and runs the service behind one proxy.

| Setting | Source |
|---|---|
| `SECRET_KEY` | Generated by Render, never stored in the repository |
| `DATABASE_URL` | Connection string of the Blueprint's database |
| `BASE_URL` | You enter it when creating the Blueprint |
| `PROXY_COUNT` | `1`, because Render puts one proxy in front of the app |
| `WEB_CONCURRENCY` | `1`, so in-memory rate limits stay accurate |

The database accepts internal connections only. The service and database share a region (Frankfurt).

### First deployment

1. Push the repository to GitHub.
2. In the Render dashboard choose **New**, then **Blueprint**, connect the repository, and apply `render.yaml`.
3. When prompted for `BASE_URL`, enter the public address, for example `https://linkforge.onrender.com`.
   Check the real address on the service page after creation. If the name was taken, Render adds a suffix,
   and you must update `BASE_URL` to match.
4. Wait for the build and the first deploy. The logs show the migration, then Gunicorn starting.
5. Run the smoke test against the public address:

```powershell
   python scripts/smoke_test.py https://linkforge.onrender.com
```

The smoke test waits for a cold start, then checks liveness, database readiness, the home page,
security headers, link creation (including that `BASE_URL` matches), the redirect, the API spec and
the 404 behavior. It creates one real link. Run it after every deployment.

### Updates

With `autoDeploy: true`, every push to the connected branch redeploys. If your dashboard offers
auto-deploy after CI checks pass, choose it so a red build never ships. To roll back, deploy a
previous commit with **Manual Deploy**, then restore a backup if the release changed the schema.

### Custom domain

Add the domain under the service's settings and create the DNS records Render shows. HTTPS is
issued automatically. Then change `BASE_URL` to the new address and redeploy, or every short link
keeps the old host.

### Free plan limits

- The service spins down after 15 minutes without traffic. The next request waits about a minute,
  and in-memory rate limits reset.
- The free Postgres database holds 1 GB, expires 30 days after creation, has no backups, and only
  one can exist per workspace.
- Free instances can be restarted at any time, and usage hours are capped per month.

Treat the free plan as a trial. For real use, upgrade the web service and the database to paid
instances, which do not spin down and keep database backups. Render documents its limits at
https://render.com/docs/free, so check there before relying on them.

### Moving to a paid instance

Paid instances can run migrations as a pre-deploy step instead of at start. In `render.yaml`, change
the plan, delete the `dockerCommand` line, and add:

```yaml
    preDeployCommand: flask --app run db upgrade
```

The image's default command then starts Gunicorn. Migrating before the new version starts is the safer
pattern once you run more than one instance. Backups on a paid database are managed by Render. The
scripts in `scripts/` remain useful for your own copies, and they need external access to the database,
so the `ipAllowList` in `render.yaml` must allow your address.

## Checklist

- [ ] `APP_ENV=production` (the image default) and debug is off
- [ ] Strong `SECRET_KEY`, real `DATABASE_URL`, correct `BASE_URL`
- [ ] HTTPS in front of the app, `PROXY_COUNT` matches the proxies you control
- [ ] Shared rate-limit store if more than one worker
- [ ] Migrations applied, health checks green
- [ ] Backups scheduled and a restore tested
- [ ] `pip-audit -r requirements.txt` is clean
- [ ] python scripts/smoke_test.py <public address> passes after every deploy.
