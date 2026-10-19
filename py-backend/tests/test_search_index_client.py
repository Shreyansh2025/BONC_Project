import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from app.utils import search_index_client


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeAsyncClient:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return self.response


class SearchIndexClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_to_internal_contract_and_does_not_parse_response_body(self):
        calls = []
        fake_client = FakeAsyncClient(FakeResponse(200), calls)
        env = {
            "SEARCH_INTERNAL_BASE_URL": "http://127.0.0.1:8002/",
            "SEARCH_INTERNAL_API_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            search_index_client.httpx, "AsyncClient", return_value=fake_client
        ):
            result = await search_index_client.refresh_local_product_index("products_saved")

        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        url, payload, headers = calls[0]
        self.assertEqual(
            url,
            "http://127.0.0.1:8002/internal/v1/indexes/local-products/refresh",
        )
        self.assertEqual(payload["reason"], "products_saved")
        self.assertTrue(payload["event_id"])
        self.assertEqual(headers["Authorization"], "Bearer test-token")

    async def test_retries_once_after_server_error_with_same_event_id(self):
        calls = []
        clients = [
            FakeAsyncClient(FakeResponse(500), calls),
            FakeAsyncClient(FakeResponse(200), calls),
        ]
        env = {
            "SEARCH_INTERNAL_BASE_URL": "http://127.0.0.1:8002",
            "SEARCH_INTERNAL_API_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            search_index_client.httpx, "AsyncClient", side_effect=clients
        ), patch.object(search_index_client.asyncio, "sleep", new_callable=AsyncMock):
            result = await search_index_client.refresh_local_product_index("product_deleted")

        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["event_id"], calls[1][1]["event_id"])
        self.assertEqual(calls[0][1]["reason"], "product_deleted")

    async def test_missing_configuration_skips_request(self):
        env = {
            "SEARCH_INTERNAL_BASE_URL": "",
            "SEARCH_INTERNAL_API_TOKEN": "",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            search_index_client.httpx, "AsyncClient"
        ) as client_class:
            result = await search_index_client.refresh_local_product_index("products_saved")

        self.assertFalse(result)
        client_class.assert_not_called()

    async def test_authentication_error_is_not_retried(self):
        calls = []
        fake_client = FakeAsyncClient(FakeResponse(401), calls)
        env = {
            "SEARCH_INTERNAL_BASE_URL": "http://127.0.0.1:8002",
            "SEARCH_INTERNAL_API_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            search_index_client.httpx, "AsyncClient", return_value=fake_client
        ) as client_class:
            result = await search_index_client.refresh_local_product_index("products_saved")

        self.assertFalse(result)
        client_class.assert_called_once()


if __name__ == "__main__":
    unittest.main()
