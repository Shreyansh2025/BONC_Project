"""Tests for the protected Search AI local-product index refresh endpoint."""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

try:
    import faiss  # noqa: F401
except ModuleNotFoundError:
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.Index = object
    fake_faiss.IndexFlatL2 = object
    sys.modules["faiss"] = fake_faiss

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request


class ClientHostOverrideMiddleware:
    """Set an explicit ASGI client host without newer TestClient kwargs."""

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
from app.routes.internal import LocalProductIndexRefreshRequest, refresh_local_product_index


class InternalRefreshTests(unittest.TestCase):
    def _startup_patches(self):
        return (
            patch("app.search_main.connect_sql_server"),
            patch("app.search_main.warm_up_model"),
            patch("app.search_main.b2b_search.build_index", new_callable=AsyncMock),
            patch("app.search_main.b2b_product_search.build_index", new_callable=AsyncMock),
            patch("app.search_main.product_search.build_index", new_callable=AsyncMock),
        )

    def _client(self):
        patches = self._startup_patches()
        stack = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])
        client = _test_client(search_main.create_app())
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        # Don't let background initialization overlap route tests; it uses the
        # same app-state refresh lock as manual internal refreshes.
        import time
        deadline = time.monotonic() + 2
        response = client.get("/health/ready")
        while response.status_code == 503 and time.monotonic() < deadline:
            time.sleep(0.01)
            response = client.get("/health/ready")
        return client

    def test_refresh_succeeds_with_valid_token(self):
        client = self._client()
        client.app.state.database_ready = True
        client.app.state.embedding_model_ready = True
        with patch.dict("os.environ", {"SEARCH_INTERNAL_API_TOKEN": "test-secret"}), \
             patch("app.routes.internal.product_search.build_index", new_callable=AsyncMock) as build, \
             patch("app.routes.internal.product_search.index_status", return_value={"count": 1, "index_built": True}):
            response = client.post(
                "/internal/v1/indexes/local-products/refresh",
                headers={"Authorization": "Bearer test-secret"},
                json={"event_id": "event-1", "reason": "products_saved"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["scope"], "local_products")
        build.assert_awaited_once()

    def test_missing_and_invalid_tokens_are_rejected(self):
        client = self._client()
        client.app.state.database_ready = True
        client.app.state.embedding_model_ready = True
        with patch.dict("os.environ", {"SEARCH_INTERNAL_API_TOKEN": "test-secret"}):
            missing = client.post(
                "/internal/v1/indexes/local-products/refresh",
                json={"event_id": "missing-token", "reason": "products_saved"},
            )
            invalid = client.post(
                "/internal/v1/indexes/local-products/refresh",
                headers={"Authorization": "Bearer wrong"},
                json={"event_id": "invalid-token", "reason": "products_saved"},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 403)

    def test_non_loopback_client_is_rejected_even_with_valid_token(self):
        with patch.dict("os.environ", {"SEARCH_INTERNAL_API_TOKEN": "test-secret"}):
            client = _test_client(search_main.create_app(), client_host="192.0.2.10")
            # Configure the instance before entering lifespan.
            patches = self._startup_patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4], client:
                client.app.state.database_ready = True
                client.app.state.embedding_model_ready = True
                response = client.post(
                    "/internal/v1/indexes/local-products/refresh",
                    headers={"Authorization": "Bearer test-secret"},
                    json={"event_id": "remote-client", "reason": "products_saved"},
                )
                self.assertEqual(response.status_code, 403)

    def test_concurrent_duplicate_event_refreshes_once(self):
        # Exercise both refreshes in one asyncio loop and with one shared lock.
        # This specifically models retries that reuse the same event_id.
        app = search_main.create_app()
        build_calls = 0

        async def slow_build():
            nonlocal build_calls
            build_calls += 1
            await asyncio.sleep(0.05)

        def make_request() -> Request:
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/internal/v1/indexes/local-products/refresh",
                "raw_path": b"/internal/v1/indexes/local-products/refresh",
                "query_string": b"",
                "headers": [(b"authorization", b"Bearer test-secret")],
                "server": ("127.0.0.1", 8002),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "app": app,
            }
            return Request(scope)

        async def run_two():
            app.state.database_ready = True
            app.state.embedding_model_ready = True
            app.state.index_refresh_lock = asyncio.Lock()
            app.state.completed_refresh_events = __import__("collections").OrderedDict()
            app.state.index_last_errors = {}
            app.state.index_states = {
                "b2bCompanies": {"status": "ready_empty", "count": 0, "index_built": False},
                "b2bProducts": {"status": "ready_empty", "count": 0, "index_built": False},
                "localProducts": {"status": "ready_empty", "count": 0, "index_built": False},
            }
            body_a = LocalProductIndexRefreshRequest(event_id="same-event", reason="products_saved")
            body_b = LocalProductIndexRefreshRequest(event_id="same-event", reason="products_saved")
            with patch.dict("os.environ", {"SEARCH_INTERNAL_API_TOKEN": "test-secret"}), \
                 patch("app.routes.internal.product_search.build_index", new=slow_build), \
                 patch("app.routes.internal.product_search.index_status", return_value={"count": 1, "index_built": True}):
                return await asyncio.gather(
                    refresh_local_product_index(body_a, make_request()),
                    refresh_local_product_index(body_b, make_request()),
                )

        responses = asyncio.run(run_two())
        self.assertEqual(build_calls, 1)
        self.assertEqual(responses[0]["status"], "ok")
        self.assertEqual(responses[1]["status"], "ok")
        self.assertTrue(responses[0].get("duplicate") or responses[1].get("duplicate"))

    def test_refresh_failure_returns_sanitized_error_and_can_retry(self):
        client = self._client()
        client.app.state.database_ready = True
        client.app.state.embedding_model_ready = True
        with patch.dict("os.environ", {"SEARCH_INTERNAL_API_TOKEN": "test-secret"}), \
             patch("app.routes.internal.product_search.build_index", new=AsyncMock(side_effect=RuntimeError("secret SQL detail"))):
            response = client.post(
                "/internal/v1/indexes/local-products/refresh",
                headers={"Authorization": "Bearer test-secret"},
                json={"event_id": "failed-event", "reason": "products_saved"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret SQL detail", response.text)
        self.assertNotIn("failed-event", client.app.state.completed_refresh_events)


if __name__ == "__main__":
    unittest.main()
