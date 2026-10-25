"""Legacy combined-app health routes.

The standalone Search AI health/debug implementation lives in app.search_ai.routes.health.
"""
from fastapi import APIRouter

from app.search_ai.routes.health import router as search_router

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


router.include_router(search_router)
