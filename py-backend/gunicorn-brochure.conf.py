"""Gunicorn config for the standalone BrochureIQ application.

Set APP_ENV, PORT, UPLOADS_DIR and other required variables in the service
manager before launching. Start with one worker to avoid duplicate ML model
loads and to keep resource use predictable on the Contabo host.
"""
import os

worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", "1"))

_raw_port = os.getenv("PORT", "8003")
try:
    _port = int(_raw_port)
    if not 1 <= _port <= 65535:
        raise ValueError
except ValueError as exc:
    raise RuntimeError(f"Invalid PORT value for BrochureIQ: {_raw_port}") from exc

# Keep application ports private. Nginx or an approved internal caller can
# reach this process via loopback on the same host.
bind = f"127.0.0.1:{_port}"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
proc_name = "bonc-brochureiq"
