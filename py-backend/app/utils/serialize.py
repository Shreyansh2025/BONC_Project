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


# Dedicated serializer for the imported B2B catalog. Keep this separate from
# _TABLE_FIELDS so the existing Brochures and local Products response shapes
# remain unchanged. Column names match B2BProducts in sql/schema.sql.
_B2B_PRODUCT_FIELDS: dict[str, str] = {
    "Id": "_id",
    "UniqueId": "productUniqueId",
    "IndustryName": "industryName",
    "CategoryName": "categoryName",
    "SubCategoryName": "subCategoryName",
    "DispositionName": "dispositionName",
    "ProductName": "productName",
    "ItemType": "itemType",
    "ProductType": "productType",
    "Description": "description",
    "ManufacturerName": "manufacturerName",
    "BrandName": "brandName",
    "KeyWords": "keyWords",
    "BusinessName": "businessName",
    "CustomizedPrice": "customizedPrice",
    "MinPrice": "minPrice",
    "MaxPrice": "maxPrice",
    "ExportCapabilities": "exportCapabilities",
    "CustomizationAvailability": "customizationAvailability",
    "ShipsGlobally": "shipsGlobally",
    "HazardousGoods": "hazardousGoods",
    "GstPercentage": "gstPercentage",
    "AverageDeliveryTime": "averageDeliveryTime",
    "ProcessingTime": "processingTime",
    "CountryOfOrigin": "countryOfOrigin",
    "MinimumOrderQuantity": "minimumOrderQuantity",
    "Status": "status",
    "PublishDate": "publishDate",
    "Slug": "slug",
    "ImagePath": "imagePath",
}


def serialize_b2b_product_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a B2BProducts SQL row to the API's camelCase product shape.

    The B2B catalog has different columns from brochure-extracted Products,
    so it must not be passed to serialize_row("Products", ...). The common
    ``category`` and ``price`` aliases support the existing product-detail
    shape, while the catalog-specific fields (categoryName, minPrice, etc.)
    remain available to clients that use the Search AI result schema.
    """
    source = dict(row)
    result: dict[str, Any] = {}

    for sql_column, api_field in _B2B_PRODUCT_FIELDS.items():
        if sql_column not in source:
            continue
        value = _decode_value(source[sql_column], None)
        if sql_column in ("MinPrice", "MaxPrice") and value is not None:
            # Search AI's existing result shape exposes these SQL DECIMALs as
            # JSON numbers, not Decimal objects.
            value = float(value)
        result[api_field] = value

    # The test product-detail view's common shape uses category/price, whereas
    # B2B search results use categoryName/customizedPrice. Return both names
    # without altering the existing Brochures/Products serializers.
    if "categoryName" in result:
        result["category"] = result["categoryName"]
    if "customizedPrice" in result:
        result["price"] = result["customizedPrice"]

    return result


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