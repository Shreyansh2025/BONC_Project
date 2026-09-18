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
# Accepts pagination parameters from user payload. max_length on SearchText
# caps how much text a single request can push through the vocabulary
# spell-correction pass (difflib.get_close_matches over the whole catalog
# vocabulary for every uncached word) -- without a cap, one very long query
# string is an easy way to burn a disproportionate amount of CPU per request.
class SearchRequest(BaseModel):
    SearchText: str = Field(default="", max_length=200)
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

    # Every path through the three search_*_sync() functions now always
    # sets matchPercentage (exact/partial/AI layers all set it explicitly),
    # so this branch should be unreachable in normal operation. Kept as a
    # defensive fallback for any future caller that skips the search
    # pipeline and hands rows to this shaper directly -- but it now
    # defaults to 0.0 instead of the old 50.0/100.0 guesses. Fabricating a
    # 100.0 "exact match" score for a row that was never actually scored
    # would silently pin it to the top of the results; 0.0 just means it
    # sorts last until something gives it a real score.
    if row.get("matchPercentage") is None:
        row["matchPercentage"] = 0.0

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
        # Each source's search is CPU-bound (regex + FAISS), so it's run
        # via asyncio.to_thread on the default thread pool. The three
        # were previously awaited one after another -- company, THEN b2b
        # product, THEN local product -- so a Type="all" request paid the
        # full latency of all three back-to-back even though none of them
        # depends on another's result. asyncio.gather launches all three
        # (up to) at once instead, so wall-clock time is roughly the
        # slowest single source rather than the sum of all of them.
        # return_exceptions=True keeps the previous behavior of one
        # source's failure never taking down the other two.
        tasks: list[asyncio.Future] = []
        task_kinds: list[str] = []  # "company" or "product", parallel to tasks

        if body.Type in ("business", "all"):
            tasks.append(asyncio.to_thread(b2b_search.search_companies_sync, q))
            task_kinds.append("company")

        if body.Type in ("product", "all"):
            tasks.append(asyncio.to_thread(b2b_product_search.search_products_sync, q))
            task_kinds.append("product")
            tasks.append(asyncio.to_thread(search_local_products_sync, q))
            task_kinds.append("product")

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        combined: list[dict[str, Any]] = []
        for kind, result in zip(task_kinds, results):
            if isinstance(result, Exception):
                logger.error(f"{kind} search failed: {result}")
                continue
            shape_fn = _shape_company if kind == "company" else _shape_product
            combined.extend(shape_fn(r) for r in result)

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