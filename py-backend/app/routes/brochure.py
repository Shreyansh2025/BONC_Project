import asyncio
import json
import os
import random
import re
import string
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, insert, select, update

from app.config import UPLOADS_DIR
from app.db import brochures_table, get_engine, products_table
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
from app.utils.serialize import json_col, serialize_row

router = APIRouter()

BASE_UPLOADS_URL = "/api/uploads"
ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB, mirrors multer's limits.fileSize
MAX_IMAGES_PER_PRODUCT = 5


@router.post("/remove-background")
async def remove_bg(file: UploadFile):
    input_bytes = await file.read()

    # Runs on rembg's cached, pre-warmed session (see app/utils/bg_remover.py)
    # instead of building a fresh ONNX session per request — this is what was
    # causing the ~1 minute delay.
    output_bytes = remove_background(input_bytes)

    # Persist the result to disk (like every other image in this app) rather
    # than only returning raw bytes for a browser-side blob: URL. Blob URLs
    # live only in that tab's memory, so closing the lightbox / refreshing
    # the page loses the edit. Saving it here and handing back a real
    # /api/uploads/... URL means the frontend can attach it permanently to
    # the product or image pool, same as any other image.
    ensure_dir(UPLOADS_DIR)
    dest_name = _safe_filename("bg-removed.png")
    dest_path = os.path.join(UPLOADS_DIR, dest_name)
    with open(dest_path, "wb") as out:
        out.write(output_bytes)

    return JSONResponse({"url": f"{BASE_UPLOADS_URL}/{dest_name}"})


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _safe_filename(original_name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9.-]", "_", original_name)
    return f"{int(time.time() * 1000)}-{safe}"


async def _save_upload(file: UploadFile) -> tuple[str, str]:
    """Validates and saves an uploaded file, mirroring the multer diskStorage config.
    Returns (file_path, ext)."""
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
    with open(dest_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                out.close()
                os.remove(dest_path)
                raise HTTPException(status_code=400, detail="File too large (max 50MB)")
            out.write(chunk)

    return dest_path, ext


def _normalize_selected_pages(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    try:
        arr = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(arr, list) or len(arr) == 0:
        return None
    nums = []
    for p in arr:
        try:
            n = int(p)
            if n > 0:
                nums.append(n)
        except (ValueError, TypeError):
            continue
    return nums or None


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
            result = conn.execute(insert(products_table()).values(**row))
            ids.append(result.inserted_primary_key[0])
        fetched = (
            conn.execute(select(products_table()).where(products_table().c.Id.in_(ids)))
            .mappings()
            .all()
        )
        return [serialize_row("Products", r) for r in fetched]


def _list_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = (
            conn.execute(select(products_table()).order_by(products_table().c.CreatedDate.desc()))
            .mappings()
            .all()
        )
        return [serialize_row("Products", r) for r in rows]


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

    selected_pages = _normalize_selected_pages(selectedPages)
    file_path, ext = await _save_upload(file)

    try:
        ocr_pages: list[PageResult] = []
        extracted_images: list[str] = []

        if ext == ".pdf":
            pages_to_process = selected_pages or list(
                range(1, get_pdf_page_count(file_path) + 1)
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
            status_code=500, content={"message": "Processing failed", "error": str(err)}
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


# ─── POST /products ─────────────────────────────────────────────────────────────
@router.post("/products")
async def save_products(body: dict[str, Any]):
    products = body.get("products")
    if not products or not isinstance(products, list):
        raise HTTPException(status_code=400, detail="products array is required")

    try:
        rows = [ProductCreate(**p).to_row() for p in products]
        saved_docs = await asyncio.to_thread(_insert_products_sync, rows)
        return JSONResponse(
            status_code=201,
            content={
                "savedCount": len(saved_docs),
                "products": saved_docs,
            },
        )
    except Exception as err:
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
        return {"message": "Product deleted", "id": product_id}
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(
            status_code=500,
            content={"message": "Error deleting product", "error": str(err)},
        )