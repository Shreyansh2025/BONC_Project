# Search AI runtime settings integration

This patch connects the SQL-backed `SearchRulesSettings` snapshot to the three
live search pipelines.

## Runtime behavior

- Each non-empty `/api/search` request reads the active settings once from
  `searchai.SearchAISettings` using `asyncio.to_thread`.
- The same validated, immutable settings object is passed to the company,
  B2B product, and local product search functions invoked by that request.
- The search functions use the supplied values for semantic candidate count,
  lexical-to-semantic fallback threshold, and the relevant minimum/high-
  confidence scores.
- Because the current settings are read from SQL Server for every request,
  updates are visible to the next request in every Gunicorn worker. No
  per-process settings cache or service restart is required for a settings
  change to become visible.
- If the settings store cannot be read, `/api/search` returns HTTP 503 instead
  of silently falling back to defaults. This prevents operators from thinking
  a saved setting is active when the database configuration is unavailable.
- Direct internal calls that omit the optional settings argument retain the
  validated model defaults for backwards compatibility. The public Search AI
  route always supplies the persisted snapshot.

## Files in this patch

- `py-backend/app/search_ai/routes/search.py`
- `py-backend/app/search_ai/services/b2b_search.py`
- `py-backend/app/search_ai/services/b2b_product_search.py`
- `py-backend/app/search_ai/services/product_search.py`
- `py-backend/app/search_ai/services/search_common.py`
- `py-backend/tests/test_search_app.py`
- `py-backend/tests/test_search_runtime_settings.py`

`search_main.py` is not included because the settings router was already
registered in the existing staging branch.

## Tests

From `py-backend`:

```powershell
python -m unittest discover -s tests -v
```

The patch adds tests for all three candidate-limit controls, all three lexical
fallback controls, each minimum score, company/B2B high-confidence cutoffs,
one settings snapshot per request, and fail-closed behavior if the settings
store cannot be read.
