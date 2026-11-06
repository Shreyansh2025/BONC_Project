"""Unit tests for SQL-backed Search AI settings persistence."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app.search_ai.settings import SearchRulesSettings
from app.search_ai.services import settings_store


class SearchSettingsStoreTests(unittest.TestCase):
    def test_get_current_settings_returns_validated_record(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = {
            "Version": 3,
            "SettingsJson": SearchRulesSettings().model_dump_json(),
            "UpdatedAtUtc": "2026-11-06T12:00:00",
            "UpdatedBy": "admin:test",
        }

        with patch.object(settings_store, "get_engine", return_value=engine):
            result = settings_store.get_current_settings()

        self.assertEqual(result["version"], 3)
        self.assertIsInstance(result["settings"], SearchRulesSettings)
        self.assertEqual(result["updated_by"], "admin:test")

    def test_get_current_settings_fails_when_migration_has_not_seeded_row(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = None

        with patch.object(settings_store, "get_engine", return_value=engine):
            with self.assertRaises(settings_store.SettingsNotInitializedError):
                settings_store.get_current_settings()

    def test_update_settings_increments_version_and_writes_history(self):
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = {
            "Version": 1
        }
        # The UPDATE statement must report that its singleton row was updated.
        connection.execute.return_value.rowcount = 1

        committed_row = {
            "Version": 2,
            "SettingsJson": SearchRulesSettings(semantic_candidate_limit=60).model_dump_json(),
            "UpdatedAtUtc": "2026-11-06T12:00:00",
            "UpdatedBy": "admin:test",
        }
        read_connection = engine.connect.return_value.__enter__.return_value
        read_connection.execute.return_value.mappings.return_value.first.return_value = committed_row

        with patch.object(settings_store, "get_engine", return_value=engine):
            result = settings_store.update_settings(
                SearchRulesSettings(semantic_candidate_limit=60),
                expected_version=1,
                changed_by="admin:test",
                change_reason="test update",
            )

        self.assertEqual(result["version"], 2)
        self.assertEqual(result["settings"].semantic_candidate_limit, 60)
        self.assertEqual(connection.execute.call_count, 3)

    def test_update_rejects_stale_expected_version_before_writing(self):
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.first.return_value = {
            "Version": 4
        }

        with patch.object(settings_store, "get_engine", return_value=engine):
            with self.assertRaises(settings_store.SettingsVersionConflictError):
                settings_store.update_settings(
                    SearchRulesSettings(),
                    expected_version=3,
                    changed_by="admin:test",
                )

        self.assertEqual(connection.execute.call_count, 1)

    def test_history_returns_validated_settings(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.mappings.return_value.all.return_value = [
            {
                "HistoryId": 10,
                "Version": 2,
                "SettingsJson": SearchRulesSettings().model_dump_json(),
                "ChangedAtUtc": "2026-11-06T12:00:00",
                "ChangedBy": "admin:test",
                "ChangeReason": "test",
            }
        ]

        with patch.object(settings_store, "get_engine", return_value=engine):
            result = settings_store.get_settings_history(limit=10)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["version"], 2)
        self.assertIsInstance(result[0]["settings"], SearchRulesSettings)

    def test_history_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            settings_store.get_settings_history(limit=201)


if __name__ == "__main__":
    unittest.main()
