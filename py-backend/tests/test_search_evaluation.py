"""Unit tests for the standalone search evaluation metric helpers."""
import unittest

from app.search_ai.services.search_evaluation import evaluate_ranking


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


if __name__ == "__main__":
    unittest.main()
