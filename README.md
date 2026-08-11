# BrochureIQ

BrochureIQ is a brochure and catalog intelligence platform built for
[Bonc Network](https://www.boncnetwork.com/). It converts uploaded product
catalogs and brochures (PDF or image) into structured, searchable product
data using OCR and AI, and provides B2B company and product search over
Bonc Network's catalog data.

## Features

- **Brochure extraction** — upload a PDF or image catalog, select pages,
  and automatically extract structured product listings (name, model,
  category, description, price, specifications, features, and images)
  using OCR and AI.
- **Product library** — review, edit, and save extracted products to a
  searchable library.
- **Upload history** — every processed brochure is kept as a record you
  can revisit.
- **B2B search** — search Bonc Network's company directory and product
  catalog by name, category, or location, with AI-assisted matching for
  imprecise queries.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React, Vite, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI (Python) |
| Database | Microsoft SQL Server |
| OCR | Tesseract |
| AI | Groq (LLM), sentence-transformers + FAISS (semantic search) |
| API contract | OpenAPI, with a generated TypeScript client |

## Project structure

```
├── react-frontend/      # React application
├── py-backend/          # FastAPI backend, database, and search engine
├── lib/api-spec/        # OpenAPI spec (source of truth for the API)
├── lib/api-client-react/ # Generated TypeScript API client
└── scripts/             # Build/deploy scripts
```

## Getting started

**Backend** — see [`py-backend/README.md`](py-backend/README.md) for full
setup instructions.

**Frontend:**
```bash
npm install
npm run dev --workspace=@workspace/brochure-app
```

**Useful root-level commands:**
```bash
npm run typecheck   # typecheck all packages
npm run build       # typecheck + build all packages
npm run codegen --workspace=@workspace/api-spec   # regenerate the API client after editing the OpenAPI spec
```

## Documentation

- [`py-backend/README.md`](py-backend/README.md) — backend setup, database
  schema, and search architecture
- `BrochureIQ-API-Reference.docx` — full API endpoint reference