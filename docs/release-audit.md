# Release audit

The audit turns the production readiness checklist into evidence. Run it before every release and
commit the record.

```powershell
python scripts/release_audit.py                      # fast: code, behavior and configuration
python scripts/release_audit.py --full               # also runs tests with coverage, browser tests, Ruff and pip-audit
python scripts/release_audit.py --url https://your-domain.example --markdown docs/audit-record.md
```

Results are `[PASS]`, `[FAIL]` (the audit exits 1), `[MANUAL]` (automated preconditions pass, a person
must sign off) or `[SKIP]` (an option such as `--run-tests` was not given). Evidence never includes
secrets or connection strings. The audit uses temporary databases and never touches real data.

## What is checked

| Section | Items | How they are verified |
|---|---|---|
| Functional | Shortening, unique codes, stored URLs, redirect, click count, invalid URLs, 404s, expiry | A real app on a temporary database is driven through the API and redirects |
| Frontend | Loading, error and success states, copy button, accessibility basics | Template and script contents, plus the browser tests that cover them |
| Backend | Application factory, modular layout, service layer, migrations, PostgreSQL, configuration, error handling | Imports, file structure, migrations applied to a fresh database and compared to the models, live error responses |
| Security | `.env` handling, secrets, debug, validation, safe redirects, input limits, headers, rate limiting, error protection | Git ignore rules and history, a secret-pattern scan, tampered rows, forced server errors, real rate-limit hits |
| Testing | Unit, model, service, API, redirect, error and browser tests, CI, coverage | Test files and counts, the CI workflow, and a coverage run with `--run-tests` |
| Production | Gunicorn, PostgreSQL, HTTPS, variables, health, logging, backups, recovery, documentation | Configuration, images, scripts, docs, and the production preflight with sample production values |
| Live deployment | Smoke test, HTTPS and HSTS | Only with `--url` |

## Manual sign-off

Five items need a person. Do each once per release and record the result.

| Item | How to verify |
|---|---|
| Responsive | Open the site and use the browser device toolbar at 360, 768 and 1280 px wide. Nothing should overflow sideways |
| Professional UI | Read the page as a stranger would: spacing, typography, contrast, consistent buttons and cards |
| Rich UX | Shorten a link, trigger an error, copy the result, follow the link, and open the API docs |
| Mobile support | Repeat the walkthrough on a real phone. The URL keyboard should appear and the copy button should work |
| Backups | Confirm backups run on a schedule, run `scripts/restore.sh` into a scratch database, and compare row counts (see [deployment](deployment.md)) |

## Audit record

`--markdown` writes a table with the date, commit and every result. Commit it as release evidence, and
add the manual sign-offs by hand:

```
Release: v1.0.0       Date: ____-__-__       Auditor: __________
[ ] Responsive   [ ] Professional UI   [ ] Rich UX   [ ] Mobile support   [ ] Backups
Notes:
```

## When an item fails

Fix the cause, never the audit. The audit is locked to the original checklist by a test, so removing a
check fails the suite. If a secret was committed, rotate it first, because deleting the file does not
remove it from Git history.
