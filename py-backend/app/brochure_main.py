"""Standalone BrochureIQ FastAPI application.

This entry point intentionally does not register Search AI routes or start
Search AI embedding/index tasks. The existing app/main.py remains unchanged as
the combined-app rollback path during the staged migration.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.brochureiq.config import UPLOADS_DIR  # noqa: E402
from app.shared.db import connect_sql_server  # noqa: E402
from app.shared.logger import logger  # noqa: E402
from app.brochureiq.routes.brochure import router as brochure_router  # noqa: E402
from app.brochureiq.services.bg_remover import warm_up as warm_up_bg_remover  # noqa: E402
from app.brochureiq.services.image_extractor import ensure_dir  # noqa: E402


_raw_port = os.getenv("PORT", "8003")
try:
    PORT = int(_raw_port)
    if not 1 <= PORT <= 65535:
        raise ValueError
except ValueError as exc:
    raise RuntimeError(
        f'Invalid PORT value: "{_raw_port}". Set PORT to a valid TCP port.'
    ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start only BrochureIQ's database and background-removal initialization."""
    ensure_dir(UPLOADS_DIR)
    app.state.database_ready = False
    app.state.background_removal_warmup = "pending"

    async def connect_database() -> None:
        try:
            await asyncio.to_thread(connect_sql_server)
            app.state.database_ready = True
            logger.info("BrochureIQ database initialization completed")
        except Exception as err:
            logger.error("BrochureIQ failed to initialize SQL Server: %s", err)

    async def warm_background_remover() -> None:
        try:
            started_at = time.time()
            await asyncio.to_thread(warm_up_bg_remover)
            app.state.background_removal_warmup = "ready"
            logger.info(
                "BrochureIQ background-removal model warmed up in %sms",
                round((time.time() - started_at) * 1000),
            )
        except Exception as err:
            # Background removal can still attempt lazy initialization later;
            # record this separately rather than disabling all brochure routes.
            app.state.background_removal_warmup = "failed"
            logger.error("BrochureIQ failed to warm up background remover: %s", err)

    tasks = [
        asyncio.create_task(connect_database()),
        asyncio.create_task(warm_background_remover()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(lifespan=lifespan)

ALLOWED_ORIGINS = [
    origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - started_at) * 1000, 1)
    logger.info(
        "%s %s -> %s (%sms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# Preserve the existing public upload URL format. The deployment must point
# this service at the persistent BrochureIQ uploads directory.
ensure_dir(UPLOADS_DIR)
app.mount("/api/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.include_router(brochure_router, prefix="/api")


@app.get("/api/healthz")
async def legacy_healthz():
    """Keep the existing public health response exactly compatible."""
    return {"status": "ok"}


@app.get("/health/live")
async def liveness():
    return {"status": "ok", "service": "brochureiq"}


@app.get("/health/ready")
async def readiness(request: Request):
    database_ready = bool(getattr(request.app.state, "database_ready", False))
    uploads_ready = os.path.isdir(UPLOADS_DIR) and os.access(UPLOADS_DIR, os.W_OK)
    ready = database_ready and uploads_ready
    content = {
        "status": "ready" if ready else "not_ready",
        "service": "brochureiq",
        "checks": {
            "database": "ready" if database_ready else "not_ready",
            "uploads": "ready" if uploads_ready else "not_ready",
            "backgroundRemoval": getattr(
                request.app.state, "background_removal_warmup", "pending"
            ),
        },
    }
    return JSONResponse(status_code=200 if ready else 503, content=content)


if __name__ == "__main__":
    import uvicorn

    logger.info("BrochureIQ listening on 127.0.0.1:%s", PORT)
    uvicorn.run("app.brochure_main:app", host="127.0.0.1", port=PORT)
