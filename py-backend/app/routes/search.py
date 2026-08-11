import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.logger import logger
from app.utils import b2b_product_search, b2b_search

router = APIRouter()


# ─── GET /search/companies ──────────────────────────────────────────────────
@router.get("/search/companies")
async def search_companies(q: str = ""):
    if not q.strip():
        return []
    try:
        return await asyncio.to_thread(b2b_search.search_companies_sync, q)
    except Exception as err:
        logger.error(f"Company search error: {err}")
        return JSONResponse(
            status_code=500,
            content={"message": "Search failed", "error": str(err)},
        )


# ─── GET /search/products ───────────────────────────────────────────────────
# Searches the real B2B product catalog (imported via
# scripts/import_b2b_products.py), matching the original standalone search
# engine's Products tab — NOT the brochure-extracted Products table (that
# data is still visible on the Library page, just not in this search).
@router.get("/search/products")
async def search_products(q: str = ""):
    if not q.strip():
        return []
    try:
        return await asyncio.to_thread(b2b_product_search.search_products_sync, q)
    except Exception as err:
        logger.error(f"Product search error: {err}")
        return JSONResponse(
            status_code=500,
            content={"message": "Search failed", "error": str(err)},
        )