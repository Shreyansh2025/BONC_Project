import asyncio
import json
import os
import random
import re
import string
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import fitz
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from sqlalchemy import delete, insert, select, update

from app.config import UPLOADS_DIR
from app.db import b2b_products_table, brochures_table, get_engine, products_table
from app.logger import logger
from app.models import AdditionalSection, BrochureCreate, ProductCreate, UpdateProductImages
from app.utils.ai_structure import structure_text_with_ai
from app.utils.bg_remover import remove_background
from app.utils.image_extractor import (
    EmbeddedImage,
    PageResult,
    build_image_pool,
    crop_product_image,
    ensure_dir,
    extract_embedded_images,
    generate_ocr_pages,
    generate_page_thumbnails,
    get_pdf_page_count,
)
from app.utils.ocr_pool import recognize_batch
from app.utils.search_index_client import refresh_local_product_index
from app.utils.serialize import json_col, serialize_b2b_product_row, serialize_row

router = APIRouter()

BASE_UPLOADS_URL = "/api/uploads"
ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB, mirrors multer's limits.fileSize
MAX_IMAGES_PER_PRODUCT = 5
# Resource limits for files that are rasterized or passed to OCR/background removal.
MAX_PDF_PAGES = 100
MAX_IMAGE_DIMENSION = 10_000
MAX_IMAGE_PIXELS = 40_000_000
PDF_RENDER_SCALE = 3.0  # generate_ocr_pages uses scale=3.0
MAX_RENDERED_PAGE_DIMENSION = 12_000
MAX_RENDERED_PAGE_PIXELS = 25_000_000


# ─── Upload validation ─────────────────────────────────────────────────────────
def _safe_filename(original_name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9.-]", "_", original_name)
    return f"{int(time.time() * 1000)}-{safe}"


def _check_image_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="Image dimensions are invalid")
    if max(width, height) > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Image dimensions exceed the supported limit "
                f"({MAX_IMAGE_DIMENSION}px per side and {MAX_IMAGE_PIXELS} total pixels)"
            ),
        )


def _validate_image_source(source: str | BytesIO, ext: str) -> None:
    expected_format = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
    try:
        with Image.open(source) as image:
            if image.format != expected_format:
                raise HTTPException(
                    status_code=400,
                    detail=f"File content does not match the {ext} extension",
                )
            width, height = image.size
            _check_image_dimensions(width, height)
            image.verify()
    except HTTPException:
        raise
    except Image.DecompressionBombError as err:
        raise HTTPException(
            status_code=400,
            detail="Image dimensions exceed the supported limit",
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=400,
            detail="Uploaded image is invalid or corrupted",
        ) from err


def _validate_uploaded_file(file_path: str, ext: str) -> None:
    """Validate actual file content and processing resource limits after upload."""
    if ext != ".pdf":
        _validate_image_source(file_path, ext)
        return

    try:
        with fitz.open(file_path) as doc:
            if doc.needs_pass:
                raise HTTPException(
                    status_code=400,
                    detail="Password-protected PDFs are not supported",
                )
            if doc.page_count < 1:
                raise HTTPException(status_code=400, detail="PDF contains no pages")
            if doc.page_count > MAX_PDF_PAGES:
                raise HTTPException(
                    status_code=400,
                    detail=f"PDF exceeds the supported limit of {MAX_PDF_PAGES} pages",
                )

            for page_number, page in enumerate(doc, start=1):
                width = int(page.rect.width * PDF_RENDER_SCALE)
                height = int(page.rect.height * PDF_RENDER_SCALE)
                if (
                    width <= 0
                    or height <= 0
                    or max(width, height) > MAX_RENDERED_PAGE_DIMENSION
                    or width * height > MAX_RENDERED_PAGE_PIXELS
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"PDF page {page_number} exceeds the supported rendering size "
                            f"({MAX_RENDERED_PAGE_DIMENSION}px per side and "
                            f"{MAX_RENDERED_PAGE_PIXELS} total pixels)"
                        ),
                    )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail="Uploaded PDF is invalid or corrupted") from err


