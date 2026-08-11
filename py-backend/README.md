# BrochureIQ Backend

FastAPI backend for BrochureIQ. Handles brochure/catalog OCR and AI
extraction, product storage, and B2B company + product search.

## Tech stack

FastAPI, SQL Server (SQLAlchemy + pyodbc), PyMuPDF (PDF rendering),
Pillow, Tesseract OCR (pytesseract), Groq (AI extraction),
sentence-transformers + FAISS (semantic search).

## Setup

**Prerequisites:**
- Python 3.11+
- SQL Server (local, or a connection string to a remote instance)
- [Microsoft ODBC Driver 17/18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
- Tesseract OCR (`sudo apt-get install tesseract-ocr` / `brew install tesseract`)

**Install and run:**
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in the values below
python -m app.main
```

Server runs on the port set by `PORT` in `.env` (default `8000`). Check it's
up at `GET /api/healthz`.

## Environment variables

| Variable | Description |
|---|---|
| `APP_ENV` | `local`, `staging`, or `production` — selects which connection string below is active |
| `SQL_SERVER_ODBC_CONNECT_LOCAL` / `_STAGING` / `_PRODUCTION` | SQL Server connection string for each environment |
| `GROQ_API_KEY` | API key for AI-powered product extraction (falls back to a simpler parser if not set) |
| `UPLOADS_DIR` | Where uploaded files and generated images are stored (defaults to `./uploads`) |

Example connection string:
```
SQL_SERVER_ODBC_CONNECT_LOCAL=DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost\SQLEXPRESS;DATABASE=BrochureApp;Trusted_Connection=yes;TrustServerCertificate=yes;
```

## Database

Run `sql/schema.sql` against your database once to create the tables. For
databases that already have data, use the incremental scripts in
`sql/migrations/` instead.

| Table | Description |
|---|---|
| `Brochures` | One row per uploaded/processed brochure |
| `Products` | Products extracted from brochures and saved to the library |
| `B2BCompanies` | Bonc Network's company directory |
| `B2BProducts` | Bonc Network's product catalog |

Full column-level reference is in `sql/schema.sql`.

## One-time data imports

Populate the B2B search tables from source data:
```bash
python -m scripts.import_b2b_companies
python -m scripts.import_b2b_products "path/to/ProductAndServiceDocuments.xlsx"
```

## Search

Two independent search endpoints, backed by an in-memory index (exact
match → keyword match → AI similarity fallback) built from the tables
above at startup:

- `GET /api/search/companies?q=...`
- `GET /api/search/products?q=...`

See `BrochureIQ-API-Reference.docx` (project root) for the full API
reference, including the brochure extraction and product library endpoints.

## Project structure

```
app/
├── main.py            # App entrypoint
├── config.py           # Environment configuration
├── db.py                # Database connection
├── models.py            # Request/response schemas
├── routes/               # API endpoints
└── utils/                 # OCR, AI extraction, image processing, search
sql/
├── schema.sql            # Table definitions
└── migrations/            # Incremental schema changes
scripts/                    # Data import scripts
```