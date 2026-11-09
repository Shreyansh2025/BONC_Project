# Search AI Project Checkpoint — 9 November 2026

This checkpoint supersedes earlier planning notes where they describe the settings API, runtime integration, or Admin UI as not yet implemented. It records verified facts and user-reported outcomes separately. Production remains unchanged.

## 1. Current architecture

| Component | Current staging setup |
|---|---|
| Search AI standalone FastAPI app | `app.search_main:app`, loopback port `8002`, service `bonc-search-ai-staging.service` |
| BrochureIQ standalone FastAPI app | `app.brochure_main:app`, loopback port `8003`, service `bonc-brochureiq-staging.service` |
| Legacy combined staging app | Port `8001` (rollback/legacy service) |
| Production API | Port `8000`; do not modify as part of this work |
| Search AI Admin frontend | Separate `search-ai-admin/` React + TypeScript + Vite app; local dev port `5174` |

The Search AI search contract remains `POST /api/search` with JSON fields `SearchText`, `PageNo`, `PageSize`, and `Type`. The Search AI admin API is registered separately under `/api/admin/`; it does not replace the search contract.

## 2. Backend implementation status

### Completed and user-verified

- Search Rules Pydantic model, validation, SQL Server persistence, version history, and dedicated admin-token authentication are implemented.
- Staging migration `003_create_search_ai_settings.sql` was applied to `RN_BoncNetwork_Staging`, creating `searchai.SearchAISettings` and `searchai.SearchAISettingsHistory`.
- Admin API routes:
  - `GET /api/admin/search-settings`
  - `PUT /api/admin/search-settings`
  - `GET /api/admin/search-settings/history?limit=50`
- The active settings were restored to the original defaults after API testing; the last user-provided output showed version `4` with the defaults below.
- Runtime integration reads one settings snapshot per search request and applies it to company, B2B-product, and local-product search. Settings changes do not require a service restart.
- User-reported backend suite result: **93/93 tests passed** after settings/runtime integration and spelling updates.
- Two additional route-level API tests (successful PUT update and settings-history retrieval) were drafted in `Search_AI_Settings_API_Tests_Expanded.patch`. The complete mocked settings API test module passed **7/7** in an isolated extracted test harness. The patch has not yet been applied to the user's working tree, so it is not included in the reported 93-test run.
- `SEARCH_ADMIN_API_TOKEN` is distinct from `SEARCH_INTERNAL_API_TOKEN`. The dedicated token is kept server-side in staging configuration and in a private `~/.search_admin_token` file with restrictive permissions. Never paste or commit it.

### Persisted defaults

| Setting | Default | Validation |
|---|---:|---|
| `semantic_candidate_limit` | 50 | Integer 10–100 |
| `lexical_result_threshold` | 20 | Integer 1–100 |
| `company.semantic_min_score` | 35 | Number 0–100 |
| `company.semantic_high_confidence_score` | 60 | Number 0–100 and greater than company minimum |
| `b2b_product.semantic_min_score` | 40 | Number 0–100 |
| `b2b_product.semantic_high_confidence_score` | 65 | Number 0–100 and greater than B2B product minimum |
| `local_product.semantic_min_score` | 10 | Number 0–100 |

These are the existing baseline defaults, not new tuned values. Do not lower thresholds or change defaults simply to increase result counts. Search score values are ranking signals, not calibrated confidence probabilities.

## 3. Search quality and readiness status

- Spelling correction uses `pyspellchecker==0.9.1` together with catalog vocabulary. Valid English words such as `monitor`, `measure`, and `making` are preserved even when absent from the product vocabulary; genuine misspellings such as `chiar` can still be corrected to `chair`.
- Last user-verified Search AI readiness: database ready; embedding model ready; B2B company index count `36`; B2B product index count `27`; local-product index count `0` with status `ready_empty`.
- `ready_empty` means the local Products source is empty and the empty index has been built successfully. It does not mean the index build failed.
- Search relevance/model tuning is deliberately postponed. Product catalog metadata quality and semantic score evaluation remain separate future tasks.

## 4. Search AI Admin frontend