async def _save_upload(file: UploadFile) -> tuple[str, str]:
    """Validates and saves an upload, returning (file_path, extension).

    The upload is streamed to disk under the existing 50MB cap. Both the file
    extension and actual file content are checked before any processing starts.
    Invalid/partial files are removed before returning an error.
    """
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f'Invalid file type "{ext}". Only PDF, JPG, JPEG, PNG allowed.',
        )

    ensure_dir(UPLOADS_DIR)
    dest_name = _safe_filename(file.filename or "upload")
    dest_path = os.path.join(UPLOADS_DIR, dest_name)
    size = 0
    try:
        with open(dest_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail="File too large (max 50MB)")
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        _validate_uploaded_file(dest_path, ext)
        return dest_path, ext
    except HTTPException:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    except OSError as err:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        logger.exception("Could not save uploaded file")
        raise HTTPException(status_code=500, detail="Could not save uploaded file") from err
    except Exception as err:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        logger.exception("Unexpected upload validation failure")
        raise HTTPException(status_code=500, detail="Could not process uploaded file") from err


async def _read_limited_image_upload(file: UploadFile) -> bytes:
    """Read an image upload with the same size/content checks as file uploads."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=400, detail="Only JPG, JPEG, and PNG images are supported")

    # The upload has already been parsed by FastAPI; read at most one byte
    # beyond the existing size limit so this route never buffers an unlimited body.
    image_bytes = await file.read(MAX_FILE_SIZE + 1)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(image_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    _validate_image_source(BytesIO(image_bytes), ext)
    return image_bytes


@router.post("/remove-background")
async def remove_bg(file: UploadFile):
    input_bytes = await _read_limited_image_upload(file)
    try:
        output_bytes = await asyncio.to_thread(remove_background, input_bytes)
    except Exception as err:
        logger.exception("Background removal failed")
        raise HTTPException(status_code=500, detail="Background removal failed") from err

    ensure_dir(UPLOADS_DIR)
    dest_name = _safe_filename("bg-removed.png")
    dest_path = os.path.join(UPLOADS_DIR, dest_name)
    with open(dest_path, "wb") as out:
        out.write(output_bytes)

    return JSONResponse({"url": f"{BASE_UPLOADS_URL}/{dest_name}"})


def _normalize_selected_pages(raw: str | None) -> list[int] | None:
    """Parse an optional JSON list of one-based page numbers.

    None or an empty form value means no page filter (process every page).
    If the caller explicitly supplies a selection, reject malformed values
    instead of silently treating them as no selection and processing the
    entire PDF.
    """
    if raw is None or raw == "":
        return None

    try:
        arr = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as err:
        raise HTTPException(
            status_code=400,
            detail="selectedPages must be a JSON array of positive integer page numbers",
        ) from err

    if not isinstance(arr, list) or not arr:
        raise HTTPException(
            status_code=400,
            detail="selectedPages must be a non-empty JSON array of positive integer page numbers",
        )

    nums: list[int] = []
    for page in arr:
        # Preserve compatibility with numeric strings, but reject booleans,
        # fractional numbers, nulls, objects, and other silently-coercible values.
        if isinstance(page, bool) or not isinstance(page, (int, str)):
            raise HTTPException(
                status_code=400,
                detail="selectedPages must contain only positive integer page numbers",
            )
        if isinstance(page, str):
            page_text = page.strip()
            if not page_text or not re.fullmatch(r"[+-]?\d+", page_text):
                raise HTTPException(
                    status_code=400,
                    detail="selectedPages must contain only positive integer page numbers",
                )
            page_number = int(page_text)
        else:
            page_number = page

        if page_number < 1:
            raise HTTPException(
                status_code=400,
                detail="selectedPages page numbers must be greater than zero",
            )
        nums.append(page_number)

    return nums


def _validate_selected_pages(selected_pages: list[int] | None, total_pages: int) -> None:
    """Reject explicit selections that are outside a PDF's one-based page range."""
    if selected_pages is None:
        return

    invalid_pages = sorted({page for page in selected_pages if page > total_pages})
    if invalid_pages:
        invalid_text = ", ".join(str(page) for page in invalid_pages)
        raise HTTPException(
            status_code=400,
            detail=(
                f"selectedPages contains page number(s) outside this PDF's range "
                f"(1-{total_pages}): {invalid_text}"
            ),
        )


_PRICE_LINE_RE = re.compile(r"(₹|rs\.?|inr|mrp|price|\$)\s*[\d,]", re.IGNORECASE)


