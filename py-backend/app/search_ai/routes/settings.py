"""Admin-only endpoints for viewing and updating Search AI settings."""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.search_ai.settings import SearchRulesSettings
from app.search_ai.services import settings_store
from app.shared.logger import logger

router = APIRouter(prefix="/api/admin", tags=["Search AI admin settings"])


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    settings: SearchRulesSettings
    change_reason: str | None = Field(default=None, max_length=1000)


def _require_admin_token(authorization: str | None) -> None:
    """Require a dedicated admin token; never reuse the internal service token."""
    expected_token = os.getenv("SEARCH_ADMIN_API_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Search AI admin API is not configured",
        )

    parts = (authorization or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Bearer authentication required")

    supplied_token = parts[1].strip()
    if not hmac.compare_digest(
        supplied_token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _actor_name() -> str:
    """Return the server-configured audit actor, not a client-supplied identity."""
    actor = os.getenv("SEARCH_ADMIN_ACTOR", "admin:token").strip()
    return (actor or "admin:token")[:256]


def _handle_store_error(exc: Exception) -> HTTPException:
    logger.error(
        "Search AI settings operation failed (exception_type=%s)",
        type(exc).__name__,
    )
    return HTTPException(
        status_code=500,
        detail="Search AI settings operation failed",
    )


@router.get("/search-settings")
def read_search_settings(
    authorization: str | None = Header(default=None),
):
    _require_admin_token(authorization)
    try:
        return settings_store.get_current_settings()
    except settings_store.SettingsNotInitializedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.put("/search-settings")
def write_search_settings(
    body: SettingsUpdateRequest,
    authorization: str | None = Header(default=None),
):
    _require_admin_token(authorization)
    try:
        return settings_store.update_settings(
            body.settings,
            expected_version=body.expected_version,
            changed_by=_actor_name(),
            change_reason=body.change_reason,
        )
    except settings_store.SettingsNotInitializedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except settings_store.SettingsVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/search-settings/history")
def read_search_settings_history(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None),
):
    _require_admin_token(authorization)
    try:
        return {"items": settings_store.get_settings_history(limit=limit)}
    except Exception as exc:
        raise _handle_store_error(exc) from exc
