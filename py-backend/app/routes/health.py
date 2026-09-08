from fastapi import APIRouter

from app.utils import b2b_product_search, b2b_search, product_search

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/debug/index-status")
async def index_status():
    """Real counts of what's actually loaded in memory right now — hit
    this after a deploy instead of grepping logs to see which of the
    three search indexes (if any) failed to build."""
    return {
        "b2bCompanies": b2b_search.index_status(),
        "b2bProducts": b2b_product_search.index_status(),
        "localProducts": product_search.index_status(),
    }
