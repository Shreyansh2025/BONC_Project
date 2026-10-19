"""Internal Search AI client used by the independently running BrochureIQ app.

The client intentionally does not import Search AI's in-process index builder.
That index lives in a separate process after the microservice split.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx

from app.logger import logger

REFRESH_PATH = "/internal/v1/indexes/local-products/refresh"
ALLOWED_REASONS = {"products_saved", "product_deleted"}
REFRESH_TIMEOUT = httpx.Timeout(
    connect=2.0,
    read=60.0,
    write=2.0,
    pool=2.0,
)


async def refresh_local_product_index(reason: str) -> bool:
    """Ask Search AI to rebuild its local-product index after a DB commit.

    Returns True only when Search AI confirms success with HTTP 200. Failures
    are logged and returned as False so a committed public BrochureIQ operation
    keeps its existing response contract. The caller must call this function
    only after the relevant database transaction has committed.
    """
    if reason not in ALLOWED_REASONS:
        logger.error("Skipping local-product index refresh: unsupported reason")
        return False

    base_url = os.getenv("SEARCH_INTERNAL_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("SEARCH_INTERNAL_API_TOKEN", "").strip()
    if not base_url or not token:
        logger.error(
            "Local-product index refresh is not configured; set "
            "SEARCH_INTERNAL_BASE_URL and SEARCH_INTERNAL_API_TOKEN"
        )
        return False

    endpoint = f"{base_url}{REFRESH_PATH}"
    event_id = uuid.uuid4().hex
    payload = {"event_id": event_id, "reason": reason}
    headers = {"Authorization": f"Bearer {token}"}

    # One retry for transient transport failures or 5xx responses. The same
    # event_id is retained across retries for log correlation.
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=REFRESH_TIMEOUT) as client:
                response = await client.post(endpoint, json=payload, headers=headers)

            # The internal contract defines HTTP 200 as "index built and
            # activated". Do not depend on or forward the response JSON shape.
            if response.status_code == 200:
                logger.info(
                    "Local-product index refresh completed (event_id=%s, reason=%s)",
                    event_id,
                    reason,
                )
                return True

            if response.status_code >= 500 and attempt == 0:
                logger.warning(
                    "Search AI refresh returned HTTP %s; retrying once "
                    "(event_id=%s)",
                    response.status_code,
                    event_id,
                )
                await asyncio.sleep(0.5)
                continue

            logger.error(
                "Search AI refresh failed with HTTP %s (event_id=%s)",
                response.status_code,
                event_id,
            )
            return False

        except httpx.TransportError as exc:
            if attempt == 0:
                logger.warning(
                    "Search AI refresh transport failure (%s); retrying once "
                    "(event_id=%s)",
                    type(exc).__name__,
                    event_id,
                )
                await asyncio.sleep(0.5)
                continue

            logger.error(
                "Search AI refresh failed after retry (%s, event_id=%s)",
                type(exc).__name__,
                event_id,
            )
            return False
        except Exception as exc:
            # A bad URL/configuration or unexpected client error must not turn
            # an already-committed public product operation into HTTP 500.
            logger.error(
                "Search AI refresh failed unexpectedly (%s, event_id=%s)",
                type(exc).__name__,
                event_id,
            )
            return False

    return False
