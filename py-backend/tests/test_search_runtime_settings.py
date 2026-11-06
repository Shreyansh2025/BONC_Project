"""Tests proving persisted settings reach the real Search AI algorithms."""
from __future__ import annotations

import sys
import types
import unittest
from collections import Counter
from unittest.mock import patch

import numpy as np

# Some lightweight test environments do not install FAISS. The runtime tests
# replace the search index and normalization call, so only the import surface
# is required in that case.
try:
    import faiss  # noqa: F401
except ModuleNotFoundError:
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.Index = object
    fake_faiss.IndexFlatL2 = object
    fake_faiss.normalize_L2 = lambda vectors: None
    sys.modules["faiss"] = fake_faiss

from app.search_ai.routes import search as search_route
from app.search_ai.settings import SearchRulesSettings
from app.search_ai.services import b2b_product_search, b2b_search, product_search


class _RecordingIndex:
    def __init__(self, percentage: float = 85.0):
        self.percentage = percentage
        self.requested_k: int | None = None
        self.search_calls = 0

    def search(self, query_vector, k: int):
        self.requested_k = k
        self.search_calls += 1
        distance = 2.0 * (1.0 - self.percentage / 100.0)
        return (
            np.array([[distance]], dtype=np.float32),
            np.array([[0]], dtype=np.int64),
        )


class _FakeModel:
    def encode(self, texts):
        return np.array([[1.0, 0.0]], dtype=np.float32)


class SearchRuntimeSettingsServiceTests(unittest.TestCase):
    def _run_company(self, settings, *, percentage=85.0, exact_name=None, description="unrelated material"):
        doc = {
            "businessId": "company-1",
            "businessName": exact_name or "Acme Manufacturing",
            "businessSlug": "acme-manufacturing",
            "industryName": "Industrial Services",
            "categoryName": "Machine Fabrication",
            "subCategoryName": "",
            "businessTypeName": "Manufacturer",
            "city": "",
            "state": "",
            "country": "",
            "businessDescription": description,
            "tagline": "",
            "aboutBrief": "",
            "description": description,
            "vision": "",
            "whyChooseUs": "",
        }
        index = _RecordingIndex(percentage)
        state = b2b_search._CompanyState(
            companies=[doc], index=index, vocabulary=Counter(), inverted_index={}
        )
        with patch.object(b2b_search, "_state", state), \
             patch.object(b2b_search, "get_model", return_value=_FakeModel()), \
             patch.object(b2b_search.faiss, "normalize_L2", lambda vectors: None, create=True):
            results = b2b_search.search_companies_sync(
                "Acme" if exact_name else "quantum widgets", settings
            )
        return results, index

    def _run_b2b_product(self, settings, *, percentage=85.0, exact_name=None, description="unrelated material"):
        doc = {
            "productsAndServicesId": "product-1",
            "productName": exact_name or "Acme Device",
            "brandName": "Acme",
            "categoryName": "Machine Parts",
            "industryName": "Manufacturing",
            "city": "",
            "country": "",
            "businessTypeName": "Manufacturer",
            "description": description,
            "keyWords": "",
            "state": "",
        }
        index = _RecordingIndex(percentage)
        state = b2b_product_search._ProductState(
            products=[doc], index=index, vocabulary=Counter(), inverted_index={}
        )
        with patch.object(b2b_product_search, "_state", state), \
             patch.object(b2b_product_search, "get_model", return_value=_FakeModel()), \
             patch.object(b2b_product_search.faiss, "normalize_L2", lambda vectors: None, create=True):
            results = b2b_product_search.search_products_sync(
                "Acme" if exact_name else "quantum widgets", settings
            )
        return results, index

    def _run_local_product(self, settings, *, percentage=85.0, exact_name=None):
        doc = {
            "id": 1,
            "productName": exact_name or "Acme Device",
            "category": "Machine Parts",
            "model": "Model X",
            "description": "unrelated material",
            "features": [],
            "specifications": {},
            "sourceFileName": "catalogue.pdf",
        }
        index = _RecordingIndex(percentage)
        state = product_search._ProductState(
            products=[doc], index=index, vocabulary=Counter(), inverted_index={}
        )
        with patch.object(product_search, "_state", state), \
             patch.object(product_search, "get_model", return_value=_FakeModel()), \
             patch.object(product_search.faiss, "normalize_L2", lambda vectors: None, create=True):
            results = product_search.search_products_sync(
                "Acme" if exact_name else "quantum widgets", settings
            )
        return results, index

    def test_semantic_candidate_limit_is_used_by_all_three_indexes(self):
        settings = SearchRulesSettings(semantic_candidate_limit=77)

        for runner in (self._run_company, self._run_b2b_product, self._run_local_product):
            with self.subTest(search=runner.__name__):
                results, index = runner(settings, percentage=85.0)
                self.assertEqual(index.requested_k, 77)
                self.assertEqual(len(results), 1)

    def test_lexical_result_threshold_controls_semantic_fallback(self):
        skip_ai = SearchRulesSettings(lexical_result_threshold=1)
        run_ai = SearchRulesSettings(lexical_result_threshold=2)

        for runner in (self._run_company, self._run_b2b_product, self._run_local_product):
            with self.subTest(search=runner.__name__, threshold=1):
                results, index = runner(skip_ai, exact_name="Acme")
                self.assertEqual(len(results), 1)
                self.assertEqual(index.search_calls, 0)

            with self.subTest(search=runner.__name__, threshold=2):
                results, index = runner(run_ai, exact_name="Acme")
                self.assertEqual(len(results), 1)
                self.assertEqual(index.search_calls, 1)

    def test_each_semantic_minimum_score_is_runtime_configurable(self):
        cases = (
            (self._run_company, SearchRulesSettings(company={
                "semantic_min_score": 50,
                "semantic_high_confidence_score": 60,
            })),
            (self._run_b2b_product, SearchRulesSettings(b2b_product={
                "semantic_min_score": 50,
                "semantic_high_confidence_score": 65,
            })),
            (self._run_local_product, SearchRulesSettings(local_product={
                "semantic_min_score": 50,
            })),
        )
        for runner, settings in cases:
            with self.subTest(search=runner.__name__):
                results, index = runner(settings, percentage=45.0)
                self.assertEqual(index.search_calls, 1)
                self.assertEqual(results, [])

    def test_company_and_b2b_high_confidence_thresholds_are_runtime_configurable(self):
        settings = SearchRulesSettings(
            company={
                "semantic_min_score": 35,
                "semantic_high_confidence_score": 40,
            },
            b2b_product={
                "semantic_min_score": 40,
                "semantic_high_confidence_score": 45,
            },
        )

        # The 45% semantic result has no literal query word in its document.
        # It is accepted only because it meets the configured high-confidence
        # cutoff, not because of a lexical/shared-word match.
        company_results, _ = self._run_company(settings, percentage=45.0)
        product_results, _ = self._run_b2b_product(settings, percentage=45.0)
        self.assertEqual(len(company_results), 1)
        self.assertEqual(len(product_results), 1)


class SearchRouteSettingsSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_database_snapshot_is_passed_to_all_search_sources(self):
        settings = SearchRulesSettings(semantic_candidate_limit=77)
        with patch.object(
            search_route.settings_store,
            "get_current_settings",
            return_value={"settings": settings},
        ) as load_settings, \
             patch.object(search_route.b2b_search, "search_companies_sync", return_value=[]) as companies, \
             patch.object(search_route.b2b_product_search, "search_products_sync", return_value=[]) as b2b_products, \
             patch.object(search_route, "search_local_products_sync", return_value=[]) as local_products:
            response = await search_route.search_unified(
                search_route.SearchRequest(SearchText="quantum widgets", Type="all")
            )

        self.assertEqual(response["Success"], True)
        load_settings.assert_called_once_with()
        companies.assert_called_once_with("quantum widgets", settings)
        b2b_products.assert_called_once_with("quantum widgets", settings)
        local_products.assert_called_once_with("quantum widgets", settings)

    async def test_search_fails_closed_when_persisted_settings_cannot_be_loaded(self):
        with patch.object(
            search_route.settings_store,
            "get_current_settings",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = await search_route.search_unified(
                search_route.SearchRequest(SearchText="quantum widgets", Type="all")
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.body,
            b'{"Success":false,"Message":"Search configuration unavailable",'
            b'"Type":"error","Data":[],"TotalCount":0}',
        )


if __name__ == "__main__":
    unittest.main()