### Source prepared on 9 November 2026

The updated `search-ai-admin/` folder includes:

- Search Playground: query, type, page size, results, score/match type, matched keyword, pagination, recent session searches, and raw JSON.
- Search Rules: reads the persisted settings, edits all seven settings fields, uses expected-version updates, displays change history, and can load historical settings into the form to save as a new version.
- Index Status: displays database/model readiness and counts/status for each Search AI index.
- Feedback is intentionally marked as later because there is no backend feedback API.

The Vite development proxy attaches `SEARCH_ADMIN_API_TOKEN` server-side only to `/api/admin/` requests. The token is not a `VITE_` variable and is not included in browser code. `.env.local` is gitignored. See `search-ai-admin/README.md` for Windows setup using an SSH tunnel.

**Validation status:** TypeScript/TSX syntax transpilation and a strict-null static check with local type stubs passed. Full `npm install`, project typecheck/build, browser interaction, and staging UI smoke tests have not yet been run in the user's Windows checkout. An `npm install` attempt in the artifact environment timed out; do not describe the new UI source as build-verified until the local commands pass.

### Local staging connection

Keep this SSH tunnel running while using the local UI:

```bash
ssh -N -L 8002:127.0.0.1:8002 contabo
```

Set these local `search-ai-admin/.env.local` values:

```dotenv
VITE_SEARCH_API_BASE_URL=/search-api
SEARCH_AI_TARGET=http://127.0.0.1:8002
VITE_BASE_PATH=./
SEARCH_ADMIN_API_TOKEN=<existing staging admin token>
```

Do not use `VITE_SEARCH_ADMIN_API_TOKEN`. Do not paste the token into chat or commit `.env.local`.

## 5. Security and deployment boundaries

- For local work, the Vite proxy makes browser requests same-origin and forwards through the SSH tunnel. The browser does not hold the admin token.
- Search AI source config uses CORS with `allow_credentials=false`; the effective staging `ALLOWED_ORIGINS` value has not been independently checked yet. CORS is not an authentication mechanism.
- The Vite proxy is not included in a static production build. A hosted Admin UI still requires a BONC-approved route/gateway to port `8002`, prefix stripping for `/search-ai/`, and approved admin authentication (prefer company SSO/gateway). Do not publish the admin UI with a browser-side token or expose port `8002` directly to the internet.
- Keep `/api/internal/...` index-refresh functionality private; it is not a browser-facing admin action.
- `GET /api/debug/index-status` is unauthenticated at the application route. Keep it behind the private/staging boundary or protect it at the gateway before public exposure.
- No production service, production DB, model weights, or current search thresholds were changed for this Admin UI work.

## 6. Deferred Local Product integration test

The staging Local Product save → index refresh → search → delete test was explicitly paused by the user to prioritize the Admin frontend.

What happened:

- The temporary `POST http://127.0.0.1:8003/api/products` test request returned HTTP `500` with `OperationalError` logged by BrochureIQ during the database insert path.
- A subsequent `GET /api/products` did not find the temporary product, consistent with the failed insert transaction not saving the record.
- The Search AI local-product index refresh/search/delete path was not tested end to end.

Do not retry the insert or continue diagnosing it until the user asks to resume. This is a BrochureIQ staging save-path issue observed during the attempted test; it is not evidence that Search AI semantic retrieval itself failed.

## 7. Next actions

1. Copy the updated `search-ai-admin/` folder into the local staging repository and follow its README.
2. With the SSH tunnel running, execute `npm install`, `npm run typecheck`, and `npm run build` in `search-ai-admin/`.
3. Smoke-test the local UI: readiness, one search query, settings GET, history GET, and index status. Do not change the active defaults unless there is a deliberate reason.
4. Decide with BONC who owns the hosted admin shell and authentication before adding public Nginx routes.
5. Resume the Local Product save/refresh/delete integration test later, as explicitly requested by the user.
6. Verify the effective staging CORS allow-list before any hosted browser integration.

Do not touch production, change the model, tune thresholds, or perform another Local Product insert as part of the immediate frontend task.
