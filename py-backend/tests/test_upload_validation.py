"""Regression tests for Batch 1 upload and resource-limit validation."""

import asyncio
import importlib.util
import json
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image
import fitz


def _install_optional_model_stubs() -> None:
    """Allow validation-only tests to run without the ONNX/rembg models."""
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

from app.routes.brochure import (  # noqa: E402
    MAX_IMAGE_DIMENSION,
    MAX_PDF_PAGES,
    MAX_RENDERED_PAGE_DIMENSION,
    _read_limited_image_upload,
    _save_upload,
    process,
    _validate_uploaded_file,
)


class FakeUpload:
    def __init__(self, filename, chunks):
        self.filename = filename
        self._chunks = list(chunks)

    async def read(self, size=-1):
        return self._chunks.pop(0) if self._chunks else b""


class UploadValidationTests(unittest.TestCase):
    def test_valid_png_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "valid.png"
            Image.new("RGB", (20, 20), "white").save(path, format="PNG")
            _validate_uploaded_file(str(path), ".png")

    def test_extension_must_match_actual_image_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "image.jpg"
            Image.new("RGB", (20, 20), "white").save(path, format="PNG")
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".jpg")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("does not match", ctx.exception.detail)

    def test_corrupted_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.png"
            path.write_bytes(b"this is not a PNG")
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".png")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_oversized_image_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wide.png"
            Image.new("RGB", (MAX_IMAGE_DIMENSION + 1, 1), "white").save(path, format="PNG")
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".png")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("dimensions exceed", ctx.exception.detail)

    def test_valid_pdf_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "valid.pdf"
            with fitz.open() as doc:
                doc.new_page(width=612, height=792)
                doc.save(path)
            _validate_uploaded_file(str(path), ".pdf")

    def test_corrupted_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.pdf"
            path.write_bytes(b"not a PDF")
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".pdf")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("invalid or corrupted", ctx.exception.detail)

    def test_pdf_page_count_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "many-pages.pdf"
            with fitz.open() as doc:
                for _ in range(MAX_PDF_PAGES + 1):
                    doc.new_page(width=100, height=100)
                doc.save(path)
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".pdf")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn(str(MAX_PDF_PAGES), ctx.exception.detail)

    def test_pdf_page_render_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "oversized-page.pdf"
            with fitz.open() as doc:
                doc.new_page(width=MAX_RENDERED_PAGE_DIMENSION, height=100)
                doc.save(path)
            with self.assertRaises(HTTPException) as ctx:
                _validate_uploaded_file(str(path), ".pdf")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("rendering size", ctx.exception.detail)

    def test_save_upload_removes_corrupted_file_after_validation_failure(self):
        async def run_test(temp_dir):
            with patch("app.routes.brochure.UPLOADS_DIR", temp_dir):
                with self.assertRaises(HTTPException) as ctx:
                    await _save_upload(FakeUpload("bad.png", [b"not a PNG"]))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(run_test(temp_dir))

    def test_save_upload_enforces_size_cap_and_removes_partial_file(self):
        async def run_test(temp_dir):
            with (
                patch("app.routes.brochure.UPLOADS_DIR", temp_dir),
                patch("app.routes.brochure.MAX_FILE_SIZE", 3),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await _save_upload(FakeUpload("too-large.png", [b"1234"]))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("too large", ctx.exception.detail)
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(run_test(temp_dir))

    def test_save_upload_accepts_valid_image_and_returns_path_and_extension(self):
        async def run_test(temp_dir, data):
            with patch("app.routes.brochure.UPLOADS_DIR", temp_dir):
                path, ext = await _save_upload(FakeUpload("valid.png", [data]))
            self.assertEqual(ext, ".png")
            self.assertTrue(Path(path).is_file())

        with tempfile.TemporaryDirectory() as temp_dir:
            image_buffer = io.BytesIO()
            Image.new("RGB", (12, 12), "white").save(image_buffer, format="PNG")
            asyncio.run(run_test(temp_dir, image_buffer.getvalue()))

    def test_process_does_not_return_raw_internal_exception_details(self):
        async def run_test(image_path):
            with (
                patch(
                    "app.routes.brochure._save_upload",
                    new=AsyncMock(return_value=(image_path, ".png")),
                ),
                patch(
                    "app.routes.brochure.recognize_batch",
                    new=AsyncMock(side_effect=RuntimeError("internal-secret-value")),
                ),
            ):
                response = await process(
                    file=FakeUpload("valid.png", []),
                    category="Medical",
                    selectedPages=None,
                )
            self.assertEqual(response.status_code, 500)
            body = json.loads(response.body)
            self.assertEqual(body["message"], "Processing failed")
            self.assertEqual(body["error"], "Internal processing error")
            self.assertNotIn("internal-secret-value", response.body.decode("utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "valid.png"
            Image.new("RGB", (12, 12), "white").save(image_path, format="PNG")
            asyncio.run(run_test(str(image_path)))

    def test_remove_background_upload_rejects_pdf_extension(self):
        async def run_test():
            with self.assertRaises(HTTPException) as ctx:
                await _read_limited_image_upload(FakeUpload("brochure.pdf", [b"data"]))
            self.assertEqual(ctx.exception.status_code, 400)

        asyncio.run(run_test())

    def test_remove_background_upload_applies_size_limit(self):
        async def run_test():
            with patch("app.routes.brochure.MAX_FILE_SIZE", 3):
                with self.assertRaises(HTTPException) as ctx:
                    await _read_limited_image_upload(FakeUpload("image.png", [b"1234"]))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("too large", ctx.exception.detail)

        asyncio.run(run_test())

    def test_remove_background_upload_checks_real_image_content(self):
        async def run_test():
            with self.assertRaises(HTTPException) as ctx:
                await _read_limited_image_upload(FakeUpload("image.png", [b"not a PNG"]))
            self.assertEqual(ctx.exception.status_code, 400)

        asyncio.run(run_test())

    def test_remove_background_upload_accepts_valid_png(self):
        async def run_test():
            image_buffer = io.BytesIO()
            Image.new("RGB", (12, 12), "white").save(image_buffer, format="PNG")
            data = image_buffer.getvalue()
            actual = await _read_limited_image_upload(FakeUpload("image.png", [data]))
            self.assertEqual(actual, data)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
