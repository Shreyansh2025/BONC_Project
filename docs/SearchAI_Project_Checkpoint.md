# BONC Search AI — Project Checkpoint

**Last updated:** 26 October 2026  
**Purpose:** Single living handoff document for project status, technical decisions, verified behavior, known risks, and next steps. Update this after every major task. Keep it practical and moderately detailed.

---

## 1. Goal

Build a maintainable Search AI system that BONC staff can test and eventually configure through a simple admin frontend, without requiring a backend engineer for every search-rule adjustment. Keep the existing search working while improvements are evaluated gradually.

Long-term goals:
- Independent Search AI backend and independent Search AI frontend.
- A Search Playground to test real queries and inspect results.
- Configurable search rules/settings through authenticated backend APIs.
- Search feedback and a small evaluation dataset to measure quality.
- Model fine-tuning only if evaluation shows it is needed; training must be controlled, evaluated, versioned and reversible.
- Deployment should eventually be compatible with the BONC Network admin environment, but its final host/path/authentication details are not yet known.

## 2. Working style and guardrails

- Explain code in simple, incremental steps; the developer is actively learning JavaScript and React and wants to understand the implementation rather than blindly copy it.
- Make one focused change at a time and test it before moving on.
- Keep Search AI's frontend separate from the BrochureIQ frontend.
- Prefer staging for development and testing. Do not point staging at production data without explicit approval.
- Do not change production services, Nginx, database configuration, or model behavior unless the task explicitly calls for it and the change is reviewed.
- Never put database credentials or internal service tokens in browser code, commits, or documentation.
- Do not run `npm audit fix --force` casually; inspect dependency changes first.
- Keep one checkpoint document and update it after each major task. Avoid parallel overlapping project-plan documents.

## 3. Git and repository state

- GitHub repository: `Shreyansh2025/BONC_Project`
- Active working branch: `staging`
- Latest known refactor commit: `d9f6ff9` — `refactor: separate Search AI and BrochureIQ modules`
- That commit was pushed to `origin/staging` and fast-forwarded on Contabo staging.
- The user manually copied the Search AI admin frontend ZIP into a sibling folder. Frontend commit `4b3452a` (`feat: add Search AI admin playground`) now exists locally on `staging`; it has **not been confirmed pushed** to `origin/staging`.
- The user ran `npm install` in the repository root. It succeeded and reported 11 vulnerabilities (2 moderate, 9 high); the suggested `npm audit fix --force` was not run.
- Workspace commands failed because the user's local setup came from manually copying the ZIP rather than applying the patch that adds it to the root npm workspaces. For now, treat `search-ai-admin` as a standalone package and run npm commands inside that folder unless the repository workspace setup is deliberately changed.

## 4. Backend architecture and deployment state

### Existing/legacy services

- Production domain: `https://aiapi.boncnetwork.com`
- Production Nginx server block in `/etc/nginx/sites-available/boncapi` proxies to `127.0.0.1:8000`.
- Existing combined application entry point: `app.main:app`.
- Existing combined staging domain: `https://aistageapi.boncnetwork.com`, historically proxying to `127.0.0.1:8001`.

### Standalone staging services

- Checkout on Contabo: `/home/team/BONC_Project_stage`
- Backend: `/home/team/BONC_Project_stage/py-backend`
- Search AI service: `bonc-search-ai-staging.service`, port `8002`, bound to `127.0.0.1`, app `app.search_main:app`.
- BrochureIQ service: `bonc-brochureiq-staging.service`, port `8003`, bound to `127.0.0.1`, app `app.brochure_main:app`.
- BrochureIQ calls Search AI internally at `http://127.0.0.1:8002` for local-product index refresh using protected internal configuration.
- Contabo staging checkout was updated to commit `d9f6ff9`; backend compilation and all 66 tests passed.
- Both standalone services were restarted after the update and `/health/ready` returned `ready` after initialization.
- OpenAPI separation was verified on Contabo: port 8002 has `/api/search` and not `/api/preview`; port 8003 has `/api/preview` and not `/api/search`.
- Last observed staging index counts: B2B companies 36, B2B products 27, local BrochureIQ products 0. Local development previously showed a larger dataset; counts differ by environment. Do not assume production and staging have the same database contents.

