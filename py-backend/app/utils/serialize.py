"""
SQL Server rows are PascalCase columns with JSON-encoded text for anything
that used to be a native list/dict in MongoDB (SQL Server has no array
column type). This module converts rows to/from the same camelCase JSON
shape the frontend already expects, so no frontend changes were needed for
the Mongo -> SQL Server migration.
"""

import json
from datetime import datetime
from typing import Any, Mapping

# table name -> { sql_column: (app_field_name, kind) }
# kind: None = plain value, "list" = JSON array (default []), "dict" = JSON object (default {})
_TABLE_FIELDS: dict[str, dict[str, tuple[str, str | None]]] = {
    "Brochures": {
        "Id": ("_id", None),
        "FileName": ("fileName", None),
        "Category": ("category", None),
        "Title": ("title", None),
        "Description": ("description", None),
        "Specifications": ("specifications", "list"),
        "AdditionalSections": ("additionalSections", "list"),
        "ExtractedText": ("extractedText", None),
        "ExtractedImages": ("extractedImages", "list"),
        "UploadDate": ("uploadDate", None),
    },
    "Products": {
        "Id": ("_id", None),
        "ProductName": ("productName", None),
        "Category": ("category", None),
        "Model": ("model", None),
        "Description": ("description", None),
        "Price": ("price", None),
        "Features": ("features", "list"),
        "Specifications": ("specifications", "dict"),
        "Images": ("images", "list"),
        "ImagePath": ("imagePath", None),
        "Slug": ("slug", None),
        "SourceFileName": ("sourceFileName", None),
        "CreatedDate": ("createdDate", None),
    },
   
}


def serialize_row(table_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """SQL row -> camelCase JSON dict (same shape the frontend got from Mongo)."""
    field_map = _TABLE_FIELDS[table_name]
    out: dict[str, Any] = {}
    for sql_col, value in dict(row).items():
        if sql_col not in field_map:
            continue
        app_field, kind = field_map[sql_col]
        out[app_field] = _decode_value(value, kind)
    return out


def _decode_value(value: Any, kind: str | None) -> Any:
    if value is None:
        if kind == "list":
            return []
        if kind == "dict":
            return {}
        return None
    if kind in ("list", "dict"):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return [] if kind == "list" else {}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def json_col(value: Any) -> str:
    """Encodes a Python list/dict as JSON text for a NVARCHAR(MAX) column."""
    return json.dumps(value if value is not None else [])