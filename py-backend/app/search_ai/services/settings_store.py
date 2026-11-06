"""SQL Server persistence for Search AI runtime settings.

The active configuration is a singleton row in searchai.SearchAISettings.
Every successful update creates a matching immutable snapshot in
searchai.SearchAISettingsHistory in the same database transaction.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.search_ai.settings import SearchRulesSettings
from app.shared.db import get_engine


class SettingsStoreError(RuntimeError):
    """Base exception for settings persistence errors."""


class SettingsNotInitializedError(SettingsStoreError):
    """Raised when migration 003 has not seeded the active settings row."""


class SettingsVersionConflictError(SettingsStoreError):
    """Raised when a caller attempts to update a stale settings version."""


_CURRENT_SETTINGS_SQL = text(
    """
    SELECT [Version], [SettingsJson], [UpdatedAtUtc], [UpdatedBy]
    FROM [searchai].[SearchAISettings]
    WHERE [SettingId] = 1
    """
)

_LOCK_CURRENT_VERSION_SQL = text(
    """
    SELECT [Version]
    FROM [searchai].[SearchAISettings] WITH (UPDLOCK, HOLDLOCK)
    WHERE [SettingId] = 1
    """
)

_UPDATE_CURRENT_SETTINGS_SQL = text(
    """
    UPDATE [searchai].[SearchAISettings]
    SET [Version] = :new_version,
        [SettingsJson] = :settings_json,
        [UpdatedAtUtc] = SYSUTCDATETIME(),
        [UpdatedBy] = :updated_by
    WHERE [SettingId] = 1
    """
)

_INSERT_HISTORY_SQL = text(
    """
    INSERT INTO [searchai].[SearchAISettingsHistory]
        ([Version], [SettingsJson], [ChangedBy], [ChangeReason])
    VALUES
        (:new_version, :settings_json, :changed_by, :change_reason)
    """
)

_HISTORY_SQL = text(
    """
    SELECT TOP (:limit)
        [HistoryId], [Version], [SettingsJson], [ChangedAtUtc],
        [ChangedBy], [ChangeReason]
    FROM [searchai].[SearchAISettingsHistory]
    ORDER BY [Version] DESC
    """
)


def _settings_from_json(raw_json: str) -> SearchRulesSettings:
    """Parse and validate persisted JSON before it leaves the store."""
    try:
        return SearchRulesSettings.model_validate_json(raw_json)
    except Exception as exc:
        # Do not return malformed or unsupported configuration to callers.
        raise SettingsStoreError("Persisted Search AI settings failed validation") from exc


def get_current_settings() -> dict[str, Any]:
    """Return the active validated settings and audit metadata."""
    engine = get_engine()
    with engine.connect() as connection:
        row = connection.execute(_CURRENT_SETTINGS_SQL).mappings().first()

    if row is None:
        raise SettingsNotInitializedError(
            "Search AI settings are not initialized; apply migration 003 first"
        )

    return {
        "version": int(row["Version"]),
        "settings": _settings_from_json(row["SettingsJson"]),
        "updated_at_utc": row["UpdatedAtUtc"],
        "updated_by": row["UpdatedBy"],
    }


def update_settings(
    settings: SearchRulesSettings,
    *,
    expected_version: int,
    changed_by: str,
    change_reason: str | None = None,
) -> dict[str, Any]:
    """Persist a new version and its history snapshot atomically.

    UPDLOCK + HOLDLOCK serializes updates to the singleton row. The expected
    version check prevents a stale admin form from overwriting newer changes.
    """
    if expected_version < 1:
        raise ValueError("expected_version must be at least 1")

    settings_json = settings.model_dump_json()
    engine = get_engine()

    with engine.begin() as connection:
        current_row = (
            connection.execute(_LOCK_CURRENT_VERSION_SQL).mappings().first()
        )
        if current_row is None:
            raise SettingsNotInitializedError(
                "Search AI settings are not initialized; apply migration 003 first"
            )

        current_version = int(current_row["Version"])
        if current_version != expected_version:
            raise SettingsVersionConflictError(
                f"Settings version is {current_version}, not {expected_version}"
            )

        new_version = current_version + 1
        parameters = {
            "new_version": new_version,
            "settings_json": settings_json,
            "updated_by": changed_by,
            "changed_by": changed_by,
            "change_reason": change_reason,
        }

        update_result = connection.execute(
            _UPDATE_CURRENT_SETTINGS_SQL, parameters
        )
        if update_result.rowcount != 1:
            raise SettingsStoreError("Active Search AI settings row was not updated")

        connection.execute(_INSERT_HISTORY_SQL, parameters)

    # Read the committed row back, including SQL Server's generated timestamp.
    return get_current_settings()


def get_settings_history(limit: int = 50) -> list[dict[str, Any]]:
    """Return the newest settings snapshots first."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")

    engine = get_engine()
    with engine.connect() as connection:
        rows = connection.execute(_HISTORY_SQL, {"limit": limit}).mappings().all()

    history: list[dict[str, Any]] = []
    for row in rows:
        history.append(
            {
                "history_id": int(row["HistoryId"]),
                "version": int(row["Version"]),
                "settings": _settings_from_json(row["SettingsJson"]),
                "changed_at_utc": row["ChangedAtUtc"],
                "changed_by": row["ChangedBy"],
                "change_reason": row["ChangeReason"],
            }
        )
    return history