### Nginx/security note — verify current state before future changes

- `/etc/nginx/sites-available/boncapi` has both production and old combined-staging server blocks.
- Production block previously had `client_max_body_size 60M` and proxied to port 8000. A proposed change to increase the body limit was not part of the Search AI admin task; do not assume it was applied.
- Basic Auth directives were present in the old staging server block (`auth_basic "Staging";` and `auth_basic_user_file /etc/nginx/.htpasswd;`). The user later removed these directives at their request. The final active Nginx configuration and exposure status were not independently rechecked in this context. Since these directives guarded the entire staging proxy, not only `/docs`, verify the current status and access policy before relying on staging being protected.
- Production was not changed as part of the code-separation deployment or Search Playground development.
- The temporary SSH tunnel is local-only: `ssh -N -L 8002:127.0.0.1:8002 contabo`. It forwards the user's local port 8002 to Contabo's loopback port 8002. Close it with Ctrl+C when finished; this does not stop systemd services.
- On 26 October, SSH and both domains temporarily timed out, then access recovered. The cause was not established; do not assert the VPS was definitely down.

## 5. Search AI model and search behavior

- Model used by Search AI: `all-MiniLM-L6-v2`, embedding size 384, loaded through Sentence Transformers.
- Current code generates embeddings and builds in-memory FAISS indexes. It does not currently fine-tune or train the embedding model on BONC data.
- Index refresh/rebuild is not model training. BrochureIQ product changes can trigger the protected local-product index refresh endpoint.
- Search is hybrid: literal/lexical matching and hard-coded ranking rules are combined with semantic candidates.
- Rule values previously identified from code at `d9f6ff9` (revalidate in source before editing):
  - Up to 50 semantic candidates.
  - Semantic search may run when lexical result count is below 20.
  - Company semantic minimum/high-confidence thresholds: 35 / 60.
  - B2B product semantic minimum/high-confidence thresholds: 40 / 65.
  - Local product semantic minimum: 10.
  - Query maximum length: 200.
  - Search endpoint default page size: 10; max 100.
  - Exact matches may receive a score of 100. `matchPercentage` is a ranking signal, not a calibrated probability.
- Company, B2B product and local product searches do not use exactly identical rules. Record them separately during the audit; do not expose a shared setting unless the behavior is genuinely shared.
- Spelling correction uses vocabulary derived from the searchable data. If a query word is absent from a particular search source's vocabulary, it can be replaced with a similar in-vocabulary word before results are generated. If the word is present, it is typically retained. Correction is therefore data-dependent and may differ among search sources/environments.
- Example observed: staging query `chair` returned a `hair` result when staging's vocabulary apparently lacked `chair`; production `chair` query returned chair products. This is consistent with vocabulary-driven correction and the larger production catalogue, but verify each source-specific path using actual responses before changing the algorithm.
- Production responses supplied by the user showed chair products for `chair` and hair-related results for `hair`. Some `hair` results appeared semantically irrelevant (e.g. skincare products), so relevance ranking remains an evaluation topic, not a reason to immediately replace the model.
- Keep the current model as baseline unless a measured evaluation demonstrates that fine-tuning or replacement is beneficial.

## 6. Standalone Search AI frontend — current state

### Location and stack

- Intended folder: `search-ai-admin/` at repository root, a sibling of `react-frontend/` and `py-backend/`.
- This is a separate React + TypeScript + Vite app. It is not Next.js.
- It must remain independent from the BrochureIQ frontend so it can later be hosted/mounted in the BONC Network admin environment.
- The frontend directory was copied manually from the generated ZIP. The workspace patch was not applied at that time; therefore don't assume the root npm workspace includes it.

### Files and responsibilities in the starter

