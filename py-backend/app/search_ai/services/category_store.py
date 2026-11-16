"""SQL Server persistence for dynamic search categories and normalized documents."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.search_ai.platform.models import CategoryConfig, SearchDocument
from app.shared.db import get_engine


class CategoryNotFoundError(LookupError):
    pass


class CategoryAlreadyExistsError(ValueError):
    pass


class CategoryStoreError(RuntimeError):
    pass


_GET_CATEGORY = text("""
    SELECT CategoryId, ConfigJson, Status, ConfigVersion, IndexVersion,
           UpdatedAtUtc, UpdatedBy
    FROM searchai.SearchCategories
    WHERE CategoryId = :category_id
""")

_LIST_CATEGORIES = text("""
    SELECT CategoryId, ConfigJson, Status, ConfigVersion, IndexVersion,
           UpdatedAtUtc, UpdatedBy
    FROM searchai.SearchCategories
    ORDER BY DisplayName
""")

_INSERT_CATEGORY = text("""
    INSERT INTO searchai.SearchCategories
        (CategoryId, DisplayName, ConfigJson, Status, ConfigVersion, IndexVersion, UpdatedBy)
    VALUES
        (:category_id, :display_name, :config_json, :status, 1, 0, :updated_by)
""")

_UPDATE_CATEGORY = text("""
    UPDATE searchai.SearchCategories
    SET DisplayName = :display_name,
        ConfigJson = :config_json,
        Status = :status,
        ConfigVersion = ConfigVersion + 1,
        IndexVersion = IndexVersion + 1,
        UpdatedAtUtc = SYSUTCDATETIME(),
        UpdatedBy = :updated_by
    WHERE CategoryId = :category_id AND ConfigVersion = :expected_version
""")

_DELETE_DOCUMENTS = text("DELETE FROM searchai.SearchDocuments WHERE CategoryId = :category_id")

_INSERT_DOCUMENT = text("""
    INSERT INTO searchai.SearchDocuments
        (CategoryId, DocumentId, Title, FieldsJson, MetadataJson)
    VALUES
        (:category_id, :document_id, :title, :fields_json, :metadata_json)
""")

_UPSERT_DOCUMENT = text("""
    MERGE searchai.SearchDocuments AS target
    USING (
        SELECT :category_id AS CategoryId,
               :document_id AS DocumentId,
               :title AS Title,
               :fields_json AS FieldsJson,
               :metadata_json AS MetadataJson
    ) AS source
    ON target.CategoryId = source.CategoryId
       AND target.DocumentId = source.DocumentId
    WHEN MATCHED THEN
        UPDATE SET Title = source.Title,
                   FieldsJson = source.FieldsJson,
                   MetadataJson = source.MetadataJson,
                   UpdatedAtUtc = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (CategoryId, DocumentId, Title, FieldsJson, MetadataJson)
        VALUES (source.CategoryId, source.DocumentId, source.Title,
                source.FieldsJson, source.MetadataJson);
""")

_LIST_DOCUMENTS = text("""
    SELECT DocumentId, Title, FieldsJson, MetadataJson
    FROM searchai.SearchDocuments
    WHERE CategoryId = :category_id
    ORDER BY DocumentId
""")

_BUMP_INDEX_VERSION = text("""
    UPDATE searchai.SearchCategories
    SET IndexVersion = IndexVersion + 1,
        UpdatedAtUtc = SYSUTCDATETIME(),
        UpdatedBy = :updated_by
    WHERE CategoryId = :category_id
""")


def _category_from_row(row: Any) -> dict[str, Any]:
    config = CategoryConfig.model_validate_json(row["ConfigJson"])
    return {
        "config": config,
        "status": row["Status"],
        "config_version": int(row["ConfigVersion"]),
        "index_version": int(row["IndexVersion"]),
        "updated_at_utc": row["UpdatedAtUtc"],
        "updated_by": row["UpdatedBy"],
    }


def get_category(category_id: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(_GET_CATEGORY, {"category_id": category_id}).mappings().first()
    if row is None:
        raise CategoryNotFoundError(category_id)
    return _category_from_row(row)


def list_categories() -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(_LIST_CATEGORIES).mappings().all()
    return [_category_from_row(row) for row in rows]


def create_category(config: CategoryConfig, *, updated_by: str) -> dict[str, Any]:
    values = {
        "category_id": config.category_id,
        "display_name": config.display_name,
        "config_json": config.model_dump_json(),
        "status": "active" if config.active else "disabled",
        "updated_by": updated_by,
    }
    try:
        with get_engine().begin() as connection:
            connection.execute(_INSERT_CATEGORY, values)
    except Exception as exc:
        # Keep database-specific duplicate-key errors out of the API response.
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower() or "2627" in str(exc):
            raise CategoryAlreadyExistsError(config.category_id) from exc
        raise CategoryStoreError("Unable to create search category") from exc
    return get_category(config.category_id)


def update_category(
    config: CategoryConfig, *, expected_version: int, updated_by: str
) -> dict[str, Any]:
    values = {
        "category_id": config.category_id,
        "display_name": config.display_name,
        "config_json": config.model_dump_json(),
        "status": "active" if config.active else "disabled",
        "expected_version": expected_version,
        "updated_by": updated_by,
    }
    with get_engine().begin() as connection:
        result = connection.execute(_UPDATE_CATEGORY, values)
        if result.rowcount != 1:
            current = connection.execute(
                text("SELECT ConfigVersion FROM searchai.SearchCategories WHERE CategoryId = :category_id"),
                {"category_id": config.category_id},
            ).scalar_one_or_none()
            if current is None:
                raise CategoryNotFoundError(config.category_id)
            raise CategoryStoreError(f"Configuration version conflict; current version is {current}")
    return get_category(config.category_id)


def list_documents(category_id: str) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(_LIST_DOCUMENTS, {"category_id": category_id}).mappings().all()
    documents: list[dict[str, Any]] = []
    for row in rows:
        documents.append({
            "document_id": str(row["DocumentId"]),
            "title": row["Title"] or "",
            "fields": json.loads(row["FieldsJson"]),
            "metadata": json.loads(row["MetadataJson"]),
        })
    return documents


def replace_documents(
    category_id: str,
    documents: list[SearchDocument],
    *,
    updated_by: str,
    replace_all: bool = False,
) -> int:
    """Upsert documents (or replace the full snapshot) and bump index version."""
    if not documents:
        raise ValueError("At least one document is required")
    ids = [doc.document_id for doc in documents]
    if len(ids) != len(set(ids)):
        raise ValueError("Document IDs must be unique in one ingestion batch")

    with get_engine().begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM searchai.SearchCategories WHERE CategoryId = :category_id"),
            {"category_id": category_id},
        ).first()
        if exists is None:
            raise CategoryNotFoundError(category_id)

        if replace_all:
            connection.execute(_DELETE_DOCUMENTS, {"category_id": category_id})
        connection.execute(
            _INSERT_DOCUMENT if replace_all else _UPSERT_DOCUMENT,
            [{
                "category_id": category_id,
                "document_id": doc.document_id,
                "title": doc.title,
                "fields_json": json.dumps(doc.fields, ensure_ascii=False),
                "metadata_json": json.dumps(doc.metadata, ensure_ascii=False),
            } for doc in documents],
        )
        connection.execute(_BUMP_INDEX_VERSION, {
            "category_id": category_id,
            "updated_by": updated_by,
        })
    return len(documents)
