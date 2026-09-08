import asyncio
import os
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
from app.routes.brochure import router as brochure_router  # noqa: E402
from app.routes.health import router as health_router  # noqa: E402
from app.routes.search import router as search_router  # noqa: E402
from app.utils import b2b_product_search, b2b_search, product_search  # noqa: E402
from app.utils.bg_remover import warm_up as warm_up_bg_remover  # noqa: E402
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

    async def _auto_refresh_indexes():
        """Rebuilds all three search indexes every hour. Only started after
        the initial connect + index-build pass inside _connect() has run."""
        while True:
            await asyncio.sleep(3600)  # 1 hour
            logger.info("Auto-refreshing search indexes in the background...")

            # Each index refreshed independently — same reasoning as in
            # _connect() below: one index failing to refresh should never
            # stop the others from refreshing.
            try:
                await b2b_search.build_index()
            except Exception as err:
                logger.error(f"Failed to refresh B2B company index: {err}")

            try:
                await b2b_product_search.build_index()
            except Exception as err:
                logger.error(f"Failed to refresh B2B product index: {err}")

            try:
                await product_search.build_index()
            except Exception as err:
                logger.error(f"Failed to refresh local product index: {err}")

            logger.info("Search index refresh pass complete.")

    async def _connect():
        """Connects to SQL Server, warms up the embedding model, builds all
        search indexes, then starts the hourly refresh loop. Routes fail
        gracefully if this task hasn't finished yet."""
        try:
            await asyncio.to_thread(connect_sql_server)
        except Exception as err:
            logger.error(f"Failed to connect to SQL Server: {err}")
            return

        try:
            await asyncio.to_thread(warm_up_model)
        except Exception as err:
            logger.error(f"Failed to warm up embedding model: {err}")
            return  # nothing below can succeed without the model

        # Build the in-memory search indexes (B2B companies, B2B product
        # catalog, AND the brochure-extracted local Products table). Each
        # gets its own try/except: previously these three awaits shared one
        # try/except, so an exception in an earlier build (e.g.
        # b2b_product_search) silently skipped every build after it (e.g.
        # product_search) — both product indexes would end up empty while
        # company search kept working, with only one generic error line to
        # explain why. Now each is independent and logs its own outcome.
        try:
            await b2b_search.build_index()
        except Exception as err:
            logger.error(f"Failed to build B2B company index: {err}")

        try:
            await b2b_product_search.build_index()
        except Exception as err:
            logger.error(f"Failed to build B2B product index: {err}")

        try:
            await product_search.build_index()
        except Exception as err:
            logger.error(f"Failed to build local product index: {err}")

        # Start the hourly refresh loop regardless of which builds above
        # succeeded — a build that failed this pass gets another chance
        # next hour instead of being stuck empty until the next restart.
        asyncio.create_task(_auto_refresh_indexes())

    async def _warm_up_bg_remover():
        try:
            start = time.time()
            # Loads/downloads the rembg model once here, off the request path,
            # so the first "Remove Background" click from a user is fast too.
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

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# The Python API is called server-to-server from the .NET backend, so CORS
# (which is only enforced by browsers) does not apply to those calls.
# allow_origins=["*"] is therefore safe for now.
#
# IMPORTANT: allow_credentials=True is intentionally removed. Combining it
# with allow_origins=["*"] is invalid per the CORS spec and causes browsers
# to reject credentialed requests. When the React frontend is deployed, set
# ALLOWED_ORIGINS in .env to the exact frontend domain and re-enable
# allow_credentials if cookies/auth headers are needed.
# ---------------------------------------------------------------------------
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


# Serve uploaded files at /api/uploads, mirrors express.static
ensure_dir(UPLOADS_DIR)
app.mount("/api/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(health_router, prefix="/api")
app.include_router(brochure_router, prefix="/api")
app.include_router(search_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Server listening on port {PORT}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT)