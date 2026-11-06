"""API tests for the protected Search AI admin settings endpoints."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.search_ai.routes.settings import router
from app.search_ai.settings import SearchRulesSettings
from app.search_ai.services import settings_store


class SearchSettingsApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer test-admin-token"}

    def test_admin_api_is_not_enabled_without_dedicated_token(self):
        with patch.dict(os.environ, {"SEARCH_INTERNAL_API_TOKEN": "test-admin-token"}, clear=True):
            response = self.client.get("/api/admin/search-settings", headers=self.auth)
        self.assertEqual(response.status_code, 503)

    def test_settings_get_requires_bearer_authentication(self):
        with patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "test-admin-token"}, clear=True):
            response = self.client.get("/api/admin/search-settings")
        self.assertEqual(response.status_code, 401)

    def test_settings_get_returns_current_settings(self):
        expected = {
            "version": 1,
            "settings": SearchRulesSettings(),
            "updated_at_utc": "2026-11-06T12:00:00",
            "updated_by": "system:initial",
        }
        with (
            patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "test-admin-token"}, clear=True),
            patch.object(settings_store, "get_current_settings", return_value=expected),
        ):
            response = self.client.get("/api/admin/search-settings", headers=self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)
        self.assertEqual(response.json()["settings"]["semantic_candidate_limit"], 50)

    def test_settings_put_returns_conflict_for_stale_version(self):
        payload = {
            "expected_version": 1,
            "settings": SearchRulesSettings().model_dump(),
            "change_reason": "test",
        }
        with (
            patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "test-admin-token"}, clear=True),
            patch.object(
                settings_store,
                "update_settings",
                side_effect=settings_store.SettingsVersionConflictError("current version is 2"),
            ),
        ):
            response = self.client.put(
                "/api/admin/search-settings", headers=self.auth, json=payload
            )

        self.assertEqual(response.status_code, 409)

    def test_settings_put_rejects_unknown_fields(self):
        payload = {
            "expected_version": 1,
            "settings": SearchRulesSettings().model_dump(),
            "unexpected": "not allowed",
        }
        with patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "test-admin-token"}, clear=True):
            response = self.client.put(
                "/api/admin/search-settings", headers=self.auth, json=payload
            )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
