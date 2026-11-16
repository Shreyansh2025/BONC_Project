import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.logger import logger
from app.utils import b2b_product_search, b2b_search
from app.utils.dynamic_entities import (
    get_all_active_dynamic_entities,
    get_dynamic_index,
)
from app.utils.product_search import search_products_sync as search_local_products_sync

router = APIRouter()


# ─── 1. Request Body Schema ──────────────────────────────────────────────
# Accepts pagination parameters from user payload. Supports built-in entities
# ("business", "product", "all") as well as any dynamic entity (e.g. "people").
class SearchRequest(BaseModel):
    SearchText: str = Field(default="", max_length=200)
    PageNo: int = Field(default=1, ge=1)
    PageSize: int = Field(default=10, ge=1, le=100)
    Type: str = Field(default="all")


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

    if row.get("matchPercentage") is None:
        row["matchPercentage"] = 0.0

    slug = (row.get("slug") or "").strip()
    if not slug and "productsAndServicesId" in row:
        logger.warning(
            f"B2B product {row.get('productsAndServicesId')} "
            f"({row.get('productName')!r}) has no Slug set upstream"
        )
    row["slug"] = slug
    return row


def _shape_dynamic(row: dict[str, Any], entity_type: str) -> dict[str, Any]:
    row = dict(row)
    row["type"] = entity_type
    row["image"] = row.pop("imagePath", None) or row.get("image") or None
    if row.get("matchPercentage") is None:
        row["matchPercentage"] = 0.0
    return row


# ─── 3. Response Formatter (Strict 5-Field Envelope) ─────────────────────
def _paginate(items: list[dict[str, Any]], page_no: int, page_size: int) -> dict[str, Any]:
    total_count = len(items)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page_no = max(1, min(page_no, total_pages))
    offset = (page_no - 1) * page_size

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
        tasks: list[asyncio.Future] = []
        task_kinds: list[str] = []

        wanted_type = body.Type.lower().strip()

        # Built-in company search
        if wanted_type in ("business", "all"):
            tasks.append(asyncio.to_thread(b2b_search.search_companies_sync, q))
            task_kinds.append("business")

        # Built-in product search
        if wanted_type in ("product", "all"):
            tasks.append(asyncio.to_thread(b2b_product_search.search_products_sync, q))
            task_kinds.append("product")
            tasks.append(asyncio.to_thread(search_local_products_sync, q))
            task_kinds.append("product")

        # Dynamic entities search (e.g. "people", "services", etc.)
        dynamic_entities = get_all_active_dynamic_entities()
        if wanted_type == "all":
            for dyn_name in dynamic_entities:
                dyn_idx = get_dynamic_index(dyn_name)
                if dyn_idx:
                    tasks.append(asyncio.to_thread(dyn_idx.search_sync, q))
                    task_kinds.append(dyn_name)
        elif wanted_type in dynamic_entities:
            dyn_idx = get_dynamic_index(wanted_type)
            if dyn_idx:
                tasks.append(asyncio.to_thread(dyn_idx.search_sync, q))
                task_kinds.append(wanted_type)

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        combined: list[dict[str, Any]] = []
        for kind, result in zip(task_kinds, results):
            if isinstance(result, Exception):
                logger.error(f"{kind} search failed: {result}")
                continue
            if kind == "business":
                combined.extend(_shape_company(r) for r in result)
            elif kind == "product":
                combined.extend(_shape_product(r) for r in result)
            else:
                combined.extend(_shape_dynamic(r, kind) for r in result)

        # Dynamic sort by match percentage
        combined.sort(key=lambda r: r.get("matchPercentage") or 0, reverse=True)

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
