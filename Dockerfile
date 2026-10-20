# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --create-home app


# ---------- development: dev tools, Flask dev server ----------
FROM base AS development
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY --chown=app:app . .
USER app
EXPOSE 5000
CMD ["flask", "--app", "run", "run", "--host", "0.0.0.0", "--port", "5000", "--debug"]


# ---------- production: runtime dependencies only, Gunicorn ----------
FROM base AS production
ENV APP_ENV=production
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY --chown=app:app . .
USER app
EXPOSE 8000
# Tune via PORT, WEB_CONCURRENCY, GUNICORN_THREADS, GUNICORN_TIMEOUT (see gunicorn.conf.py)
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]