- `search-ai-admin/src/App.tsx`: Search Playground UI, input controls, result cards, pagination, session search history, error/loading states, index status display, raw response viewer.
- `search-ai-admin/src/main.tsx`: React entry point that renders `App`.
- `search-ai-admin/src/index.css`: presentation/responsive layout.
- `search-ai-admin/src/lib/search-api.ts`: typed API interfaces and `fetch()` calls for search and readiness.
- `search-ai-admin/vite.config.ts`: Vite development server at port 5174; local proxy routes `/search-api` and `/search-ai` to the Search AI target, stripping the prefix before forwarding.
- `search-ai-admin/package.json`: standalone scripts/dependencies. Run these scripts from inside `search-ai-admin` for now.
- `search-ai-admin/.env.example`: example API target/base-path settings; no secret should be put in browser-exposed `VITE_` variables.

### Deployment clarification — important

- The Search AI Admin frontend is **currently a local development application**, normally served by Vite at `http://127.0.0.1:5174`. It is not yet deployed as a public website on Contabo or mounted inside the BONC Network website/admin panel.
- The API/backend is the part listening on port `8002` on Contabo staging. Port `8002` is **not** the frontend port.
- During local testing, the SSH tunnel `ssh -N -L 8002:127.0.0.1:8002 contabo` forwards the laptop's local port 8002 to the Contabo staging Search AI backend. The Vite proxy then forwards frontend requests through that local port.
- Future target: deploy/host the standalone frontend on Contabo and/or integrate it into the BONC Network admin website. The exact hosting location, public path, authentication/SSO and routing ownership are **not yet confirmed**. We must decide this before production-facing routing changes.
- Do not describe the admin frontend as “live on Contabo” until its built assets are actually hosted there, its public route is configured, and an end-to-end check confirms it.

### API request flow

- Search frontend calls `POST /search-api/api/search` in local development.
- Vite strips `/search-api`, forwarding the request to target `http://127.0.0.1:8002` as `/api/search`.
- When the SSH tunnel is active, local port 8002 forwards to Contabo staging port 8002. Without the tunnel, `127.0.0.1:8002` means a service on the user's own computer and will not reach Contabo.
- Frontend readiness check calls `/health/ready` through the same proxy.
- `/search-api` is a Vite frontend development proxy prefix. It is not a new FastAPI route.
- Production/hosted reverse-proxy routing for the future admin frontend has not been implemented or confirmed. A Vite dev proxy does not survive as a production proxy automatically.

### Implemented in the starter

- Search Playground for all/business/product types.
- Page size selection, result pagination, sample queries and recent queries for the current session.
- Result display with available title, category, location, description, match type/keyword and score fields.
- Raw JSON response inspection.
- Search AI readiness/index-count display using existing health API.

### Not implemented yet

- Editable rules or persistent settings API.
- Feedback persistence or labelled evaluation set.
- Model training/fine-tuning, model versioning or rollback tooling.
- Dedicated admin authentication/authorization.
- Production route/deployment configuration for the new frontend.
- **Build verification (26 October 2026, output supplied by user):** `npm run typecheck` passed with no TypeScript errors. `npm run build` passed using Vite 7.3.7: 30 modules transformed; `dist/index.html` (0.56 kB), CSS (14.65 kB), and JS (199.46 kB) generated. This verifies type-checking and production bundling after the manual ZIP copy. It does not by itself verify every UI interaction, error state, or deployment configuration.

## 7. Frontend vs backend responsibilities

- Frontend: collect search input, call APIs, show results/status, and later present validated controls and feedback forms.
- Backend: own the actual search logic/model/index, validate and authorize settings changes, persist settings, apply them consistently, record audit history, and handle index refreshes.
- Browser code must never connect directly to SQL Server or contain the internal Search AI API token.
- Frontend rule changes are not model training. Fine-tuning, if ever justified, should run offline/in a controlled job, be evaluated against a baseline, versioned and approved before switching the live model.

## 8. Known issues / risks to track

