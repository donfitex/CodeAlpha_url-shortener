import os

_SHM = "/dev/shm"  # noqa: S108  (shared-memory dir for worker heartbeat files)

port = os.environ.get("PORT", "8000")

bind = os.environ.get("GUNICORN_BIND", f"0.0.0.0:{port}")
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to contain slow memory growth
max_requests = 1000
max_requests_jitter = 100

# Heartbeat files in RAM: avoids spurious worker timeouts in containers
worker_tmp_dir = _SHM if os.path.isdir(_SHM) else None

# Log to stdout/stderr so Docker and hosting platforms collect them
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

proc_name = "linkforge"
