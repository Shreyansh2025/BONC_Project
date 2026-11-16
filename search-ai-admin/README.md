# BONC Search AI Admin

A standalone React + TypeScript + Vite interface for the Search AI staging service. It is separate from the BrochureIQ `react-frontend/` application.

## Included screens

- **Search Playground** — calls `POST /api/search`, displays results, scores, pagination, query history, and raw JSON.
- **Search rules** — reads and updates the persisted Search AI settings, with optimistic version checks and an audit-history list.
- **Index status** — reads `/health/ready` and displays database, model, and per-index state/counts.
- **Search engines** — create independent engine configurations, connect reusable SQL Server profiles or use push ingestion, map fields, configure lexical/semantic ranking, test queries, and collect relevance labels with configuration/index context.

## Local staging setup (Windows)

Use a private SSH tunnel so the browser does not need direct access to the VPS service or its admin token.

1. Open a terminal and keep this tunnel running:

   ```bash
   ssh -N -L 8002:127.0.0.1:8002 contabo
   ```

2. In a second terminal, obtain the existing staging admin token privately from Contabo:

   ```bash
   ssh contabo
   cat ~/.search_admin_token
   ```

   Do not paste the token into chat, source code, or Git. Keep it private.

3. In the local repository, open `search-ai-admin/`, copy `.env.example` to `.env.local`, and set `SEARCH_ADMIN_API_TOKEN` to that existing token. Keep these other values:

   ```dotenv
   VITE_SEARCH_API_BASE_URL=/search-api
   SEARCH_AI_TARGET=http://127.0.0.1:8002
   VITE_BASE_PATH=./
   ```

4. Install and run:

   ```bash
   npm install
   npm run typecheck
   npm run build
   npm run dev
   ```

5. Open `http://127.0.0.1:5174`.

The Vite proxy forwards requests through the SSH tunnel. It adds `Authorization: Bearer ...` only to `/api/admin/...` upstream requests. The token is read by the Vite server from `SEARCH_ADMIN_API_TOKEN`; it is not a `VITE_` variable and is not included in the browser bundle. `.env.local` is ignored by Git.

## Preview

After a successful build, `npm run preview` serves the build at `http://127.0.0.1:4174`. The preview server has the same local-only proxy configuration.

## Deployment boundary

The Vite proxy is for local development/preview only; it is not included in the static build. Do not expose port `8002` or deploy this UI publicly with a browser-side admin token. A hosted deployment needs an approved same-origin reverse-proxy/API-gateway route to Search AI, plus BONC-approved administrator authentication (for example, the company's SSO/gateway). The public `/search-ai/` route must be explicitly mapped to `127.0.0.1:8002` with the prefix stripped. Do not expose the internal index-refresh endpoint to the browser.

## Notes

- This frontend does not call BrochureIQ on port `8003`.
- Search settings are versioned on the backend; saving creates a new version and an audit-history entry.
- Loading an older history version copies it into the form; saving then creates a new version instead of overwriting history.
- An index with status `ready_empty` is a successful empty index, not a failed build.


## Generic search platform (feature branch)

The **Search engines** workspace is a new configuration-driven platform running alongside the legacy Company/Product search. It does not replace or modify the existing search routes. The API retains the original `categories` route names as an internal compatibility detail; in this workspace, each category record represents one independent search engine.

### Database setup

1. Confirm Search AI is connected to the staging SQL Server database.
2. Run `py-backend/sql/migrations/004_create_generic_search_platform.sql` against **RN_BoncNetwork_Staging only**. The migration contains a database-name safety check and creates:
   - `searchai.SearchCategories` — category configuration and versions.
   - `searchai.SearchCategoryHistory` — immutable configuration snapshots.
   - `searchai.SearchDocuments` — normalized records ingested for each category.

Do not run this staging migration against production. A production migration must be reviewed and prepared separately.

### Data source setup

Supported source types in this first increment:

- **SQL Server table/view:** an operator registers a reusable server-side profile once, using an environment variable whose name begins with `SEARCH_SOURCE_`, for example `SEARCH_SOURCE_PEOPLE`. Its value is a SQLAlchemy SQL Server URL or raw ODBC connection string and is never returned to the browser. Admins select the profile by its short name, test the connection, discover accessible schemas/tables/views/columns, and select the source table, unique ID and mapped columns in the dashboard. The same profile can be reused by multiple search engines. The source account should have read-only access to only the required tables/views. A new credential/profile still requires one-time operator setup; admins must never paste connection strings into the UI.
- **Push ingestion:** another backend can send normalized records to the protected admin ingestion endpoint.

A SQL sync reads only the configured fields, applies validated filters, and is capped by `max_records`. It replaces the category snapshot. Push ingestion upserts by document ID unless `replace_all` is set.

### Admin API

All `/api/admin/platform/*` endpoints require the same server-side `SEARCH_ADMIN_API_TOKEN` used by the existing admin API. The local Vite proxy attaches it server-side; do not put it in a `VITE_` variable.

- `GET /api/admin/platform/sources` — list configured profile names only; never returns credential values.
- `GET /api/admin/platform/sources/{profile_id}/schema` — test a profile connection and discover accessible metadata.
- `GET /api/admin/platform/categories`
- `POST /api/admin/platform/categories`
- `PUT /api/admin/platform/categories/{category_id}`
- `GET /api/admin/platform/categories/{category_id}/history`
- `POST /api/admin/platform/categories/{category_id}/sync`
- `POST /api/admin/platform/categories/{category_id}/ingest`
- `POST /api/admin/platform/search` (private admin testing)

Public search is separate at `POST /api/platform/search`. It returns results only when a category is explicitly marked `public_search_enabled`. New categories default to private; do not enable public people search until BONC authorization and visibility rules are verified.

### Current limitations

- The first connector supports SQL Server tables/views through operator-registered reusable connection profiles and normalized push ingestion; it is not a universal connector for every database or API. Secret-manager-backed self-service credential creation is not included.
- Query understanding currently provides configurable field-value/alias hints. It is not a full NLP entity extraction or intent-classification model.
- Field weighting, lexical matching and the shared SentenceTransformer semantic index are configurable. Fine-tuning, feedback collection, training jobs, model evaluation and model-version activation are not included in this increment.
- Category indexes are built lazily in each Search AI worker after the category is first searched or after its index version changes.
- No existing Company/Product pipeline has been migrated. Validate the new path independently before considering migration.
