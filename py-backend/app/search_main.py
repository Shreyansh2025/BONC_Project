"""Standalone Search AI FastAPI application.

The legacy app/main.py remains unchanged as the combined-app rollback path.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from app.shared.db import connect_sql_server  # noqa: E402
from app.shared.logger import logger  # noqa: E402
from app.search_ai.routes.health import router as search_health_router  # noqa: E402
from app.search_ai.routes.internal import router as internal_router  # noqa: E402
from app.search_ai.routes.search import router as search_router  # noqa: E402
from app.search_ai.services import b2b_product_search, b2b_search, product_search  # noqa: E402
from app.search_ai.services.embedding_model import warm_up_model  # noqa: E402


_raw_port = os.getenv("PORT", "8002")
try:
    PORT = int(_raw_port)
    if PORT <= 0:
        raise ValueError
except ValueError as exc:
    raise RuntimeError(
        f'Invalid PORT value: "{_raw_port}". Set PORT to a positive integer.'
    ) from exc


_INDEX_BUILDERS = (
    ("b2bCompanies", "B2B company", b2b_search),
    ("b2bProducts", "B2B product", b2b_product_search),
    ("localProducts", "local product", product_search),
)
_READY_INDEX_STATUSES = {"ready", "ready_empty"}


def _snapshot_status(module: Any) -> dict[str, Any]:
    snapshot = module.index_status()
    count = int(snapshot.get("count", 0))
    index_built = bool(snapshot.get("index_built", False))
    if index_built:
        status = "ready"
    elif count == 0:
        # An empty source table is a successful, ready-to-serve empty index.
        status = "ready_empty"
    else:
        status = "failed"
    return {"status": status, **snapshot}


def _initialize_state(app: FastAPI) -> None:
    app.state.database_ready = False
    app.state.embedding_model_ready = False
    app.state.initialization_error = None
    app.state.index_refresh_lock = asyncio.Lock()
    app.state.completed_refresh_events = OrderedDict()
    app.state.index_last_errors = {}
    app.state.index_states = {
        key: {"status": "pending", "count": 0, "index_built": False}
        for key, _label, _module in _INDEX_BUILDERS
    }
    app.state.initialization_task = None
    app.state.hourly_refresh_task = None


def _record_build_result(app: FastAPI, key: str, label: str, module: Any, result: Any, verb: str) -> None:
    if isinstance(result, BaseException):
        app.state.index_last_errors[key] = type(result).__name__
        current = app.state.index_states[key]
        if current.get("status") not in _READY_INDEX_STATUSES:
            app.state.index_states[key] = {
                "status": "failed",
                "count": current.get("count", 0),
                "index_built": current.get("index_built", False),
                "last_error": type(result).__name__,
            }
        else:
            # Keep serving the previously activated, internally consistent index.
            current["last_error"] = type(result).__name__
        logger.error("Failed to %s %s index (exception_type=%s)", verb, label, type(result).__name__)
        return

    snapshot = _snapshot_status(module)
    app.state.index_states[key] = snapshot
    app.state.index_last_errors.pop(key, None)


async def _rebuild_all_indexes(app: FastAPI, log_verb: str) -> None:
    """Serialize full rebuilds against the internal local-product refresh."""
    async with app.state.index_refresh_lock:
        results = await asyncio.gather(
            b2b_search.build_index(),
            b2b_product_search.build_index(),
            product_search.build_index(),
            return_exceptions=True,
        )
        for (key, label, module), result in zip(_INDEX_BUILDERS, results):
            _record_build_result(app, key, label, module, result, log_verb)


async def _auto_refresh_indexes(app: FastAPI) -> None:
    """Preserve the existing hourly all-index refresh behavior."""
    while True:
        await asyncio.sleep(3600)
        logger.info("Auto-refreshing Search AI indexes in the background...")
        await _rebuild_all_indexes(app, "refresh")
        logger.info("Search AI index refresh pass complete")


async def _initialize_search(app: FastAPI) -> None:
    """Initialize only dependencies required by Search AI."""
    try:
        await asyncio.to_thread(connect_sql_server)
        app.state.database_ready = True
        logger.info("Search AI database initialization completed")
    except Exception as err:
        app.state.initialization_error = type(err).__name__
        logger.error("Search AI failed to initialize SQL Server (exception_type=%s)", type(err).__name__)
        return

    try:
        await asyncio.to_thread(warm_up_model)
        app.state.embedding_model_ready = True
        logger.info("Search AI embedding model warmed up")
    except Exception as err:
        app.state.initialization_error = type(err).__name__
        logger.error("Search AI failed to warm up embedding model (exception_type=%s)", type(err).__name__)
        return

    await _rebuild_all_indexes(app, "build")
    # Match the existing behavior: start hourly recovery even if an individual
    # index failed its initial build; readiness will remain 503 for that index.
    app.state.hourly_refresh_task = asyncio.create_task(_auto_refresh_indexes(app))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _initialize_state(app)
    app.state.initialization_task = asyncio.create_task(_initialize_search(app))
    try:
        yield
    finally:
        tasks = [
            task
            for task in (app.state.initialization_task, app.state.hourly_refresh_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def create_app() -> FastAPI:
    application = FastAPI(lifespan=lifespan)

    allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
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

    # These are the existing public route contracts. Search AI intentionally
    # does not register the BrochureIQ router or mount /api/uploads.
    application.include_router(search_health_router, prefix="/api")
    application.include_router(search_router, prefix="/api")
    application.include_router(internal_router)

    @application.get("/health/live")
    async def liveness():
        return {"status": "ok", "service": "search-ai"}

    @application.get("/health/ready")
    async def readiness(request: Request):
        state = request.app.state
        indexes = {key: dict(value) for key, value in state.index_states.items()}
        ready = (
            bool(state.database_ready)
            and bool(state.embedding_model_ready)
            and all(item.get("status") in _READY_INDEX_STATUSES for item in indexes.values())
        )
        body = {
            "status": "ready" if ready else "not_ready",
            "service": "search-ai",
            "checks": {
                "database": "ready" if state.database_ready else "not_ready",
                "embeddingModel": "ready" if state.embedding_model_ready else "not_ready",
                "indexes": indexes,
            },
            "initializationError": state.initialization_error,
        }
        return JSONResponse(status_code=200 if ready else 503, content=body)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logger.info("Search AI listening on 127.0.0.1:%s", PORT)
    uvicorn.run("app.search_main:app", host="127.0.0.1", port=PORT)
