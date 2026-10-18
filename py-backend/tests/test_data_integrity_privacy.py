"""Regression tests for Batch 3 product deduplication and source-PDF privacy."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects import mssql
from fastapi import HTTPException

from app.routes import brochure as brochure_routes


class FakeUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self._chunks = list(chunks)

    async def read(self, size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _one_page_pdf() -> bytes:
    with fitz.open() as doc:
        doc.new_page(width=612, height=792)
        return doc.tobytes()


class ProductDeduplicationTests(unittest.TestCase):
    def test_duplicate_lookup_includes_model_in_matching_key(self):
        table = Table(
            "Products",
            MetaData(),
            Column("Id", Integer, primary_key=True),
            Column("ProductName", String),
            Column("SourceFileName", String),
            Column("Model", String),
            Column("Category", String),
            Column("Description", String),
            Column("Price", String),
            Column("Features", String),
            Column("Specifications", String),
            Column("Images", String),
            Column("ImagePath", String),
            Column("Slug", String),
            Column("CreatedDate", String),
        )
        existing_result = MagicMock()
        existing_result.first.return_value = None
        insert_result = MagicMock()
        insert_result.inserted_primary_key = [42]
        fetched_result = MagicMock()
        fetched_result.mappings.return_value.all.return_value = [
            {"Id": 42, "ProductName": "Industrial Pump", "Model": "PX-200", "SourceFileName": "catalog.pdf"}
        ]
        connection = MagicMock()
        connection.execute.side_effect = [existing_result, insert_result, fetched_result]
        engine = MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        row = {
            "ProductName": "Industrial Pump",
            "SourceFileName": "catalog.pdf",
            "Model": "PX-200",
            "Category": "Machinery",
            "Description": "",
            "Price": None,
            "Features": "[]",
            "Specifications": "{}",
            "Images": "[]",
            "ImagePath": None,
            "CreatedDate": "2026-01-01",
        }

        with (
            patch.object(brochure_routes, "products_table", return_value=table),
            patch.object(brochure_routes, "get_engine", return_value=engine),
        ):
            saved = brochure_routes._insert_products_sync([row])

        lookup_sql = str(connection.execute.call_args_list[0].args[0].compile(dialect=mssql.dialect()))
        self.assertIn("[Products].[Model] =", lookup_sql)
        self.assertEqual(saved[0]["_id"], 42)
        self.assertEqual(connection.execute.call_count, 3)


class PrivatePdfUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_source_is_saved_outside_public_upload_directory(self):
        with tempfile.TemporaryDirectory() as root:
            public_dir = Path(root) / "public"
            private_dir = Path(root) / "private"
            public_dir.mkdir()
            private_dir.mkdir()
            with (
                patch.object(brochure_routes, "UPLOADS_DIR", str(public_dir)),
                patch.object(brochure_routes, "PRIVATE_PDF_UPLOAD_DIR", str(private_dir)),
            ):
                file_path, ext = await brochure_routes._save_upload(
                    FakeUpload("confidential-catalog.pdf", [_one_page_pdf()])
                )

            self.assertEqual(ext, ".pdf")
            self.assertTrue(Path(file_path).is_file())
            self.assertEqual(Path(file_path).parent, private_dir)
            self.assertNotIn(Path(file_path), public_dir.iterdir())

    async def test_pdf_private_directory_inside_public_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            public_dir = Path(root) / "uploads"
            private_dir = public_dir / "private"
            public_dir.mkdir()
            with (
                patch.object(brochure_routes, "UPLOADS_DIR", str(public_dir)),
                patch.object(brochure_routes, "PRIVATE_PDF_UPLOAD_DIR", str(private_dir)),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await brochure_routes._save_upload(
                        FakeUpload("catalog.pdf", [_one_page_pdf()])
                    )
            self.assertEqual(getattr(raised.exception, "status_code", None), 500)
            self.assertFalse(private_dir.exists())

    async def test_preview_removes_pdf_source_but_preserves_generated_preview(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "private-source.pdf"
            source.write_bytes(_one_page_pdf())
            generated = root_path / "page-1.png"
            generated.write_bytes(b"generated page image")
            page = SimpleNamespace(pageNumber=1, imageUrl="/api/uploads/page-1.png")

            def make_thumbnails(*args, **kwargs):
                kwargs["created_paths"].append(str(generated))
                return [page]

            with (
                patch.object(brochure_routes, "_save_upload", new=AsyncMock(return_value=(str(source), ".pdf"))),
                patch.object(brochure_routes, "get_pdf_page_count", return_value=1),
                patch.object(brochure_routes, "generate_page_thumbnails", side_effect=make_thumbnails),
            ):
                response = await brochure_routes.preview(FakeUpload("catalog.pdf", []))

            self.assertFalse(source.exists())
            self.assertTrue(generated.exists())
            self.assertTrue(response["isPdf"])
            self.assertEqual(response["pages"][0]["imageUrl"], "/api/uploads/page-1.png")

    async def test_successful_pdf_processing_removes_source_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "private-source.pdf"
            source.write_bytes(_one_page_pdf())
            ocr_page = SimpleNamespace(pageNumber=1, imageUrl="/api/uploads/page-1.png", buffer=b"page")
            structured = {
                "title": "Catalog",
                "description": "",
                "specifications": [],
                "additionalSections": [],
                "products": [],
            }
            with (
                patch.object(brochure_routes, "_save_upload", new=AsyncMock(return_value=(str(source), ".pdf"))),
                patch.object(brochure_routes, "get_pdf_page_count", return_value=1),
                patch.object(brochure_routes, "generate_ocr_pages", return_value=[ocr_page]),
                patch.object(brochure_routes, "extract_embedded_images", return_value={}),
                patch.object(brochure_routes, "recognize_batch", new=AsyncMock(return_value=[{"pageNumber": 1, "text": "Catalog"}])),
                patch.object(brochure_routes, "structure_text_with_ai", new=AsyncMock(return_value=structured)),
                patch.object(brochure_routes, "_insert_brochure_sync", return_value=123),
                patch.object(brochure_routes, "build_image_pool", return_value=[]),
            ):
                response = await brochure_routes.process(
                    FakeUpload("catalog.pdf", []), category="Medical", selectedPages=None
                )

            self.assertEqual(response["brochureId"], "123")
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
