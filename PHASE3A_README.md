# BrochureIQ Phase 3A — isolated implementation candidate

Status: local working-copy changes only. Nothing has been pushed to GitHub or deployed to Contabo.

## What changed

- Added `py-backend/app/brochure_main.py`: standalone BrochureIQ FastAPI entry point. It registers brochure routes and `/api/uploads`, preserves the existing `/api/healthz` response, and adds service-specific `/health/live` and `/health/ready` checks. It starts only database initialization and background-removal warm-up; it does not build Search AI indexes or register search routes.
- Added `py-backend/app/utils/search_index_client.py`: HTTP client for the agreed Search AI internal endpoint `POST /internal/v1/indexes/local-products/refresh`, using `SEARCH_INTERNAL_BASE_URL` and `SEARCH_INTERNAL_API_TOKEN`. It sends `event_id` and `reason`, uses a 2-second connect / 60-second read timeout, retries one transient transport/5xx failure once, accepts HTTP 200 as completed, and does not inspect or forward the internal JSON body.
- Updated `py-backend/app/routes/brochure.py`: removed its direct import of `app.utils.product_search.build_index`. Product save/deletion now call the internal HTTP client after the synchronous SQL helper returns. Those helpers exit their SQLAlchemy `begin()` context (commit) before returning. The public response bodies/statuses remain unchanged if refresh is unavailable; failures are logged and the existing hourly Search AI index refresh remains the recovery path.
- Added `py-backend/gunicorn-brochure.conf.py`: separate Gunicorn configuration, one worker by default, loopback binding, 120-second timeout and stdout/stderr logging.
- Added `py-backend/tests/test_search_index_client.py`: four standard-library `unittest` tests for the internal client.

## Verification performed

- Python syntax compilation passed for the changed/new Python files.
- Four client unit tests passed: HTTP 200, one retry after HTTP 500, missing configuration, and no retry for HTTP 401.
- An import/route-registration check passed using temporary in-memory stubs for `rembg` and `onnxruntime` because those packages are not installed in this local execution environment. No packages were installed.
- Confirmed that `app/brochure_main.py` does not register `/api/search` or `/api/debug/index-status`.
- Confirmed that no direct `app.utils.product_search` import remains in `app/routes/brochure.py`.

## Not yet verified / blockers

- This is not a deployment and has not been run against SQL Server, real OCR, rembg inference, Groq, or the live website.
- Ajay must implement the matching Search AI endpoint and its server-side authentication, refresh lock, atomic index replacement, loopback binding and Nginx `/internal/` rejection before end-to-end integration can pass.
- A staging service-specific environment must explicitly configure `APP_ENV=staging`, `PORT=8003`, an absolute staging-only `UPLOADS_DIR`, `SEARCH_INTERNAL_BASE_URL=http://127.0.0.1:8002`, and `SEARCH_INTERNAL_API_TOKEN`. Never put the token in Git or logs. Use different token values for staging and production.
- The Search AI route's HTTP 200 must mean the refreshed index was built and activated. BrochureIQ deliberately preserves existing public save/delete responses even if the internal refresh is down; the periodic Search AI refresh is the fallback, so search visibility can be delayed until recovery.
- Preserve existing production `app/main.py` and `gunicorn.conf.py`; do not deploy or change Nginx as part of this candidate.

## Suggested staging launch (only after review and environment setup)

From `py-backend`, after explicitly exporting the environment above and confirming the staging virtual environment has the required project packages:

```bash
APP_ENV=staging PORT=8003 WEB_CONCURRENCY=1 \
  UPLOADS_DIR=/absolute/path/to/staging/uploads \
  SEARCH_INTERNAL_BASE_URL=http://127.0.0.1:8002 \
  SEARCH_INTERNAL_API_TOKEN='<staging-secret>' \
  venv/bin/gunicorn -c gunicorn-brochure.conf.py app.brochure_main:app
```

This is a proposed staging command, not executed here. Confirm the actual service manager, working directory, upload path and token provisioning before using it. Do not run in the active production checkout.

## Files in this change set

- `py-backend/app/brochure_main.py` (new)
- `py-backend/app/utils/search_index_client.py` (new)
- `py-backend/app/routes/brochure.py` (modified)
- `py-backend/gunicorn-brochure.conf.py` (new)
- `py-backend/tests/test_search_index_client.py` (new)
