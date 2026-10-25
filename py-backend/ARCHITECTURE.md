# Backend Service Separation

## Active standalone services

- Search AI launcher: `app.search_main:app`. Its implementation modules are under `app/search_ai/`.
- BrochureIQ launcher: `app.brochure_main:app`. Its implementation modules are under `app/brochureiq/`.
- Legacy combined launcher: `app.main:app`. It remains present as a rollback path and registers both search and brochure routes.

## Ownership

- `app/search_ai/routes/`: public search, index status, protected internal local-product index refresh.
- `app/search_ai/services/`: company, B2B product and local product indexes; embeddings and shared search ranking helpers.
- `app/brochureiq/routes/`: PDF/image preview, processing, background removal, brochure history, and local product library.
- `app/brochureiq/services/`: OCR, brochure AI structuring, image extraction, background removal, and the HTTP client that requests Search AI index refreshes after BrochureIQ product writes commit.
- `app/shared/`: SQL Server connectivity, logging and JSON/row serialization used by both services.

## Compatibility

The old `app/routes/*`, `app/utils/*`, `app.db`, `app.logger`, `app.config`, and `app.models` paths remain as import shims. They are not separate copies of the implementation. They are kept for the old combined entry point and existing tests. Do not delete them until the combined app is formally retired and downstream imports have been checked.

## Scope and deployment

This organization change does not change route paths, payloads, SQL table ownership, service ports, Nginx, systemd units, or production configuration. The uploaded archive does not include Git metadata, so Git commits must be created from a repository clone checked out to the intended branch (`staging`).
