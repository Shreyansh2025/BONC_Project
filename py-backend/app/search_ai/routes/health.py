"""Health/debug routes owned by the standalone Search AI service."""

from fastapi import APIRouter

from app.search_ai.services import b2b_product_search, b2b_search, product_search

router = APIRouter()


@router.get("/debug/index-status")
async def index_status():
    """Return counts and build status for each Search AI index."""
    return {
        "b2bCompanies": b2b_search.index_status(),
        "b2bProducts": b2b_product_search.index_status(),
        "localProducts": product_search.index_status(),
    }
