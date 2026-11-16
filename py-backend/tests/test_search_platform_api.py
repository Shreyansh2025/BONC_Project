"""API contract and privacy tests for the generic Search AI platform."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.search_ai.routes.platform import admin_router, router
from app.search_ai.services import category_store, platform_search


class GenericSearchPlatformApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.include_router(admin_router)
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer platform-admin-token"}
        self.category = {
            "config": {
                "category_id": "people",
                "display_name": "People",
                "description": "Search BONC people",
                "source": {"type": "push"},
                "fields": [
                    {"key": "name", "source_column": "name", "label": "Name", "role": "title", "weight": 5},
                    {"key": "skills", "source_column": "skills", "label": "Skills", "role": "skill", "weight": 4},
                    {"key": "location", "source_column": "location", "label": "Location", "role": "location", "weight": 1},
                ],
                "active": True,
                "public_search_enabled": False,
            },
            "status": "active",
            "config_version": 1,
            "index_version": 0,
        }

    def test_admin_category_list_requires_token(self):
        with patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "platform-admin-token"}, clear=True):
            response = self.client.get("/api/admin/platform/categories")
        self.assertEqual(response.status_code, 401)

    def test_public_search_rejects_private_category(self):
        with patch.object(category_store, "get_category", return_value=self.category):
            response = self.client.post(
                "/api/platform/search",
                json={"query": "Shreyansh OCR expert", "category": "people"},
            )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_search_private_category(self):
        expected = {
            "query": "Shreyansh OCR expert",
            "category": "people",
            "query_understanding": {},
            "data": [],
            "total_count": 0,
            "page": 1,
            "page_size": 10,
            "index_version": 1,
        }
        with (
            patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "platform-admin-token"}, clear=True),
            patch.object(platform_search, "search_category", return_value=expected),
        ):
            response = self.client.post(
                "/api/admin/platform/search",
                headers=self.auth,
                json={"query": "Shreyansh OCR expert", "category": "people"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["category"], "people")

    def test_admin_category_creation_requires_valid_token(self):
        with patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "platform-admin-token"}, clear=True):
            response = self.client.post(
                "/api/admin/platform/categories",
                json=self.category["config"],
            )
        self.assertEqual(response.status_code, 401)

    def test_admin_category_creation_passes_valid_config_to_store(self):
        expected = {"config": self.category["config"], "status": "active"}
        with (
            patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "platform-admin-token", "SEARCH_ADMIN_ACTOR": "qa"}, clear=True),
            patch.object(category_store, "create_category", return_value=expected) as create_mock,
        ):
            response = self.client.post(
                "/api/admin/platform/categories",
                headers=self.auth,
                json=self.category["config"],
            )
        self.assertEqual(response.status_code, 201, response.text)
        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["updated_by"], "qa")


if __name__ == "__main__":
    unittest.main()