1. Hard-coded ranking thresholds mean the future UI cannot change them until backend configuration support is implemented.
2. Manually assigned lexical scores and semantic similarity scores may not be calibrated consistently; ranking quality must be measured.
3. There is no comprehensive labelled relevance dataset yet.
4. Vocabulary-based spelling correction can turn a term into another word that exists in a small dataset (e.g. `chair` → `hair` in staging); collect examples before deciding on a fix.
5. Staging currently has only 36 companies and 27 B2B products in the observed health response, with zero local products. Use staging data for safety and basic tests; request an approved production-like snapshot for representative evaluation rather than connecting staging directly to the live production database.
6. Admin endpoints will need authentication/authorization, validation, audit history and rollback.
7. The future admin hosting route is not known yet; keep the app standalone and avoid assuming where the company will deploy it.
8. Confirm whether the staging Basic Auth removal remains active and whether that exposure is acceptable to the server owner.
9. Root `npm install` reported 11 vulnerabilities (2 moderate, 9 high). Do not run `npm audit fix --force`; review the audit and package impact separately.

## 9. Current plan and next actions

### Completed milestone — Search Playground starter verified

**Status: completed / verified from user-reported terminal output (26 October 2026).** The user confirmed the search-query testing had already been completed and asked to move on. `npm run typecheck` passed with no TypeScript errors, and `npm run build` passed using Vite 7.3.7 (30 modules transformed; the production `dist/` bundle generated). Do not repeat the same query-smoke-test checklist unless a regression or new UI behavior needs testing.

### Frontend commit milestone — Search AI Admin committed locally

**Status: completed / verified from user-reported Git output (26 October 2026).** The frontend files were staged and committed separately as `4b3452a feat: add Search AI admin playground` on branch `staging`. The post-commit `git status --short` showed only `M react-frontend/vite.config.ts` and `?? search-ai-settings-model.patch`; neither was included in the frontend commit. The patch file is still untracked and has not been applied. The unrelated Vite config modification remains untouched. Do not use `git add .`.

### Rules audit — source review findings

**Source reviewed:** the provided `BONC_Project-staging-separated.zip` snapshot, with Search AI code under `py-backend/app/search_ai/`. This confirms the code shape in that snapshot, not the live Contabo commit at the time of this audit. Recheck `git rev-parse HEAD` on staging before implementation; the previously recorded code baseline is `d9f6ff9`.

| Rule | Current behavior/default in reviewed source | Decision for settings UI |
|---|---|---|
| Semantic search trigger | Company, B2B product and local product search each run FAISS when lexical results are fewer than `20` | Candidate for one shared, validated setting because all three currently use the same value |
| Semantic candidates | Each FAISS search requests `k=50` | Candidate for a shared bounded setting; affects latency and recall, so evaluate before changing |
| Company semantic minimum | `35`; lower scores are discarded | Candidate source-specific setting, constrained to 0–100 |
| Company high-confidence threshold | `60`; lower-band results require a valid shared core word | Candidate source-specific setting; must remain greater than minimum |
| B2B product semantic minimum | `40` | Candidate source-specific setting, constrained to 0–100 |
| B2B product high-confidence threshold | `65`; lower-band results require a shared word | Candidate source-specific setting; must remain greater than minimum |
| BrochureIQ-local product semantic minimum | `10`; no matching high-confidence/shared-word guard in this path | Candidate setting, but keep separate from B2B product rules; do not invent a high-confidence threshold without evaluation |
| Spelling correction | `MIN_CORRECTABLE_LEN=4`; similarity cutoff `0.75`; candidate list up to `5`; max length difference `1` for query words up to 6 chars, otherwise `2`; compound pieces at least 3 chars; cache cap `5000` | Do not expose all internals in the first settings screen. First evaluate false corrections such as staging `chair` → `hair`; an enable/disable control may be considered in a later phase |
| Exact/partial scores | Hard-coded field weights vary by source. Company: exact 100; partial weights include name 90, category/industry 85, city 82, state/business type 80, country 75, description 70. B2B product: exact 100; name 90, brand 87, category/industry 85, city 82, business type 78, country 74. Local product: exact 100; name 90, model 85, category 80, description 75, specifications 70, with a position penalty/first-word boost | Keep non-editable in v1. These are numerous and their combined ranking effects need labelled evaluation before exposing them |
| Partial-match admission floor | `10` in all three paths | Do not expose initially; validate ranking impact first |
| API query/pagination limits | Search text max `200`, page defaults to `1`/`10`, page size max `100` | Keep as API safety/contract controls, not ordinary ranking sliders |
| Final combined ranking | All selected sources are merged and sorted descending by `matchPercentage` | Keep unchanged in the first settings milestone; scores from lexical and semantic layers are not calibrated probabilities |

