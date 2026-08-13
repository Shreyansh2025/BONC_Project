from __future__ import annotations

import os
import sys
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, delete, insert

from app.db import build_engine_url

load_dotenv()

XLSX_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "b2b_products.xlsx")

# Maps the xlsx's original column names to B2BProducts table columns.
# (Five near-empty source columns — Safety Instructions/Warnings, Product
# Shelf Life, Volume, Weight, Product Size — are >99% blank in this export
# and are skipped rather than adding mostly-null columns to the schema.)
FIELD_MAP = {
    "Unique ID": "UniqueId",
    "Industry Name": "IndustryName",
    "Category Name": "CategoryName",
    "Sub Category Name": "SubCategoryName",
    "Disposition Name": "DispositionName",
    "Product Name": "ProductName",
    "Item Type": "ItemType",
    "Product Type": "ProductType",
    "Description": "Description",
    "Manufecturer Name": "ManufacturerName",  # sic, matches source header typo
    "Brand Name": "BrandName",
    "KeyWords": "KeyWords",
    "Business Name": "BusinessName",
    "Customized Price": "CustomizedPrice",
    "Min Price": "MinPrice",
    "Max Price": "MaxPrice",
    "Export Capabilities": "ExportCapabilities",
    "Customization Availability": "CustomizationAvailability",
    "Ships Globally": "ShipsGlobally",
    "Hazardous Goods": "HazardousGoods",
    "GST Percentage": "GstPercentage",
    "Average Delivery Time": "AverageDeliveryTime",
    "Processing Time": "ProcessingTime",
    "Country Of Origin": "CountryOfOrigin",
    "Minimum Order Quantity": "MinimumOrderQuantity",
    "Status": "Status",
    "Publish Date": "PublishDate",
}

NUMERIC_COLS = {"MinPrice", "MaxPrice"}


def _clean_value(value, numeric: bool = False):
    """Convert Excel/pandas values into SQL Server-safe values."""

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if numeric:
        try:
            # Convert through string to avoid float precision problems.
            number = Decimal(str(value))

            # Reject NaN / Infinity
            if not number.is_finite():
                return None

            # SQL Server DECIMAL(18,2)
            max_value = Decimal("9999999999999999.99")

            if abs(number) > max_value:
                return None

            # Match DECIMAL(18,2)
            number = number.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )

            return number

        except (InvalidOperation, ValueError, TypeError, OverflowError):
            return None

    return str(value).strip()

def load_rows(xlsx_path: str) -> list[dict]:
    df = pd.read_excel(xlsx_path)

    rows = []
    skipped = 0
    invalid_prices = []

    for excel_row_number, raw_row in df.iterrows():
        unique_id = _clean_value(raw_row.get("Unique ID"))

        if not unique_id:
            skipped += 1
            continue

        row = {}

        for xlsx_col, sql_col in FIELD_MAP.items():
            raw_value = raw_row.get(xlsx_col)

            if sql_col in NUMERIC_COLS:
                cleaned_value = _clean_value(raw_value, numeric=True)

                # If original value existed but could not be converted,
                # report it.
                if (
                    raw_value is not None
                    and not pd.isna(raw_value)
                    and cleaned_value is None
                ):
                    invalid_prices.append({
                        "excel_row": excel_row_number + 2,
                        "unique_id": unique_id,
                        "column": sql_col,
                        "value": repr(raw_value),
                    })

                row[sql_col] = cleaned_value
            else:
                row[sql_col] = _clean_value(raw_value)

        rows.append(row)

    if skipped:
        print(f"Skipped {skipped} rows with no Unique ID")

    if invalid_prices:
        print("\nWARNING: Invalid/out-of-range prices found:")
        for item in invalid_prices:
            print(
                f"  Excel row {item['excel_row']} | "
                f"{item['unique_id']} | "
                f"{item['column']} = {item['value']}"
            )

        print(f"\nTotal invalid prices: {len(invalid_prices)}")

    return rows

def main() -> None:
    try:
        url = build_engine_url()
    except RuntimeError as err:
        print(str(err))
        sys.exit(1)

    # README documents:
    #   python -m scripts.import_b2b_products "path/to/ProductAndServiceDocuments.xlsx"
    # Honor that optional path argument instead of always silently importing
    # the bundled sample file — otherwise pointing this at a real catalog
    # export has no effect and the sample data (or nothing) gets imported.
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else XLSX_PATH
    if not os.path.isfile(xlsx_path):
        print(f"File not found: {xlsx_path}")
        sys.exit(1)

    rows = load_rows(xlsx_path)
    print(f"Read {len(rows)} rows from {xlsx_path}")

    engine = create_engine(url, future=True)

    with engine.begin() as conn:
        metadata = MetaData()
        metadata.reflect(bind=conn, only=["B2BProducts"])
        b2b_products = metadata.tables["B2BProducts"]

        conn.execute(delete(b2b_products))

        # Insert one row at a time to find the problematic row.
        for index, row in enumerate(rows, start=1):
            try:
                conn.execute(insert(b2b_products), row)
            except Exception as err:
                print("\n========================================")
                print(f"FAILED ROW: {index}")
                print(f"UniqueId: {row.get('UniqueId')}")
                print("========================================")

                for key, value in row.items():
                    print(f"{key}: {repr(value)} ({type(value).__name__})")

                print("\nERROR:")
                print(err)

                raise

    print(f"Imported {len(rows)} products into the B2BProducts table")
    engine.dispose()

if __name__ == "__main__":
    main()