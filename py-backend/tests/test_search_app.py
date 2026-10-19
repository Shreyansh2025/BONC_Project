"""Tests for the standalone Search AI application and existing public contract."""
from __future__ import annotations

import sys
import types
import time
import unittest
from unittest.mock import AsyncMock, patch

# The audit/container environment may not have FAISS installed. Search route
# contract tests mock index builders and search methods, so provide only the
# import-time module surface needed when FAISS itself is unavailable.
try:
    import faiss  # noqa: F401
except ModuleNotFoundError:
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.Index = object
    fake_faiss.IndexFlatL2 = object
    sys.modules["faiss"] = fake_faiss

from fastapi.testclient import TestClient


class ClientHostOverrideMiddleware:
    """Set an explicit ASGI client host without newer TestClient kwargs.

    This keeps tests compatible with the older Starlette version used by the
    project's Windows environment as well as newer versions.
    """

    def __init__(self, app, client_host: str):
        self.app = app
        self.client_host = client_host

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            scope = dict(scope)
            scope["client"] = (self.client_host, 12345)
        await self.app(scope, receive, send)


def _test_client(app, client_host: str = "127.0.0.1") -> TestClient:
    app.add_middleware(ClientHostOverrideMiddleware, client_host=client_host)
    return TestClient(app)

from app import search_main
from app.routes import search as search_route_module


class SearchAppTestCase(unittest.TestCase):
    def _startup_patches(self):
        patches = [
            patch("app.search_main.connect_sql_server"),
            patch("app.search_main.warm_up_model"),
            patch("app.search_main.b2b_search.build_index", new_callable=AsyncMock),
            patch("app.search_main.b2b_product_search.build_index", new_callable=AsyncMock),
            patch("app.search_main.product_search.build_index", new_callable=AsyncMock),
        ]
        return patches

    def test_app_lifespan_reaches_ready_when_all_indexes_build(self):
        patches = self._startup_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with _test_client(search_main.create_app()) as client:
                deadline = time.monotonic() + 2
                response = client.get("/health/ready")
                while response.status_code == 503 and time.monotonic() < deadline:
                    time.sleep(0.01)
                    response = client.get("/health/ready")
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data["service"], "search-ai")
                self.assertEqual(data["checks"]["database"], "ready")
                self.assertEqual(data["checks"]["embeddingModel"], "ready")
                self.assertTrue(all(
                    item["status"] == "ready_empty"
                    for item in data["checks"]["indexes"].values()
                ))

    def test_readiness_is_not_ready_before_indexes_finish(self):
        with self._startup_patches()[0], self._startup_patches()[1], \
             self._startup_patches()[2], self._startup_patches()[3], \
             self._startup_patches()[4]:
            client = _test_client(search_main.create_app())
            with client:
                response = client.get("/health/live")
                self.assertEqual(response.status_code, 200)
                # The initial index build runs in a background task. Reset a
                # representative readiness state to verify 503 accurately.
                client.app.state.index_states["b2bCompanies"]["status"] = "pending"
                response = client.get("/health/ready")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["status"], "not_ready")

    def test_public_search_empty_contract_has_existing_five_fields(self):
        patches = self._startup_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with _test_client(search_main.create_app()) as client:
                response = client.post("/api/search", json={"SearchText": "   "})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json(),
                    {
                        "Success": True,
                        "Message": "No search query provided",
                        "Type": "success",
                        "Data": [],
                        "TotalCount": 0,
                    },
                )

    def test_public_search_result_contract_is_preserved(self):
        patches = self._startup_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(search_route_module.b2b_search, "search_companies_sync", return_value=[
                 {"businessName": "Acme", "imagePath": "/company.png", "matchPercentage": 90}
             ]), \
             patch.object(search_route_module.b2b_product_search, "search_products_sync", return_value=[]), \
             patch.object(search_route_module, "search_local_products_sync", return_value=[]):
            with _test_client(search_main.create_app()) as client:
                response = client.post(
                    "/api/search",
                    json={"SearchText": "acme", "PageNo": 1, "PageSize": 10, "Type": "business"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(set(data), {"Success", "Message", "Type", "Data", "TotalCount"})
                self.assertEqual(data["Success"], True)
                self.assertEqual(data["TotalCount"], 1)
                self.assertEqual(data["Data"][0]["type"], "business")
                self.assertEqual(data["Data"][0]["image"], "/company.png")
                self.assertNotIn("imagePath", data["Data"][0])

    def test_search_app_does_not_register_brochure_routes_or_upload_mount(self):
        app = search_main.create_app()
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        self.assertIn("/api/search", paths)
        self.assertIn("/api/debug/index-status", paths)
        self.assertNotIn("/api/process", paths)
        self.assertNotIn("/api/uploads", paths)


if __name__ == "__main__":
    unittest.main()