No search-settings API or persisted settings store was found in the reviewed application code. Current behavior is driven by module-level constants. The existing `SEARCH_INTERNAL_API_TOKEN` protects BrochureIQ's internal index-refresh operation; it must **not** be reused as the admin-settings credential.

### Recommended first settings contract

Build the backend contract before creating the Search Rules UI, so the UI controls change actual validated behavior rather than local display state.

- `GET /api/admin/search-settings` — return current effective settings, a monotonically increasing version, and update metadata.
- `PUT /api/admin/search-settings` — validate the complete proposed settings object, require `expectedVersion` to prevent overwriting a newer change, atomically activate the settings, and record an audit entry. Reject the entire update when any field or relationship is invalid.
- `GET /api/admin/search-settings/history` — list recent versions/changes for traceability.
- Rollback should restore a prior version through the same validation/apply path and create a new audit record; it must not erase history.

Initial editable values should be limited to semantic candidate count, lexical-result trigger count, and the existing per-source semantic minimum/high-confidence thresholds above. Enforce bounds and cross-field checks (for example, high-confidence threshold must exceed the minimum). Preserve every current default at first rollout. No tuning should happen until a labelled query baseline exists.

### Implementation safety gates

1. Confirm the actual staging commit hash before editing, because this review used the uploaded ZIP snapshot rather than reading the live VPS.
2. Pick persistent storage before writing endpoints. A SQL Server settings/audit table is a durable option but requires an approved schema change; do not run DDL against the shared company database without approval. A file-based store is easier for a staging prototype but needs explicit path/permissions, atomic writes, backups, and single-worker/process assumptions. Do not store runtime settings in `VITE_` browser variables or commit them to Git.
3. Protect all write/history/admin routes. For a staging-only prototype, use a separate admin credential/configuration from the internal index-refresh token and keep it server-side. Never embed an admin secret in browser JavaScript. The future company-admin/SSO integration and production routing are still undecided.
4. Make each search request consume one consistent immutable settings snapshot, so an update cannot mix old and new thresholds mid-request. Keep the public `POST /api/search` request/response contract unchanged.
5. Add unit tests for validation, version conflicts, rollback/audit behavior, default-preserving behavior and the exact default search outputs before building frontend controls.
6. Apply and test on staging only. Do not alter production services, the live database schema, the model, or current defaults during the initial implementation.

### Settings schema prototype — applied, tested and committed locally (26 October 2026)

- Patch: `search-ai-settings-model.patch` was applied in the user's local Windows staging checkout.
- Adds `py-backend/app/search_ai/settings.py` with immutable Pydantic settings groups for company, B2B catalogue products and BrochureIQ-local products. Defaults match the reviewed source: candidate limit `50`, lexical trigger `20`, company `35/60`, B2B product `40/65`, local-product minimum `10`.
- Adds `py-backend/tests/test_search_settings_model.py` for defaults, bounds, relational threshold validation, unknown fields and immutability.
- User-reported local verification: focused settings tests passed **6/6**; full backend suite passed **72/72** (`Ran 72 tests ... OK`). Error/warning log messages appeared from test cases intentionally exercising failure paths; they did not fail the suite.
- The two settings-schema files were committed separately on local branch `staging` as **`5906f05 feat: add search rules settings model`**. The prior frontend commit is **`4b3452a feat: add Search AI admin playground`**. These local commits have not been confirmed pushed to GitHub, and neither has been deployed to Contabo.
- This is schema-only. It does not change live search behavior, wire settings into search functions, add API endpoints, add persistence/authentication, or touch Contabo.
- Frontend verification: user's `npm run typecheck` passed and `npm run build` succeeded with Vite 7.3.7.
- Latest user-reported `git status --short`: `M react-frontend/vite.config.ts` and `?? search-ai-settings-model.patch`. The unrelated Vite config edit remains untouched. The patch file is now an applied patch artifact and must not be staged as project source.

