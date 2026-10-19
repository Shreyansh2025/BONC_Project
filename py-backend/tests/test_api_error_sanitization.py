"""Regression tests for category configuration and public API error redaction."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


def _install_optional_model_stubs() -> None:
    """Allow route tests to run when heavyweight image runtimes aren't installed."""
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

from app.models import CATEGORIES, UpdateProductImages  # noqa: E402
from app.routes import brochure as brochure_routes  # noqa: E402


_SENSITIVE_ERROR = (
    "ODBC connection failed for server sql-private.internal; "
    "password=DO_NOT_EXPOSE; database=BONC_Production"
)


class CategoryConfigurationTests(unittest.TestCase):
    def test_general_is_a_supported_category(self):
        # The /process route uses General as its fallback. It must also be a
        # supported category so valid fallback requests don't emit a warning.
        self.assertIn("General", CATEGORIES)


class PublicApiErrorSanitizationTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_error_is_sanitized(self, handler_call, expected_message: str) -> None:
        with patch.object(
            brochure_routes.asyncio,
            "to_thread",
            new=AsyncMock(side_effect=RuntimeError(_SENSITIVE_ERROR)),
        ):
            response = await handler_call

        self.assertEqual(response.status_code, 500)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(payload["message"], expected_message)
        self.assertEqual(payload["error"], "Internal server error")
        serialized = json.dumps(payload)
        self.assertNotIn("sql-private.internal", serialized)
        self.assertNotIn("DO_NOT_EXPOSE", serialized)
        self.assertNotIn("BONC_Production", serialized)

    async def test_list_brochures_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.list_brochures(), "Error fetching brochures"
        )

    async def test_delete_brochure_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.delete_brochure("123"), "Error deleting brochure"
        )

    async def test_list_products_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.list_products(), "Error fetching products"
        )

    async def test_get_product_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.get_product("sample-slug"), "Error fetching product"
        )

    async def test_save_products_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.save_products(
                {"products": [{"productName": "Sample", "category": "General"}]}
            ),
            "Error saving products",
        )

    async def test_update_product_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.update_product_images(
                "123", UpdateProductImages(images=["/api/uploads/sample.jpg"])
            ),
            "Error updating product images",
        )

    async def test_delete_product_hides_database_exception_details(self):
        await self._assert_error_is_sanitized(
            brochure_routes.delete_product("123"), "Error deleting product"
        )


if __name__ == "__main__":
    unittest.main()
