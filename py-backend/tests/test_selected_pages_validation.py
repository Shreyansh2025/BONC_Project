import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routes.brochure import (
    _normalize_selected_pages,
    _validate_selected_pages,
    process,
)


class SelectedPagesValidationTests(unittest.TestCase):
    def test_missing_or_empty_value_means_all_pages(self):
        self.assertIsNone(_normalize_selected_pages(None))
        self.assertIsNone(_normalize_selected_pages(""))

    def test_valid_integer_pages_are_preserved(self):
        self.assertEqual(_normalize_selected_pages("[1, 3, 5]"), [1, 3, 5])

    def test_numeric_strings_remain_supported(self):
        self.assertEqual(_normalize_selected_pages('["1", "3"]'), [1, 3])

    def test_malformed_or_wrong_json_shape_is_rejected(self):
        for raw in ("not-json", "{}", '"1"', "null", "[]", " "):
            with self.subTest(raw=raw):
                with self.assertRaises(HTTPException) as ctx:
                    _normalize_selected_pages(raw)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_non_integer_or_non_positive_values_are_rejected(self):
        for raw in ('[0]', '[-1]', '[true]', '[1.5]', '[null]', '["x"]', '[1, "bad"]'):
            with self.subTest(raw=raw):
                with self.assertRaises(HTTPException) as ctx:
                    _normalize_selected_pages(raw)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_valid_selection_within_pdf_range_is_accepted(self):
        _validate_selected_pages([1, 3], total_pages=5)

    def test_page_number_above_pdf_range_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            _validate_selected_pages([2, 6], total_pages=5)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("1-5", ctx.exception.detail)
        self.assertIn("6", ctx.exception.detail)

    def test_absent_selection_does_not_fail_range_validation(self):
        _validate_selected_pages(None, total_pages=5)

    def test_process_rejects_malformed_pdf_selection_before_saving_upload(self):
        async def call_process():
            with patch(
                "app.routes.brochure._save_upload",
                new=AsyncMock(side_effect=AssertionError("upload should not be saved")),
            ) as save_upload:
                with self.assertRaises(HTTPException) as ctx:
                    await process(
                        file=SimpleNamespace(filename="sample.pdf"),
                        category="Medical",
                        selectedPages="not-json",
                    )
                save_upload.assert_not_awaited()
                self.assertEqual(ctx.exception.status_code, 400)

        asyncio.run(call_process())

    def test_invalid_page_selection_is_ignored_for_non_pdf_uploads(self):
        async def call_process(upload_path):
            with (
                patch(
                    "app.routes.brochure._save_upload",
                    new=AsyncMock(return_value=(upload_path, ".png")),
                ) as save_upload,
                patch(
                    "app.routes.brochure.recognize_batch",
                    new=AsyncMock(return_value=[{"pageNumber": 1, "text": "sample"}]),
                ),
                patch(
                    "app.routes.brochure.structure_text_with_ai",
                    new=AsyncMock(return_value={
                        "title": "Sample",
                        "description": "",
                        "specifications": [],
                        "additionalSections": [],
                        "products": [],
                    }),
                ),
                patch("app.routes.brochure._insert_brochure_sync", return_value=1),
            ):
                result = await process(
                    file=SimpleNamespace(filename="sample.png"),
                    category="Medical",
                    selectedPages="not-json",
                )
                save_upload.assert_awaited_once()
                self.assertEqual(result["brochureId"], "1")

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "sample.png"
            image_path.write_bytes(b"not-a-real-image-but-enough-for-this-mocked-route-test")
            asyncio.run(call_process(str(image_path)))


if __name__ == "__main__":
    unittest.main()
