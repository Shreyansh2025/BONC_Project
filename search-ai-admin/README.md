# BONC Search AI Admin

A standalone React + TypeScript + Vite interface for the Search AI staging service. It is separate from the BrochureIQ `react-frontend/` application.

## Included screens

- **Search Playground** — calls `POST /api/search`, displays results, scores, pagination, query history, and raw JSON.
- **Search rules** — reads and updates the persisted Search AI settings, with optimistic version checks and an audit-history list.
- **Index status** — reads `/health/ready` and displays database, model, and per-index state/counts.
- **Feedback** — intentionally marked as later; no feedback API has been implemented.

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
