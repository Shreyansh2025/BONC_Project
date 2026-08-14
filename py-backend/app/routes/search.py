import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.logger import logger
from app.models import slugify
from app.utils import b2b_product_search, b2b_search
from app.utils.product_search import search_products_sync as search_local_products_sync

router = APIRouter()


# ─── Shaping helpers ─────────────────────────────────────────────────────
# search_companies_sync / search_products_sync already return rows shaped
# by serialize_row() (camelCase, JSON-decoded) plus matchType /
# matchPercentage / matchedKeyword bolted on by the scoring code. These
# helpers take one of those rows and produce the final wire shape for the
# combined endpoint: a `type` tag so the frontend knows which card layout
# to use, and `image` / `slug` fields that are always present (never
# missing) so the frontend never has to guess.

def _shape_company(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["type"] = "business"
    # ImagePath comes through as `imagePath` via serialize_row. Normalize
    # to a single `image` key for the frontend, same as products below.
    row["image"] = row.pop("imagePath", None) or None
    return row


def _shape_product(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["type"] = "product"
    row["image"] = row.pop("imagePath", None) or None

    # Guarantee a matchPercentage so products rank properly alongside companies
    if "matchPercentage" not in row or row["matchPercentage"] is None:
        row["matchPercentage"] = 100.0 if row.get("productName") else 50.0

    slug = (row.get("slug") or "").strip()
    if not slug:
        slug = slugify(row.get("productName"), fallback=str(row.get("_id", "")))
    row["slug"] = slug
    return row

def _paginate(items: list[dict[str, Any]], page: int, limit: int) -> dict[str, Any]:
    total_results = len(items)
    total_pages = max(1, (total_results + limit - 1) // limit)
    # Clamp page into range instead of erroring on an out-of-range request.
    page = max(1, min(page, total_pages))
    offset = (page - 1) * limit

    return {
        "results": items[offset : offset + limit],
        "pagination": {
            "current_page": page,
            "limit": limit,
            "total_results": total_results,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
        },
    }


# ─── GET /search ─────────────────────────────────────────────────────────
# Unified search: runs the company and product searches together, tags
# every result with `type: "business" | "product"`, merges them into one
# ranked list (sorted by matchPercentage, same three-layer scoring as
# before), and returns a page of it at a time.
#
# `type` query param lets the frontend ask for just one kind of result
# without giving up pagination/image/slug shaping — e.g. a future
# "Companies only" filter — while still hitting a single endpoint.
@router.get("/search")
async def search_unified(
    q: str = "",
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    type: Literal["business", "product", "all"] = "all",
):
    if not q.strip():
        return {
            "results": [],
            "pagination": {
                "current_page": 1,
                "limit": limit,
                "total_results": 0,
                "total_pages": 1,
                "has_next": False,
                "has_previous": False,
            },
        }

    try:
        combined: list[dict[str, Any]] = []

        if type in ("business", "all"):
            company_rows = await asyncio.to_thread(b2b_search.search_companies_sync, q)
            combined.extend(_shape_company(r) for r in company_rows)

        if type in ("product", "all"):
            # 1. Get real B2B catalog products safely
            try:
                b2b_product_rows = await asyncio.to_thread(b2b_product_search.search_products_sync, q)
                combined.extend(_shape_product(r) for r in b2b_product_rows)
            except Exception as b2b_err:
                logger.error(f"B2B product search failed: {b2b_err}")

            # 2. Get local brochure-extracted products safely
            try:
                local_product_rows = await asyncio.to_thread(search_local_products_sync, q)
                combined.extend(_shape_product(r) for r in local_product_rows)
            except Exception as local_err:
                logger.error(f"Local product search failed: {local_err}")

        combined.sort(key=lambda r: r.get("matchPercentage") or 0, reverse=True)

        return _paginate(combined, page, limit)
    except Exception as err:
        logger.error(f"Unified search error: {err}")
        return JSONResponse(
            status_code=500,
            content={"message": "Search failed", "error": str(err)},
        )