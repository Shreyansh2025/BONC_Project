import os
# Allow multiple OpenMP runtimes on Windows (torch + faiss/onnx)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import asyncio
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.config import UPLOADS_DIR  # noqa: E402
from app.db import connect_sql_server  # noqa: E402
from app.logger import logger  # noqa: E402
from app.routes.admin import router as admin_router  # noqa: E402
from app.routes.brochure import router as brochure_router  # noqa: E402
from app.routes.health import router as health_router  # noqa: E402
from app.routes.search import router as search_router  # noqa: E402
from app.utils import b2b_product_search, b2b_search, product_search  # noqa: E402
from app.utils.bg_remover import warm_up as warm_up_bg_remover  # noqa: E402
from app.utils.dynamic_entities import (  # noqa: E402
    init_dynamic_entities,
    rebuild_all_dynamic_indices,
)
from app.utils.embedding_model import warm_up_model  # noqa: E402
from app.utils.image_extractor import ensure_dir  # noqa: E402

# ---------------------------------------------------------------------------
# PORT validation — runs at import time so it is enforced whether the server
# is started via `python -m app.main`, `uvicorn app.main:app`, or Gunicorn.
# ---------------------------------------------------------------------------
_raw_port = os.getenv("PORT", "8000")
try:
    PORT = int(_raw_port)
    if PORT <= 0:
        raise ValueError
except ValueError:
    raise RuntimeError(
        f'Invalid PORT value: "{_raw_port}". '
        "Set PORT to a positive integer in your .env file."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dir(UPLOADS_DIR)

    async def _rebuild_all_indexes(log_verb: str) -> None:
        """Rebuilds all search indexes CONCURRENTLY, including dynamic entities."""
        results = await asyncio.gather(
            b2b_search.build_index(),
            b2b_product_search.build_index(),
            product_search.build_index(),
            rebuild_all_dynamic_indices(),
            return_exceptions=True,
        )
        labels = ["B2B company", "B2B product", "local product", "dynamic entities"]
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to {log_verb} {label} index: {result}")
        return any(isinstance(result, Exception) for result in results)

    async def _auto_refresh_indexes():
        """Refresh indexes hourly and retry a failed refresh once."""
        while True:
            await asyncio.sleep(3600)
            logger.info("Auto-refreshing search indexes in the background...")

            failed = await _rebuild_all_indexes("refresh")

            if not failed:
                logger.info("Search index refresh pass complete.")
                continue

            logger.warning(
                "Index refresh failed; retrying the full refresh in 60 seconds."
            )
            await asyncio.sleep(60)

            retry_failed = await _rebuild_all_indexes("retry refresh")

            if retry_failed:
                logger.error(
                    "Index refresh retry failed; the next scheduled refresh "
                    "will occur in about one hour."
                )
            else:
                logger.info("Search index refresh retry succeeded.")

    async def _connect():
        """Connects to SQL Server, warms up the embedding model, initializes
        dynamic entities, builds all search indexes, then starts the hourly refresh loop."""
        try:
            await asyncio.to_thread(connect_sql_server)
        except Exception as err:
            logger.error(f"Failed to connect to SQL Server: {err}")

        try:
            await asyncio.to_thread(warm_up_model)
        except Exception as err:
            logger.error(f"Failed to warm up embedding model: {err}")
            return

        # Initialize dynamic entities (e.g. people)
        try:
            await init_dynamic_entities()
        except Exception as err:
            logger.error(f"Failed to initialize dynamic entities: {err}")

        # Build in-memory indexes
        await _rebuild_all_indexes("build")

        # Start hourly refresh loop
        asyncio.create_task(_auto_refresh_indexes())

    async def _warm_up_bg_remover():
        try:
            start = time.time()
            await asyncio.to_thread(warm_up_bg_remover)
            logger.info(
                f"rembg model warmed up in {round((time.time() - start) * 1000)}ms"
            )
        except Exception as err:
            logger.error(f"Failed to warm up rembg model: {err}")

    asyncio.create_task(_connect())
    asyncio.create_task(_warm_up_bg_remover())

    yield


app = FastAPI(lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 1)
    logger.info(
        f'{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)'
    )
    return response


# Serve uploaded files at /api/uploads
ensure_dir(UPLOADS_DIR)
app.mount("/api/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(health_router, prefix="/api")
app.include_router(brochure_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Server listening on port {PORT}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT)
