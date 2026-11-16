"""Admin and search APIs for the configuration-driven search platform."""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.search_ai.platform.models import CategoryConfig, IngestRequest, PlatformSearchRequest, SearchFeedbackRequest
from app.search_ai.services import category_store, platform_search, source_sync

router = APIRouter(prefix="/api/platform", tags=["Search AI platform"])
admin_router = APIRouter(prefix="/api/admin/platform", tags=["Search AI platform admin"])


class CategoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    config: CategoryConfig
    change_reason: str | None = Field(default=None, max_length=1000)


def _require_admin(authorization: str | None) -> str:
    expected = os.getenv("SEARCH_ADMIN_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Search AI admin API is not configured")
    parts = (authorization or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Bearer authentication required")
    if not hmac.compare_digest(parts[1].strip().encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return os.getenv("SEARCH_ADMIN_ACTOR", "admin:token").strip() or "admin:token"


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="Search category not found")


@router.get("/categories")
def public_categories():
    return [
        {
            "category_id": item["config"].category_id,
            "display_name": item["config"].display_name,
            "description": item["config"].description,
        }
        for item in category_store.list_categories()
        if (
            item["status"] == "active"
            and item["config"].active
            and item["config"].public_search_enabled
        )
    ]


@router.post("/search")
def platform_search_endpoint(body: PlatformSearchRequest):
    try:
        category = category_store.get_category(body.category)
        if category["status"] != "active" or not category["config"].active:
            raise HTTPException(status_code=404, detail="Search category not found")
        if not category["config"].public_search_enabled:
            raise HTTPException(status_code=403, detail="This search category is not publicly available")
        return platform_search.search_category(
            query=body.query,
            category_id=body.category,
            page=body.page,
            page_size=body.page_size,
        )
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/search")
def admin_search_category(
    body: PlatformSearchRequest,
    authorization: str | None = Header(default=None),
):
    _require_admin(authorization)
    try:
        return platform_search.search_category(
            query=body.query,
            category_id=body.category,
            page=body.page,
            page_size=body.page_size,
        )
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/sources")
def admin_list_source_profiles(authorization: str | None = Header(default=None)):
    """List reusable server-configured SQL connections without returning their secrets."""
    _require_admin(authorization)
    return {"items": source_sync.list_source_profiles()}


@admin_router.get("/sources/{profile_id}/schema")
def admin_discover_source_schema(
    profile_id: str,
    schema: str | None = Query(default=None, min_length=1, max_length=128),
    authorization: str | None = Header(default=None),
):
    """Test a named SQL connection and discover accessible schemas, tables and columns."""
    _require_admin(authorization)
    try:
        return source_sync.discover_source_schema(profile_id, schema=schema)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        # Driver messages often contain server, database or login details. Keep
        # them in server logs only; never echo connection diagnostics to browsers.
        raise HTTPException(
            status_code=502,
            detail="Unable to inspect this SQL source. Check that the profile is valid and has metadata/read access.",
        ) from exc


@admin_router.get("/categories")
def admin_list_categories(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return category_store.list_categories()


@admin_router.post("/categories", status_code=201)
def admin_create_category(
    body: CategoryConfig,
    authorization: str | None = Header(default=None),
):
    actor = _require_admin(authorization)
    try:
        return category_store.create_category(body, updated_by=actor)
    except category_store.CategoryAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail="Category ID already exists") from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="Unable to create category") from exc


@admin_router.put("/categories/{category_id}")
def admin_update_category(
    category_id: str,
    body: CategoryUpdateRequest,
    authorization: str | None = Header(default=None),
):
    actor = _require_admin(authorization)
    if body.config.category_id != category_id:
        raise HTTPException(status_code=422, detail="URL category_id must match config.category_id")
    try:
        result = category_store.update_category(
            body.config,
            expected_version=body.expected_version,
            updated_by=actor,
            change_reason=body.change_reason,
        )
        platform_search.invalidate_category_index(category_id)
        return result
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc
    except category_store.CategoryStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.post("/categories/{category_id}/feedback", status_code=201)
def admin_record_category_feedback(
    category_id: str,
    body: SearchFeedbackRequest,
    authorization: str | None = Header(default=None),
):
    actor = _require_admin(authorization)
    try:
        return category_store.record_feedback(
            category_id,
            query=body.query,
            document_id=body.document_id,
            is_relevant=body.is_relevant,
            comment=body.comment,
            created_by=actor,
            result_rank=body.result_rank,
            result_score=body.result_score,
            config_version=body.config_version,
            index_version=body.index_version,
        )
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc


@admin_router.get("/categories/{category_id}/feedback")
def admin_get_category_feedback(
    category_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    _require_admin(authorization)
    try:
        return category_store.get_feedback(category_id, limit=limit)
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc


@admin_router.get("/categories/{category_id}/history")
def admin_category_history(
    category_id: str,
    authorization: str | None = Header(default=None),
):
    _require_admin(authorization)
    try:
        return {"items": category_store.get_category_history(category_id)}
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc


@admin_router.post("/categories/{category_id}/ingest")
def admin_ingest_documents(
    category_id: str,
    body: IngestRequest,
    authorization: str | None = Header(default=None),
):
    actor = _require_admin(authorization)
    try:
        category_store.get_category(category_id)
        category_config = category_store.get_category(category_id)["config"]
        allowed_fields = {field.key for field in category_config.fields}
        for document in body.documents:
            unknown_fields = set(document.fields) - allowed_fields
            if unknown_fields:
                raise ValueError(
                    "Document contains fields not configured for this category: "
                    + ", ".join(sorted(unknown_fields))
                )
        count = category_store.replace_documents(
            category_id,
            body.documents,
            updated_by=actor,
            replace_all=body.replace_all,
        )
        platform_search.invalidate_category_index(category_id)
        return {"status": "ok", "category_id": category_id, "documents_ingested": count}
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin_router.post("/categories/{category_id}/sync")
def admin_sync_category(
    category_id: str,
    authorization: str | None = Header(default=None),
):
    actor = _require_admin(authorization)
    try:
        category = category_store.get_category(category_id)
        count = source_sync.sync_sqlserver_category(category["config"], updated_by=actor)
        platform_search.invalidate_category_index(category_id)
        return {"status": "ok", "category_id": category_id, "documents_synced": count}
    except category_store.CategoryNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