def _clean_ocr_text(raw: str) -> str:
    if not raw:
        return ""
    lines_out = []
    for line in raw.split("\n"):
        # Keep the price/currency symbols this time — they get stripped by
        # the noise regex below and then the line fails the alnum-ratio
        # check, which was silently dropping price lines like "MRP: ₹15,999/-".
        has_price_signal = bool(_PRICE_LINE_RE.search(line))
        line = re.sub(r"[=|§@~><{}\[\]\\^`]", " ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line:
            continue
        letters = len(re.findall(r"[a-zA-Z0-9₹$]", line))
        if has_price_signal or letters / len(line) >= 0.6:
            lines_out.append(line)
    return "\n".join(lines_out)


def _fallback_structure(raw_text: str) -> dict[str, Any]:
    lines = [
        line.strip()
        for line in raw_text.split("\n")
        if line.strip() and not line.strip().startswith("--- PAGE")
    ]

    title = lines[0] if lines else ""
    specifications: list[str] = []
    description_lines: list[str] = []

    for i in range(1, len(lines)):
        line = lines[i]
        if re.match(r"^[A-Za-z0-9 ]{2,30}:\s*.+", line):
            specifications.append(line)
        elif i < 6:
            description_lines.append(line)

    description = " ".join(description_lines)

    return {
        "title": title,
        "description": description,
        "specifications": specifications,
        "additionalSections": [],
        "products": (
            [
                {
                    "name": title,
                    "model": None,
                    "category": None,
                    "description": description,
                    "price": None,
                    "specifications": {},
                    "features": specifications,
                    "pages": [1],
                    "images": [],
                }
            ]
            if title
            else []
        ),
    }


# ─── SQL helpers (sync — run via asyncio.to_thread so the event loop isn't blocked) ──
def _insert_brochure_sync(row: dict[str, Any]) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(insert(brochures_table()).values(**row))
        return result.inserted_primary_key[0]


def _list_brochures_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = (
            conn.execute(select(brochures_table()).order_by(brochures_table().c.UploadDate.desc()))
            .mappings()
            .all()
        )
        return [serialize_row("Brochures", r) for r in rows]


def _delete_brochure_sync(id_: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(delete(brochures_table()).where(brochures_table().c.Id == id_))
        return result.rowcount > 0


def _insert_products_sync(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with get_engine().begin() as conn:
        ids = []
        for row in rows:
            # Check if a product with the same name and source file already exists
            existing = conn.execute(
                select(products_table().c.Id).where(
                    (products_table().c.ProductName == row["ProductName"]) &
                    (products_table().c.SourceFileName == row["SourceFileName"])
                )
            ).first()

            if existing:
                ids.append(existing[0])  # Reuse existing ID instead of duplicating
            else:
                result = conn.execute(insert(products_table()).values(**row))
                ids.append(result.inserted_primary_key[0])

        # Fetch all saved rows in one query, then re-sort to match the original
        # insertion order. SQL does NOT guarantee that WHERE Id IN (...) returns
        # rows in the same order as the id list, so we sort explicitly here.
        fetched_map = {
            r["Id"]: serialize_row("Products", r)
            for r in conn.execute(
                select(products_table()).where(products_table().c.Id.in_(ids))
            ).mappings().all()
        }
        return [fetched_map[id_] for id_ in ids if id_ in fetched_map]


def _list_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = (
            conn.execute(select(products_table()).order_by(products_table().c.CreatedDate.desc()))
            .mappings()
            .all()
        )
        return [serialize_row("Products", r) for r in rows]


def _get_product_sync(id_or_slug: str) -> dict[str, Any] | None:
    """Looks a product up by numeric Id first (what the Library page links
    with), then local Slug (Search links to your own brochure products),
    then falls back to the B2BProducts catalog by Slug — since most Search
    results are catalog products, not local ones."""
    with get_engine().connect() as conn:
        row = None
        try:
            id_ = int(id_or_slug)
        except (TypeError, ValueError):
            id_ = None
        if id_ is not None:
            row = conn.execute(
                select(products_table()).where(products_table().c.Id == id_)
            ).mappings().first()
        if row is None:
            row = conn.execute(
                select(products_table()).where(products_table().c.Slug == id_or_slug)
            ).mappings().first()
        if row is not None:
            return serialize_row("Products", row)

        b2b_row = conn.execute(
            select(b2b_products_table()).where(b2b_products_table().c.Slug == id_or_slug)
        ).mappings().first()
        return serialize_b2b_product_row(b2b_row) if b2b_row else None

def _update_product_images_sync(id_: int, images: list[str]) -> dict[str, Any] | None:
    with get_engine().begin() as conn:
        conn.execute(
            update(products_table()).where(products_table().c.Id == id_).values(Images=json_col(images))
        )
        row = conn.execute(select(products_table()).where(products_table().c.Id == id_)).mappings().first()
        return serialize_row("Products", row) if row else None


def _delete_product_sync(id_: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(delete(products_table()).where(products_table().c.Id == id_))
        return result.rowcount > 0


def _parse_id(raw_id: str, not_found_message: str) -> int:
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail=not_found_message)


# ─── POST /preview ─────────────────────────────────────────────────────────────
@router.post("/preview")
async def preview(file: UploadFile):
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    file_path, ext = await _save_upload(file)

    try:
        if ext == ".pdf":
            total_pages = get_pdf_page_count(file_path)
            pages = generate_page_thumbnails(file_path, UPLOADS_DIR, BASE_UPLOADS_URL)
            return {
                "isPdf": True,
                "totalPages": total_pages,
                "pages": [
                    {"pageNumber": p.pageNumber, "imageUrl": p.imageUrl} for p in pages
                ],
                "fileName": file.filename,
            }
        else:
            dest_name = os.path.basename(file_path)
            return {
                "isPdf": False,
                "totalPages": 1,
                "pages": [{"pageNumber": 1, "imageUrl": f"{BASE_UPLOADS_URL}/{dest_name}"}],
                "fileName": file.filename,
            }
    except Exception as err:
        logger.error(f"Preview error: {err}")
        raise HTTPException(status_code=500, detail="Failed to generate preview") from err


# ─── POST /process ──────────────────────────────────────────────────────────────
@router.post("/process")
async def process(
    file: UploadFile,
    category: str = Form(...),
    selectedPages: str | None = Form(default=None),
):
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")
    if not category:
        raise HTTPException(status_code=400, detail="category is required")

    from app.models import CATEGORIES
    if category not in CATEGORIES:
        # Accept unknown categories gracefully in case the frontend sends a
        # custom value — clamp to "General" rather than hard-rejecting, so
        # existing integrations don't break. Log it so we can track drift.
        logger.warning(f"Unknown category '{category}' received — falling back to 'General'")
        category = "General"

    # Page selection only applies to PDFs. Preserve the existing behavior for
    # image uploads, for which selectedPages is not used.
    upload_ext = os.path.splitext(file.filename or "")[1].lower()
    selected_pages = _normalize_selected_pages(selectedPages) if upload_ext == ".pdf" else None
    file_path, ext = await _save_upload(file)

    try:
        ocr_pages: list[PageResult] = []
        extracted_images: list[str] = []

        if ext == ".pdf":
            total_pages = get_pdf_page_count(file_path)
            try:
                _validate_selected_pages(selected_pages, total_pages)
            except HTTPException:
                # Do not leave the uploaded source behind when its explicit
                # page selection is invalid.
                try:
                    os.remove(file_path)
                except OSError as cleanup_err:
                    logger.warning(f"Could not remove upload after invalid page selection: {cleanup_err}")
                raise

            pages_to_process = (
                selected_pages
                if selected_pages is not None
                else list(range(1, total_pages + 1))
            )
            ocr_pages = generate_ocr_pages(
                file_path, UPLOADS_DIR, BASE_UPLOADS_URL, pages_to_process
            )
            extracted_images = [p.imageUrl for p in ocr_pages]
            # Real embedded photos, grouped by page — used to give each product
            # on a shared page its own actual photo instead of one reused page-crop.
            embedded_by_page = extract_embedded_images(
                file_path, UPLOADS_DIR, BASE_UPLOADS_URL, pages_to_process
            )
        else:
            dest_name = os.path.basename(file_path)
            image_url = f"{BASE_UPLOADS_URL}/{dest_name}"
            with open(file_path, "rb") as f:
                buffer = f.read()
            ocr_pages = [PageResult(pageNumber=1, imageUrl=image_url, buffer=buffer)]
            extracted_images = [image_url]
            embedded_by_page = {}

        # Run OCR on all pages
        ocr_results = await recognize_batch(
            [(p.pageNumber, p.buffer) for p in ocr_pages]
        )
        ocr_results.sort(key=lambda r: r["pageNumber"])

        combined_text = "\n\n".join(
            f"--- PAGE {r['pageNumber']} ---\n{_clean_ocr_text(r['text'])}"
            for r in ocr_results
        )

        # AI structuring
        try:
            structured = await structure_text_with_ai(combined_text)
        except Exception as ai_err:
            logger.warning(f"AI structuring failed, using heuristic fallback: {ai_err}")
            structured = _fallback_structure(combined_text)

        # Assign each product a real photo where possible. Products sharing a
        # page are matched by REGION — each embedded photo carries the actual
        # spot on the page it was placed (top-left, center-right, etc.), and
        # each product carries the AI's guess of where its photo should be.
        # Matching by region (instead of just handing out photos in whatever
        # order the PDF happens to store them internally) is what keeps a
        # product from ending up with a neighboring product's photo, or a
        # lifestyle/person shot that just happened to come first in the file.
        #
        # Crucially, a product can be associated with MANY pages (narrative
        # brochures mention a product across several pages, not just one
        # dedicated catalog page). So instead of only looking at the first
        # page in that list — which is often a cover/lifestyle page — we
        # search every page the product appears on and pick whichever page
        # actually has a real, well-matched product photo.
        claimed: dict[int, set[int]] = {page: set() for page in embedded_by_page}

        def region_score(region: str, wanted_region: str | None) -> int:
            if not wanted_region or wanted_region in ("none", "full"):
                return 1
            if region == wanted_region:
                return 0  # exact match
            if region.split("-")[-1] == wanted_region.split("-")[-1]:
                return 1  # same left/right half
            return 2  # no relation — last resort

        def _best_image_across_pages(
            candidate_pages: list["PageResult"], wanted_region: str | None
        ) -> tuple["PageResult", EmbeddedImage] | None:
            """Look at every candidate page's unclaimed embedded images and
            return the single best (page, image) match across ALL of them —
            not just whichever page happened to be checked first."""
            best: tuple[int, int, "PageResult", int, EmbeddedImage] | None = None  # (score, -area, page, idx, img)
            for page in candidate_pages:
                for idx, img in enumerate(embedded_by_page.get(page.pageNumber, [])):
                    if idx in claimed.get(page.pageNumber, set()):
                        continue
                    score = region_score(img.region, wanted_region)
                    key = (score, -img.area)
                    if best is None or key < (best[0], best[1]):
                        best = (score, -img.area, page, idx, img)
            if best is None:
                return None
            _, _, page, idx, img = best
            claimed.setdefault(page.pageNumber, set()).add(idx)
            return page, img

        products_with_images = []
        for p in structured["products"]:
            candidate_pages = [op for op in ocr_pages if (op.pageNumber in p["pages"] if p["pages"] else True)]
            if not candidate_pages:
                candidate_pages = ocr_pages[:1] if ocr_pages else []

            if not candidate_pages:
                products_with_images.append({**p, "images": []})
                continue

            wanted_region = p.get("imageRegion")

            # 1) Search every page this product appears on for the best
            #    real embedded photo — matched by region, not just page order.
            best_match = _best_image_across_pages(candidate_pages, wanted_region)
            if best_match is not None:
                match_page, picked = best_match
                products_with_images.append(
                    {**p, "images": [picked.imageUrl, match_page.imageUrl][:MAX_IMAGES_PER_PRODUCT]}
                )
                continue

            # No embedded photo anywhere on this product's pages — fall back
            # to the first candidate page (usually where its text/specs are).
            target_page = candidate_pages[0]
            full_page_url = target_page.imageUrl

            # 2) Fall back to cropping the AI-guessed region out of the full
            #    page render.
            region = p.get("imageRegion")
            if region and region not in ("none", "full"):
                try:
                    crop_name = (
                        f"prod_{int(time.time() * 1000)}_"
                        f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=5))}.jpg"
                    )
                    crop_path = os.path.join(UPLOADS_DIR, crop_name)
                    crop_product_image(target_page.buffer, region, crop_path)
                    products_with_images.append(
                        {
                            **p,
                            "images": [f"{BASE_UPLOADS_URL}/{crop_name}", full_page_url],
                        }
                    )
                    continue
                except Exception:
                    pass  # crop failed — fall through to full-page only

            # 3) Last resort: the full page.
            products_with_images.append({**p, "images": [full_page_url]})

        # Save brochure record
        brochure_row = BrochureCreate(
            fileName=file.filename or "",
            category=category,
            title=structured["title"],
            description=structured["description"],
            specifications=structured["specifications"],
            additionalSections=[
                s if isinstance(s, AdditionalSection) else AdditionalSection(**s)
                for s in structured["additionalSections"]
            ],
            extractedText=combined_text[:10000],
            extractedImages=extracted_images,
        ).to_row()
        brochure_id = await asyncio.to_thread(_insert_brochure_sync, brochure_row)

        image_pool = build_image_pool(embedded_by_page, ocr_pages)

        return {
            "brochureId": str(brochure_id),
            "title": structured["title"],
            "description": structured["description"],
            "specifications": structured["specifications"],
            "extractedImages": extracted_images,
            "products": products_with_images,
            "imagePool": [
                {
                    "id": item.id,
                    "url": item.url,
                    "pageNumber": item.pageNumber,
                    "source": item.source,
                }
                for item in image_pool
            ],
        }
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Process error: {err}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Processing failed",
                "error": "Internal processing error",
            },
        )


