"""Regression tests for B2B product-detail lookup serialization."""

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import Column, Integer, MetaData, String, Table

# The full production environment installs these model dependencies. Keep the
# route-level regression test runnable in lightweight CI environments where
# inference libraries are intentionally omitted; this test never calls them.
def _install_optional_model_stubs() -> None:
    try:
        has_onnxruntime = importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        has_onnxruntime = "onnxruntime" in sys.modules
    if not has_onnxruntime:
        ort_stub = types.ModuleType("onnxruntime")

        class SessionOptions:
            intra_op_num_threads = 0
            inter_op_num_threads = 0

        ort_stub.SessionOptions = SessionOptions
        sys.modules["onnxruntime"] = ort_stub

    try:
        has_rembg = importlib.util.find_spec("rembg") is not None
    except (ImportError, ValueError):
        has_rembg = "rembg" in sys.modules
    if not has_rembg:
        rembg_stub = types.ModuleType("rembg")
        rembg_stub.new_session = lambda *args, **kwargs: object()
        rembg_stub.remove = lambda *args, **kwargs: b""
        sys.modules["rembg"] = rembg_stub


_install_optional_model_stubs()

from app.routes import brochure as brochure_routes  # noqa: E402
from app.utils.serialize import serialize_b2b_product_row, serialize_row  # noqa: E402


_METADATA = MetaData()
_LOCAL_PRODUCTS = Table(
    "Products", _METADATA, Column("Id", Integer), Column("Slug", String(500))
)
_B2B_PRODUCTS = Table(
    "B2BProducts", _METADATA, Column("Id", Integer), Column("Slug", String(500))
)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeConnection:
    def __init__(self, b2b_row):
        self.b2b_row = b2b_row
        self.queried_tables = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        table_name = statement.get_final_froms()[0].name
        self.queried_tables.append(table_name)
        if table_name == "Products":
            return _FakeResult(None)
        if table_name == "B2BProducts":
            return _FakeResult(self.b2b_row)
        raise AssertionError(f"Unexpected table queried: {table_name}")


class _FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


class B2BProductSerializationTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "Id": 27,
            "UniqueId": "PRD1778152482530",
            "IndustryName": "Energy",
            "CategoryName": "Solar Panels",
            "SubCategoryName": "Mono PERC",
            "DispositionName": None,
            "ProductName": "Solar Panel 550W",
            "ItemType": "Product",
            "ProductType": "Physical",
            "Description": "High-efficiency solar panel",
            "ManufacturerName": "Example Manufacturing",
            "BrandName": "Sunrise",
            "KeyWords": "solar,panel,550W",
            "BusinessName": "Example Supplier",
            "CustomizedPrice": "Contact for price",
            "MinPrice": Decimal("120.50"),
            "MaxPrice": Decimal("155.75"),
            "ExportCapabilities": "Yes",
            "CustomizationAvailability": "Available",
            "ShipsGlobally": "Yes",
            "HazardousGoods": "No",
            "GstPercentage": "18",
            "AverageDeliveryTime": "7 days",
            "ProcessingTime": "2 days",
            "CountryOfOrigin": "India",
            "MinimumOrderQuantity": "10",
            "Status": "Publish",
            "PublishDate": "2026-09-18",
            "Slug": "solar-panel-550w",
            "ImagePath": "/api/uploads/solar-panel.jpg",
        }

    def test_b2b_serializer_maps_actual_schema_columns_to_camel_case(self):
        result = serialize_b2b_product_row(self.row)

        self.assertEqual(result["_id"], 27)
        self.assertEqual(result["productUniqueId"], "PRD1778152482530")
        self.assertEqual(result["productName"], "Solar Panel 550W")
        self.assertEqual(result["categoryName"], "Solar Panels")
        self.assertEqual(result["category"], "Solar Panels")
        self.assertEqual(result["price"], "Contact for price")
        self.assertEqual(result["customizedPrice"], "Contact for price")
        self.assertEqual(result["minPrice"], 120.5)
        self.assertEqual(result["maxPrice"], 155.75)
        self.assertEqual(result["imagePath"], "/api/uploads/solar-panel.jpg")
        self.assertEqual(result["slug"], "solar-panel-550w")
        self.assertNotIn("ProductName", result)

    def test_product_detail_lookup_returns_b2b_product_instead_of_key_error(self):
        connection = _FakeConnection(self.row)
        with (
            patch.object(brochure_routes, "get_engine", return_value=_FakeEngine(connection)),
            patch.object(brochure_routes, "products_table", return_value=_LOCAL_PRODUCTS),
            patch.object(brochure_routes, "b2b_products_table", return_value=_B2B_PRODUCTS),
        ):
            result = brochure_routes._get_product_sync("solar-panel-550w")

        self.assertEqual(connection.queried_tables, ["Products", "B2BProducts"])
        self.assertIsNotNone(result)
        self.assertEqual(result["productName"], "Solar Panel 550W")
        self.assertEqual(result["category"], "Solar Panels")
        self.assertEqual(result["_id"], 27)

    def test_existing_products_and_brochures_serializers_keep_their_shape(self):
        local_product = serialize_row(
            "Products",
            {"Id": 4, "ProductName": "Local Item", "Category": "Medical", "Images": '["/x.jpg"]'},
        )
        brochure = serialize_row(
            "Brochures",
            {"Id": 8, "FileName": "catalog.pdf", "Specifications": '["A"]'},
        )

        self.assertEqual(local_product, {
            "_id": 4,
            "productName": "Local Item",
            "category": "Medical",
            "images": ["/x.jpg"],
        })
        self.assertEqual(brochure, {
            "_id": 8,
            "fileName": "catalog.pdf",
            "specifications": ["A"],
        })

    def test_datetime_values_are_json_compatible(self):
        row = {"Id": 1, "PublishDate": datetime(2026, 10, 18, tzinfo=timezone.utc)}
        result = serialize_b2b_product_row(row)
        self.assertEqual(result["publishDate"], "2026-10-18T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
