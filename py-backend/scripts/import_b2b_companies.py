"""
One-off migration: loads Final_Master_Company_Data.csv (from the old
Bonc_Network/MySQL search engine) into SQL Server's B2BCompanies table
(see sql/schema.sql), so the merged app's /api/search endpoint has
company data to search alongside brochure products.

Run once after creating the tables (sql/schema.sql) and setting
SQL_SERVER_CONNECTION_STRING (same one the app uses):

    cd py-backend
    export SQL_SERVER_CONNECTION_STRING="mssql+pyodbc://user:pass@server/db?driver=ODBC+Driver+17+for+SQL+Server"
    python -m scripts.import_b2b_companies

Safe to re-run: it clears the table's contents each time rather than
appending duplicates. After importing, restart the server so it rebuilds
the in-memory company search index with the fresh data.
"""

from __future__ import annotations

import csv
import os
import re
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, insert

from app.db import build_engine_url

load_dotenv()

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "Final_Master_Company_Data.csv")

# Source CSV rows are keyed by a UUID BusinessId. A meaningful chunk of rows
# in this particular export are junk — stray <li> bullet fragments from a
# Description field that leaked out into their own CSV row (e.g. a row whose
# only value is "Competitive Market Pricing"). Requiring BusinessId to
# actually look like a UUID filters these out reliably.
_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)

# Maps the CSV's original column names to the B2BCompanies table columns.
FIELD_MAP = {
    "BusinessId": "BusinessId",
    "BusinessName": "BusinessName",
    "CategoryId": "CategoryId",
    "BusinessDescription": "BusinessDescription",
    "BusinessSlug": "BusinessSlug",
    "Tagline": "Tagline",
    "AboutBrief": "AboutBrief",
    "Description": "Description",
    "Vision": "Vision",
    "WhyChooseUs": "WhyChooseUs",
    "Address1": "Address1",
    "City": "City",
    "State": "State",
    "Country": "Country",
    "Pincode": "Pincode",
    "Landmark": "Landmark",
}


def load_rows(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        skipped = 0
        for raw_row in reader:
            business_id = (raw_row.get("BusinessId") or "").strip()
            if not _UUID_RE.match(business_id):
                skipped += 1
                continue

            row = {}
            for csv_col, sql_col in FIELD_MAP.items():
                value = raw_row.get(csv_col)
                row[sql_col] = value.strip() if isinstance(value, str) else value
            rows.append(row)

        if skipped:
            print(f"Skipped {skipped} malformed rows (not a valid BusinessId)")
        return rows


def main() -> None:
    try:
        url = build_engine_url()
    except RuntimeError as err:
        print(str(err))
        sys.exit(1)

    rows = load_rows(CSV_PATH)
    print(f"Read {len(rows)} rows from {CSV_PATH}")

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        b2b_companies = _reflect_table(conn)
        conn.execute(delete(b2b_companies))
        if rows:
            conn.execute(insert(b2b_companies), rows)

    print(f"Imported {len(rows)} companies into the B2BCompanies table")
    engine.dispose()


def _reflect_table(conn):
    from sqlalchemy import MetaData

    metadata = MetaData()
    metadata.reflect(bind=conn, only=["B2BCompanies"])
    return metadata.tables["B2BCompanies"]


if __name__ == "__main__":
    main()
