Search AI settings backend patch
================================

Copy the py-backend directory in this archive into the repository root and
allow it to merge/overwrite the four target files:

  py-backend/app/search_ai/services/settings_store.py
  py-backend/app/search_ai/routes/settings.py
  py-backend/tests/test_search_settings_store.py
  py-backend/tests/test_search_settings_api.py

Then register the route in py-backend/app/search_main.py:

1. Add this import beside the other route imports:
   from app.search_ai.routes.settings import router as settings_router

2. Add this line beside the other include_router calls:
   application.include_router(settings_router)

This registers:
  GET  /api/admin/search-settings
  PUT  /api/admin/search-settings
  GET  /api/admin/search-settings/history?limit=50

Configuration required on the Search AI staging service:
  SEARCH_ADMIN_API_TOKEN=<a new, long random secret>
  SEARCH_ADMIN_ACTOR=admin:token

Do not reuse SEARCH_INTERNAL_API_TOKEN. Do not put SEARCH_ADMIN_API_TOKEN in
Vite variables or browser-side code. The API returns 503 until its dedicated
token is configured.

PUT request example:
{
  "expected_version": 1,
  "settings": {
    "semantic_candidate_limit": 50,
    "lexical_result_threshold": 20,
    "company": {
      "semantic_min_score": 35.0,
      "semantic_high_confidence_score": 60.0
    },
    "b2b_product": {
      "semantic_min_score": 40.0,
      "semantic_high_confidence_score": 65.0
    },
    "local_product": {
      "semantic_min_score": 10.0
    }
  },
  "change_reason": "Initial admin settings test"
}

The first implementation persists and validates settings but does NOT yet
wire them into the live search algorithms. That is a separate integration
step after this persistence/API layer passes tests.

Apply SQL migration 003 to RN_BoncNetwork_Staging before exercising the API.
