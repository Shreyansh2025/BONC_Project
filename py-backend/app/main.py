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
        the initial indexes have been built successfully inside _connect()."""
        while True:
            await asyncio.sleep(3600)  # 1 hour
            logger.info("Auto-refreshing search indexes in the background...")
            try:
                await b2b_search.build_index()
                await b2b_product_search.build_index()
                await product_search.build_index()
                logger.info("Search indexes refreshed successfully.")
            except Exception as err:
                logger.error(f"Failed to refresh indexes: {err}")

    async def _connect():
        """Connects to SQL Server, warms up the embedding model, builds all
        search indexes, then — and only then — starts the hourly refresh loop.
        Routes fail gracefully if this task hasn't finished yet."""
        try:
            await asyncio.to_thread(connect_sql_server)
        except Exception as err:
            logger.error(f"Failed to connect to SQL Server: {err}")
            return

        # Build the in-memory search indexes (B2B companies, B2B product
        # catalog, AND the brochure-extracted local Products table) once
        # SQL Server is up. Loading the embedding model is CPU/IO work, so
        # it runs off the event loop too. All three share the same
        # SentenceTransformer singleton (see embedding_model.py), so
        # warming it up once here covers all of them.
        try:
            await asyncio.to_thread(warm_up_model)
            await b2b_search.build_index()
            await b2b_product_search.build_index()
            await product_search.build_index()
        except Exception as err:
            logger.error(f"Failed to build search index: {err}")
            return

        # Start the hourly refresh loop only after indexes are ready.
        # Previously this was a top-level task that could fire before the DB
        # was connected, causing the first refresh to crash immediately.
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