**Next immediate task:** design the protected settings API before implementing it. Confirm the actual staging source baseline (`git rev-parse HEAD`) when resuming, then choose persistence and authentication deliberately. Do not build the Search Rules screen until the backend endpoints can return and apply real settings. Keep production untouched.

### Before hosted deployment

- Confirm target admin URL/hosting location, allowed origin, authentication/SSO, reverse-proxy configuration and deployment owner with BONC.
- Configure the hosted API route to Search AI port 8002 intentionally; do not assume the Vite development proxy works in production.
- Perform a staging deployment and end-to-end test before any production release.

## 10. Checkpoint update procedure

After every major completed task, update this same file with:

1. Date and task completed.
2. Files/code changed.
3. Tests run and exact results.
4. Commit hash/branch if committed.
5. Current running environment/service state, if checked.
6. Decisions made and why.
7. Open issues and the single next recommended task.

Clearly mark items as **verified**, **reported by user**, **planned**, or **unknown**. Do not record assumptions as completed work. Do not include passwords, tokens, `.env` values, or other secrets.

### BrochureIQ frontend staging proxy — hostname verified from user's tests (26 October 2026)

- Scope clarification: `react-frontend/vite.config.ts` is the existing BrochureIQ frontend proxy configuration. It is separate from `search-ai-admin/vite.config.ts` for the Search AI Admin Playground.
- The user tested `https://aistageapi-staging.boncnetwork.com/health/ready`; Windows `curl.exe` returned `Could not resolve host`. That hostname did not resolve from the user's machine at the time of testing.
- The user tested `https://aistageapi.boncnetwork.com/health/ready`; Nginx answered `404 Not Found`. This was the wrong health path for the API surfaced by this hostname, not proof that the API host was down.
- Verified from the user's OpenAPI output and Swagger screenshot: `https://aistageapi.boncnetwork.com/openapi.json` returns HTTP 200 and includes `GET /api/healthz`, `POST /api/preview`, `POST /api/process`, `POST /api/remove-background`, `/api/products`, `/api/brochures`, and `/api/search`. Swagger's `GET /api/healthz` returned HTTP 200 with `{"status":"ok"}`. `GET /api/preview` returned HTTP 405 with `Allow: POST`, consistent with the preview endpoint accepting uploaded files via POST.
- Implication: for local BrochureIQ frontend development that sends requests to relative `/api/...` paths, use `https://aistageapi.boncnetwork.com` as the Vite development proxy target, not the hostname that did not resolve. Keep this change confined to the BrochureIQ frontend's staging branch/repo. It does not belong in a separate backend/webservice branch unless that branch separately needs API implementation changes.
- Important limitation: Vite's `server.proxy` is a development-server feature only; it does not route requests in a production build. Any hosted frontend deployment needs an explicit API base URL or corresponding reverse-proxy routing. Source: official Vite server options documentation, https://vite.dev/config/server-options.
- Observation/risk: this OpenAPI spec exposes both BrochureIQ routes and `/api/search`, so the host appears to be serving a combined/legacy API surface (inferred from the paths). Confirm intended service architecture with the project owner before assuming it is the isolated BrochureIQ service.
- Next step: change the BrochureIQ frontend's local Vite target to the verified hostname, restart the Vite dev server, and smoke-test preview/process/product calls from the UI. Commit only the Vite config change on the frontend staging branch after end-to-end test. Do not modify backend/webservice branch or server DNS/Nginx as part of this frontend-only change. Production remains untouched.


