#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start.sh — Production startup script for the BrochureIQ Python backend.
# Run this on the Contabo server to start the API.
#
# First time setup:
#   chmod +x start.sh
#
# To start:
#   ./start.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present so PORT, APP_ENV, GROQ_API_KEY etc. are available
if [ -f .env ]; then
  echo "[start.sh] Loading .env"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "[start.sh] Starting BrochureIQ API on port ${PORT:-8000} with ${WEB_CONCURRENCY:-2} worker(s)..."

exec gunicorn -c gunicorn.conf.py app.main:app