# ─── GET /brochures ─────────────────────────────────────────────────────────────
@router.get("/brochures")
async def list_brochures():
    try:
        return await asyncio.to_thread(_list_brochures_sync)
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error fetching brochures", "error": str(err)},
        )


# ─── DELETE /brochures/{id} ──────────────────────────────────────────────────────
@router.delete("/brochures/{brochure_id}")
async def delete_brochure(brochure_id: str):
    id_ = _parse_id(brochure_id, "Brochure not found")
    try:
        found = await asyncio.to_thread(_delete_brochure_sync, id_)
        if not found:
            raise HTTPException(status_code=404, detail="Brochure not found")
        return {"message": "Brochure deleted", "id": brochure_id}
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error deleting brochure", "error": str(err)},
        )


# ─── GET /products ──────────────────────────────────────────────────────────────
@router.get("/products")
async def list_products():
    try:
        return await asyncio.to_thread(_list_products_sync)
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error fetching products", "error": str(err)},
        )


# ─── GET /products/{id_or_slug} ─────────────────────────────────────────────────
# Powers the product detail page. Accepts either the numeric Id (used by the
# Library page, which already has `_id` from the list response) or the Slug
# (used by Search result links, which only carry `slug`) — whichever was
# passed in is tried in that order.
@router.get("/products/{id_or_slug}")
async def get_product(id_or_slug: str):
    try:
        product = await asyncio.to_thread(_get_product_sync, id_or_slug)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        return product
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error fetching product", "error": str(err)},
        )


