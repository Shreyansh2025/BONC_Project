"""Regression tests for Batch 2 OCR/AI failure handling and artifact cleanup."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routes import brochure as brochure_routes  # noqa: E402
from app.utils import ai_structure, image_extractor, ocr_pool  # noqa: E402


class OCRPartialFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_page_failure_preserves_other_page_results(self):
        def fake_recognize(buffer: bytes) -> str:
            if buffer == b"bad":
                raise ValueError("OCR failure with detail that should not be logged")
            return f"text-{buffer.decode()}"

        with ThreadPoolExecutor(max_workers=2) as executor:
            with (
                patch.object(ocr_pool, "_get_executor", return_value=executor),
                patch.object(ocr_pool, "_recognize_sync", side_effect=fake_recognize),
            ):
                result = await ocr_pool.recognize_batch(
                    [(1, b"first"), (2, b"bad"), (3, b"third")]
                )

        self.assertEqual(
            result,
            [
                {"pageNumber": 1, "text": "text-first"},
                {"pageNumber": 3, "text": "text-third"},
            ],
        )

    async def test_all_page_failures_raise_a_stable_error(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            with (
                patch.object(ocr_pool, "_get_executor", return_value=executor),
                patch.object(ocr_pool, "_recognize_sync", side_effect=ValueError("private detail")),
            ):
                with self.assertRaisesRegex(RuntimeError, r"OCR failed for all 2 page\(s\)"):
                    await ocr_pool.recognize_batch([(1, b"one"), (2, b"two")])

    async def test_empty_batch_has_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "No pages available for OCR"):
            await ocr_pool.recognize_batch([])


class GroqErrorSanitizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_does_not_include_provider_response_body(self):
        response = MagicMock()
        response.status_code = 429
        response.text = "secret-like-provider-response-body"
        client = MagicMock()
        client.post = AsyncMock(return_value=response)

        with self.assertRaisesRegex(RuntimeError, "HTTP 429") as raised:
            await ai_structure._process_chunk(client, "some OCR text", "test-key")

        self.assertNotIn("secret-like-provider-response-body", str(raised.exception))


class GeneratedArtifactCleanupTests(unittest.TestCase):
    def _mock_pdf_with_pages(self, page_count: int = 2):
        pages = [MagicMock() for _ in range(page_count)]
        document = MagicMock()
        document.__enter__.return_value = document
        document.__exit__.return_value = False
        document.page_count = page_count
        document.__iter__.return_value = iter(pages)
        document.__getitem__.side_effect = pages
        return document, pages

    def test_thumbnail_generation_removes_outputs_if_later_page_fails(self):
        document, pages = self._mock_pdf_with_pages()
        image = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as temp_dir:
            created_paths: list[str] = []
            with (
                patch.object(image_extractor.fitz, "open", return_value=document),
                patch.object(image_extractor, "_render_page", side_effect=[image, RuntimeError("render failed")]),
            ):
                with self.assertRaisesRegex(RuntimeError, "render failed"):
                    image_extractor.generate_page_thumbnails(
                        "fake.pdf", temp_dir, "/api/uploads", created_paths=created_paths
                    )
            self.assertEqual(len(created_paths), 1)
            self.assertTrue(Path(created_paths[0]).exists())
            brochure_routes._cleanup_failed_upload_artifacts(set(created_paths))
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_ocr_page_generation_removes_outputs_if_later_page_fails(self):
        document, _ = self._mock_pdf_with_pages()
        image = Image.new("RGB", (10, 10))
        with tempfile.TemporaryDirectory() as temp_dir:
            created_paths: list[str] = []
            with (
                patch.object(image_extractor.fitz, "open", return_value=document),
                patch.object(image_extractor, "_render_page", side_effect=[image, RuntimeError("render failed")]),
            ):
                with self.assertRaisesRegex(RuntimeError, "render failed"):
                    image_extractor.generate_ocr_pages(
                        "fake.pdf", temp_dir, "/api/uploads", [1, 2], created_paths=created_paths
                    )
            self.assertEqual(len(created_paths), 1)
            self.assertTrue(Path(created_paths[0]).exists())
            brochure_routes._cleanup_failed_upload_artifacts(set(created_paths))
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_embedded_image_extraction_removes_prior_files_if_later_page_fails(self):
        document, pages = self._mock_pdf_with_pages()
        pages[0].rect = SimpleNamespace(width=100, height=100)
        pages[0].get_images.return_value = [(1, 0, 100, 100, 8, 3)]
        pages[0].get_image_rects.return_value = []
        pages[1].get_images.side_effect = RuntimeError("page image table failed")
        document.extract_image.return_value = {
            "image": b"sample-image-bytes",
            "ext": "png",
            "width": 100,
            "height": 100,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            created_paths: list[str] = []
            with patch.object(image_extractor.fitz, "open", return_value=document):
                with self.assertRaisesRegex(RuntimeError, "page image table failed"):
                    image_extractor.extract_embedded_images(
                        "fake.pdf", temp_dir, "/api/uploads", created_paths=created_paths
                    )
            self.assertEqual(len(created_paths), 1)
            self.assertTrue(Path(created_paths[0]).exists())
            brochure_routes._cleanup_failed_upload_artifacts(set(created_paths))
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_failed_processing_cleanup_removes_only_provided_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.png"
            unrelated = Path(temp_dir) / "unrelated.png"
            for path in (first, second, unrelated):
                path.write_bytes(b"x")
            with patch.object(brochure_routes, "UPLOADS_DIR", temp_dir):
                brochure_routes._cleanup_failed_upload_artifacts({str(first), str(second)})
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(unrelated.exists())

    def test_process_failure_cleans_request_owned_source_file(self):
        async def run_test(image_path: str):
            with (
                patch(
                    "app.routes.brochure._save_upload",
                    new=AsyncMock(return_value=(image_path, ".png")),
                ),
                patch(
                    "app.routes.brochure.recognize_batch",
                    new=AsyncMock(side_effect=RuntimeError("simulated OCR failure")),
                ),
            ):
                response = await brochure_routes.process(
                    file=MagicMock(filename="source.png"),
                    category="Medical",
                    selectedPages=None,
                )
            self.assertEqual(response.status_code, 500)
            self.assertFalse(Path(image_path).exists())

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (12, 12), "white").save(image_path, format="PNG")
            asyncio.run(run_test(str(image_path)))


if __name__ == "__main__":
    unittest.main()
