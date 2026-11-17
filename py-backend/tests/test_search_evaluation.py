"""Unit tests for the standalone search evaluation metric helpers."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.search_ai.services.search_evaluation import evaluate_ranking
from scripts.evaluate_platform_search import load_cases, run_benchmark, summarize


class SearchEvaluationMetricTests(unittest.TestCase):
    def test_perfect_ranking(self):
        result = evaluate_ranking(["a", "b", "c"], ["a", "b"], k=3)
        self.assertAlmostEqual(result["precision_at_k"], 2 / 3)
        self.assertEqual(result["recall_at_k"], 1.0)
        self.assertEqual(result["mrr_at_k"], 1.0)
        self.assertEqual(result["ndcg_at_k"], 1.0)
        self.assertEqual(result["relevant_found_at_k"], 2)

    def test_first_relevant_result_after_one_irrelevant_result(self):
        result = evaluate_ranking(["not-relevant", "relevant", "also-relevant"], ["relevant", "also-relevant"], k=3)
        self.assertAlmostEqual(result["precision_at_k"], 2 / 3)
        self.assertEqual(result["recall_at_k"], 1.0)
        self.assertEqual(result["mrr_at_k"], 0.5)
        self.assertLess(result["ndcg_at_k"], 1.0)

    def test_results_are_deduplicated_before_metrics(self):
        result = evaluate_ranking(["a", "a", "b"], ["a", "b"], k=2)
        self.assertEqual(result["precision_at_k"], 1.0)
        self.assertEqual(result["recall_at_k"], 1.0)
        self.assertEqual(result["mrr_at_k"], 1.0)
        self.assertEqual(result["ndcg_at_k"], 1.0)
        self.assertEqual(result["returned_at_k"], 2)

    def test_no_relevant_results_in_top_k(self):
        result = evaluate_ranking(["x", "y"], ["a"], k=2)
        self.assertEqual(result["precision_at_k"], 0.0)
        self.assertEqual(result["recall_at_k"], 0.0)
        self.assertEqual(result["mrr_at_k"], 0.0)
        self.assertEqual(result["ndcg_at_k"], 0.0)

    def test_document_id_matching_is_exact_and_case_sensitive(self):
        result = evaluate_ranking(["ABC"], ["abc"], k=1)
        self.assertEqual(result["precision_at_k"], 0.0)
        self.assertEqual(result["recall_at_k"], 0.0)

    def test_rejects_invalid_k(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(k=value):
                with self.assertRaises(ValueError):
                    evaluate_ranking(["a"], ["a"], k=value)

    def test_requires_at_least_one_relevant_id(self):
        with self.assertRaisesRegex(ValueError, "relevant_ids"):
            evaluate_ranking(["a"], [], k=5)


class SearchEvaluationRunnerTests(unittest.TestCase):
    def test_load_cases_accepts_relevant_and_expected_empty_cases(self):
        payload = {
            "k": 5,
            "cases": [
                {
                    "id": "people-1",
                    "category": "people",
                    "query": "Python developers",
                    "relevant_ids": ["P-1"],
                },
                {
                    "id": "jobs-empty",
                    "category": "jobs",
                    "query": "role not in dataset",
                    "relevant_ids": [],
                    "expected_no_results": True,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            k, cases = load_cases(path)

        self.assertEqual(k, 5)
        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0]["relevant_ids"], ["P-1"])
        self.assertTrue(cases[1]["expected_no_results"])

    def test_load_cases_rejects_duplicate_case_ids(self):
        payload = {
            "cases": [
                {"id": "same", "category": "people", "query": "query", "relevant_ids": ["1"]},
                {"id": "same", "category": "jobs", "query": "query", "relevant_ids": ["2"]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate case id"):
                load_cases(path)

    def test_load_cases_rejects_unjudged_cases(self):
        payload = {
            "cases": [
                {"id": "people-1", "category": "people", "query": "query", "relevant_ids": []}
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relevant_ids"):
                load_cases(path)

    @patch("scripts.evaluate_platform_search.call_search")
    def test_benchmark_runs_cases_sequentially_and_reports_by_category(self, search_mock):
        search_mock.side_effect = [
            {
                "data": [{"id": "person-1"}],
                "total_count": 1,
                "index_version": 3,
                "config_version": 2,
                "query_plan": {"filters_applied": False},
                "corrected_query": "python developer",
            },
            {
                "data": [],
                "total_count": 0,
                "index_version": 4,
                "config_version": 1,
            },
        ]
        cases = [
            {
                "id": "people-1",
                "category": "people",
                "query": "Python developer",
                "relevant_ids": ["person-1"],
                "expected_no_results": False,
                "filters": [],
            },
            {
                "id": "jobs-empty",
                "category": "jobs",
                "query": "missing role",
                "relevant_ids": [],
                "expected_no_results": True,
                "filters": [],
            },
        ]

        report = run_benchmark(cases, base_url="http://localhost:8002", token="test-token", k=5)

        self.assertEqual(search_mock.call_count, 2)
        self.assertEqual(report["successful_case_count"], 2)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["by_category"]["people"]["mrr@5"], 1.0)
        self.assertEqual(report["by_category"]["jobs"]["expected_no_result_accuracy"], 1.0)
        self.assertEqual(report["cases"][0]["query_plan"], {"filters_applied": False})

    def test_summary_separates_unexpected_empty_results_from_expected_empty_cases(self):
        rows = [
            {
                "metrics": {
                    "precision_at_k": 0.2,
                    "recall_at_k": 0.5,
                    "mrr_at_k": 0.5,
                    "ndcg_at_k": 0.4,
                },
                "expected_no_results": False,
                "returned_count": 0,
            },
            {
                "metrics": None,
                "expected_no_results": True,
                "returned_count": 0,
            },
        ]
        summary = summarize(rows, k=5)
        self.assertEqual(summary["zero_result_rate"], 1.0)
        self.assertEqual(summary["unexpected_zero_result_rate"], 1.0)
        self.assertEqual(summary["expected_no_result_accuracy"], 1.0)




if __name__ == "__main__":
    unittest.main()