# ─── POST /products ─────────────────────────────────────────────────────────────
@router.post("/products")
async def save_products(body: dict[str, Any]):
    products = body.get("products")
    if not products or not isinstance(products, list):
        raise HTTPException(status_code=400, detail="products array is required")

    try:
        rows = [ProductCreate(**p).to_row() for p in products]
        saved_docs = await asyncio.to_thread(_insert_products_sync, rows)

        # _insert_products_sync returns only after its SQLAlchemy transaction
        # context has committed. Search AI owns the in-memory index in the
        # separated architecture, so request refresh over its internal API.
        # Keep this public response unchanged if the refresh is temporarily
        # unavailable; Search AI also performs its periodic full refresh.
        await refresh_local_product_index(reason="products_saved")

        return JSONResponse(
            status_code=201,
            content={
                "savedCount": len(saved_docs),
                "products": saved_docs,
            },
        )
    except Exception as err:
        logger.error(f"Save products error traceback: {repr(err)}") # Added detailed log
        return JSONResponse(
            status_code=500,
            content={"message": "Error saving products", "error": str(err)},
        )

# ─── PATCH /products/{id} ────────────────────────────────────────────────────────
@router.patch("/products/{product_id}")
async def update_product_images(product_id: str, body: UpdateProductImages):
    id_ = _parse_id(product_id, "Product not found")
    try:
        result = await asyncio.to_thread(_update_product_images_sync, id_, body.images)
        if not result:
            raise HTTPException(status_code=404, detail="Product not found")
        return result
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error updating product images", "error": str(err)},
        )


# ─── DELETE /products/{id} ───────────────────────────────────────────────────────
@router.delete("/products/{product_id}")
async def delete_product(product_id: str):
    id_ = _parse_id(product_id, "Product not found")
    try:
        found = await asyncio.to_thread(_delete_product_sync, id_)
        if not found:
            raise HTTPException(status_code=404, detail="Product not found")

        # _delete_product_sync returns only after its SQLAlchemy transaction
        # context has committed. Refresh Search AI over HTTP after that commit.
        # Preserve the existing public delete response even if refresh is down.
        await refresh_local_product_index(reason="product_deleted")

        return {"message": "Product deleted", "id": product_id}
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error deleting product", "error": str(err)},
        )