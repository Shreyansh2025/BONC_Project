# Gunicorn configuration for production (Contabo server).
# Start the server with:  gunicorn -c gunicorn.conf.py app.main:app
# Or simply run:          ./start.sh

import os

# ---------------------------------------------------------------------------
# Worker setup
# ---------------------------------------------------------------------------
# UvicornWorker gives async FastAPI support inside Gunicorn's process model.
worker_class = "uvicorn.workers.UvicornWorker"

# 2 workers is a safe default for a Contabo VPS with ML models in memory.
# Each worker loads its own copy of sentence-transformer + rembg models.
# Increase only if you have enough RAM (check with `free -h` on the server).
workers = int(os.getenv("WEB_CONCURRENCY", 2))

# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------
port = os.getenv("PORT", "8000")
bind = f"0.0.0.0:{port}"

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
# PDF processing + OCR + AI structuring can take 30-90s for large files.
# 120s gives enough headroom without hanging forever.
timeout = 120
keepalive = 5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Log to stdout/stderr so systemd / Docker captures everything.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "warning").lower()

# ---------------------------------------------------------------------------
# Process naming (makes `ps aux` readable on the server)
# ---------------------------------------------------------------------------
proc_name = "brochureiq-api"
