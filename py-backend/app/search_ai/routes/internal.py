"""Protected internal endpoints used for service-to-service operations."""
from __future__ import annotations

import hmac
import ipaddress
import os
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.shared.logger import logger
from app.search_ai.services import product_search

router = APIRouter()


class LocalProductIndexRefreshRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    reason: Literal["products_saved", "product_deleted"]


def _require_loopback_client(request: Request) -> None:
    """Restrict this endpoint to same-host callers as well as token auth."""
    client_host = request.client.host if request.client else None
    try:
        is_loopback = bool(client_host) and ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=403, detail="Internal endpoint is restricted to loopback clients")


def _index_status_snapshot() -> dict:
    snapshot = product_search.index_status()
    count = int(snapshot.get("count", 0))
    built = bool(snapshot.get("index_built", False))
    if built:
        status = "ready"
    elif count == 0:
        # An empty Products table is a valid, successfully built state. The
        # existing debug route retains its historic index_built=False shape.
        status = "ready_empty"
    else:
        status = "failed"
    return {"status": status, **snapshot}


def _remember_completed_event(request: Request, event_id: str, reason: str) -> None:
    completed: OrderedDict[str, str] = request.app.state.completed_refresh_events
    completed[event_id] = reason
    completed.move_to_end(event_id)
    while len(completed) > 512:
        completed.popitem(last=False)


@router.post("/internal/v1/indexes/local-products/refresh")
async def refresh_local_product_index(
    body: LocalProductIndexRefreshRequest,
    request: Request,
):
    """Rebuild and atomically activate the local brochure-product index.

    BrochureIQ calls this only after its product transaction commits. A 200
    means the build completed and its resulting state was activated.
    """
    _require_loopback_client(request)

    expected_token = os.getenv("SEARCH_INTERNAL_API_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Internal index refresh is not configured",
        )

    authorization = request.headers.get("Authorization", "")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Bearer authentication required")
    supplied_token = parts[1].strip()
    if not hmac.compare_digest(supplied_token.encode("utf-8"), expected_token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid internal token")

    if not getattr(request.app.state, "database_ready", False):
        raise HTTPException(status_code=503, detail="Search database is not ready")
    if not getattr(request.app.state, "embedding_model_ready", False):
        raise HTTPException(status_code=503, detail="Search embedding model is not ready")

    # This lock is shared with startup and hourly full-index rebuilds. It
    # prevents two refreshes from activating out-of-order snapshots.
    async with request.app.state.index_refresh_lock:
        completed: OrderedDict[str, str] = request.app.state.completed_refresh_events
        previous_reason = completed.get(body.event_id)
        if previous_reason is not None:
            if previous_reason != body.reason:
                raise HTTPException(
                    status_code=409,
                    detail="event_id was already used for a different refresh reason",
                )
            return {
                "status": "ok",
                "scope": "local_products",
                "event_id": body.event_id,
                "duplicate": True,
            }

        try:
            await product_search.build_index()
        except Exception as err:
            error_type = type(err).__name__
            request.app.state.index_last_errors["localProducts"] = error_type
            current_status = request.app.state.index_states["localProducts"]
            if current_status.get("status") not in {"ready", "ready_empty"}:
                current_status["status"] = "failed"
            current_status["last_error"] = error_type
            logger.error(
                "Local-product index refresh failed (event_id=%s, reason=%s, exception_type=%s)",
                body.event_id,
                body.reason,
                error_type,
            )
            raise HTTPException(
                status_code=500,
                detail="Local product index refresh failed",
            ) from err

        snapshot = _index_status_snapshot()
        request.app.state.index_states["localProducts"] = snapshot
        request.app.state.index_last_errors.pop("localProducts", None)
        _remember_completed_event(request, body.event_id, body.reason)

    logger.info(
        "Local-product index refresh completed (event_id=%s, reason=%s)",
        body.event_id,
        body.reason,
    )
    return {"status": "ok", "scope": "local_products", "event_id": body.event_id}
