import asyncio
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.logger import logger
from app.utils import b2b_product_search, b2b_search
from app.utils.product_search import search_products_sync as search_local_products_sync

router = APIRouter()


# ─── 1. Request Body Schema ──────────────────────────────────────────────
# Accepts pagination parameters from user payload
class SearchRequest(BaseModel):
    SearchText: str = ""
    PageNo: int = Field(default=1, ge=1)
    PageSize: int = Field(default=10, ge=1, le=100)
    Type: Literal["business", "product", "all"] = "all"


# ─── 2. Shaping Helpers ──────────────────────────────────────────────────
def _shape_company(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["type"] = "business"
    row["image"] = row.pop("imagePath", None) or None
    return row


def _shape_product(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["type"] = "product"
    row["image"] = row.pop("imagePath", None) or None

    if "matchPercentage" not in row or row["matchPercentage"] is None:
        row["matchPercentage"] = 100.0 if row.get("productName") else 50.0

    # B2B catalog rows (have "productsAndServicesId" — set in
    # b2b_product_search.py) come straight from the company's live
    # ProductsAndServices.Slug, which is always populated for a
    # Publish-status row and is THEIR real, permanent slug — we must not
    # regenerate it, since ours would never match the one their frontend
    # actually links to. If it's ever unexpectedly empty, that's a data
    # problem on their side worth knowing about, not something to silently
    # paper over here.
    #
    # Local brochure-extracted rows (from product_search.py / our own
    # Products table) never get a slug — they aren't published to the main
    # site's catalog, so there's no "real" slug to have. The frontend links
    # to these by numeric id instead (see product-detail.tsx / search.tsx).
    slug = (row.get("slug") or "").strip()
    if not slug and "productsAndServicesId" in row:
        logger.warning(
            f"B2B product {row.get('productsAndServicesId')} "
            f"({row.get('productName')!r}) has no Slug set upstream"
        )
    row["slug"] = slug
    return row


# ─── 3. Response Formatter (Strict 5-Field Envelope) ─────────────────────
def _paginate(items: list[dict[str, Any]], page_no: int, page_size: int) -> dict[str, Any]:
    total_count = len(items)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page_no = max(1, min(page_no, total_pages))
    offset = (page_no - 1) * page_size

    # Returns ONLY Success, Message, Type, Data, TotalCount
    return {
        "Success": True,
        "Message": "Search results fetched successfully",
        "Type": "success",
        "Data": items[offset : offset + page_size],
        "TotalCount": total_count,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "Success": True,
        "Message": "No search query provided",
        "Type": "success",
        "Data": [],
        "TotalCount": 0,
    }


# ─── 4. POST /search Route ───────────────────────────────────────────────
@router.post("/search")
async def search_unified(body: SearchRequest):
    q = body.SearchText.strip()
    if not q:
        return _empty_result()

    try:
        combined: list[dict[str, Any]] = []

        if body.Type in ("business", "all"):
            try:
                company_rows = await asyncio.to_thread(b2b_search.search_companies_sync, q)
                combined.extend(_shape_company(r) for r in company_rows)
            except Exception as b2b_company_err:
                logger.error(f"B2B company search failed: {b2b_company_err}")

        if body.Type in ("product", "all"):
            # 1. Real B2B catalog products
            try:
                b2b_product_rows = await asyncio.to_thread(b2b_product_search.search_products_sync, q)
                combined.extend(_shape_product(r) for r in b2b_product_rows)
            except Exception as b2b_err:
                logger.error(f"B2B product search failed: {b2b_err}")

            # 2. Local brochure-extracted products
            try:
                local_product_rows = await asyncio.to_thread(search_local_products_sync, q)
                combined.extend(_shape_product(r) for r in local_product_rows)
            except Exception as local_err:
                logger.error(f"Local product search failed: {local_err}")

        # Dynamic sort by match percentage
        combined.sort(key=lambda r: r.get("matchPercentage") or 0, reverse=True)

        # Dynamic slicing based on incoming user PageNo and PageSize
        return _paginate(combined, body.PageNo, body.PageSize)

    except Exception as err:
        logger.error(f"Unified search error: {err}")
        return JSONResponse(
            status_code=500,
            content={
                "Success": False,
                "Message": "Search failed",
                "Type": "error",
                "Data": [],
                "TotalCount": 0,
            },
        )