### Latest Git milestone — BrochureIQ staging proxy committed locally (26 October 2026)

- **Reported by user:** In the local Windows checkout, branch is `staging`; `git status` reports `nothing to commit, working tree clean`. The branch is ahead of `origin/staging` by **3 commits**.
- The BrochureIQ frontend's `react-frontend/vite.config.ts` staging proxy change was committed locally, targeting `https://aistageapi.boncnetwork.com` with `changeOrigin: true`. Production shorthand remains commented out during staging development.
- The proxy change belongs to the BrochureIQ frontend staging branch, not the separate backend/webservice branch. Vite's `server.proxy` only applies to the development server; it does not configure a production build.
- The user tested the host's OpenAPI/Swagger output: `/openapi.json` returned HTTP 200; it listed `GET /api/healthz`, `POST /api/preview`, `POST /api/process`, and other BrochureIQ endpoints. Swagger `GET /api/healthz` returned `200` with `{"status":"ok"}`. `GET /api/preview` returned `405` with `Allow: POST`, as expected for a POST-only endpoint.
- The hostname `aistageapi-staging.boncnetwork.com` did not resolve from the user's machine; do not use it unless DNS is configured.
- The working tree is clean and the standalone patch artifact no longer appears in `git status`; it was not committed as source code.
- **Not yet done/verified:** The three local commits have not been confirmed pushed to GitHub. No Contabo deployment or production change was performed as part of this local commit. The exact hash of the newest Vite-config commit was not included in the user's latest output.
- **Next decision:** If these three commits are ready to share with the team, push `staging` to `origin`; this is a Git publication step, not a production deployment. Keep the backend settings API as the next development milestone, with production left untouched.

### Search settings source audit — defaults mapped to current implementation (27 October 2026)

**Verified from user-provided source output; no code changed during this audit.** The new `py-backend/app/search_ai/settings.py` schema defaults map to the current search implementation as follows:

| Settings field | Existing behavior/source | Mapping status |
|---|---|---|
| `semantic_candidate_limit = 50` | All three semantic search calls use FAISS `state.index.search(..., k=50)` in `b2b_search.py`, `b2b_product_search.py`, and `product_search.py`. | Matches current behavior |
| `lexical_result_threshold = 20` | `MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI = 20` in all three search service modules. | Matches current behavior |
| `company.semantic_min_score = 35` | `AI_MIN_PERCENTAGE = 35.0` in `b2b_search.py`; semantic candidates below it are discarded. | Matches current behavior |
| `company.semantic_high_confidence_score = 60` | `AI_HIGH_CONFIDENCE_PERCENTAGE = 60.0` in `b2b_search.py`. | Matches current behavior |
| `b2b_product.semantic_min_score = 40` | `AI_MIN_PERCENTAGE = 40.0` in `b2b_product_search.py`. | Matches current behavior |
| `b2b_product.semantic_high_confidence_score = 65` | `AI_HIGH_CONFIDENCE_PERCENTAGE = 65.0` in `b2b_product_search.py`. | Matches current behavior |
| `local_product.semantic_min_score = 10` | `product_search.py` drops semantic matches when `percentage < 10.0`. | Matches current behavior |

**Important distinction:** `product_search.py` also has `if match_score >= 10.0` in the partial lexical-match path. This is a separate lexical-scoring threshold; it is not the same as the local-product semantic minimum setting and is not represented in `SearchRulesSettings`.

The settings model remains a passive, validated schema. It is not connected to search execution and does not change behavior, persist values, expose API endpoints, or handle administrator authentication. No settings API or storage layer has been implemented yet.

**Next step:** put this same living checkpoint inside the repository at `docs/SEARCH_AI_PROJECT_CHECKPOINT.md` (do not create a second project plan), then design the protected settings API and its persistence/authentication deliberately. Verify the actual local Git HEAD before making further code changes if the branch has moved. Production remains untouched.
