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


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dir(UPLOADS_DIR)

    # Connect SQL Server (non-blocking — routes will fail gracefully if not connected)
    async def _connect():
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
app.mount(f"/api/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(health_router, prefix="/api")
app.include_router(brochure_router, prefix="/api")
app.include_router(search_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    raw_port = os.getenv("PORT")
    if not raw_port:
        raise RuntimeError("PORT environment variable is required but was not provided.")

    port = int(raw_port)
    if port <= 0:
        raise RuntimeError(f'Invalid PORT value: "{raw_port}"')

    logger.info(f"Server listening on port {port}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)