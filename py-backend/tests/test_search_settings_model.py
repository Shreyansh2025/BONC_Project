"""Validation tests for the planned Search AI settings schema."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.search_ai.settings import SearchRulesSettings


class SearchRulesSettingsTests(unittest.TestCase):
    def test_defaults_match_current_search_service_constants(self):
        settings = SearchRulesSettings()
        self.assertEqual(settings.semantic_candidate_limit, 50)
        self.assertEqual(settings.lexical_result_threshold, 20)
        self.assertEqual(settings.company.semantic_min_score, 35.0)
        self.assertEqual(settings.company.semantic_high_confidence_score, 60.0)
        self.assertEqual(settings.b2b_product.semantic_min_score, 40.0)
        self.assertEqual(settings.b2b_product.semantic_high_confidence_score, 65.0)
        self.assertEqual(settings.local_product.semantic_min_score, 10.0)

    def test_rejects_candidate_and_trigger_values_outside_safe_bounds(self):
        with self.assertRaises(ValidationError):
            SearchRulesSettings(semantic_candidate_limit=9)
        with self.assertRaises(ValidationError):
            SearchRulesSettings(semantic_candidate_limit=101)
        with self.assertRaises(ValidationError):
            SearchRulesSettings(lexical_result_threshold=0)

    def test_rejects_scores_outside_zero_to_one_hundred(self):
        with self.assertRaises(ValidationError):
            SearchRulesSettings(company={"semantic_min_score": -1})
        with self.assertRaises(ValidationError):
            SearchRulesSettings(local_product={"semantic_min_score": 101})

    def test_high_confidence_threshold_must_exceed_minimum(self):
        with self.assertRaises(ValidationError):
            SearchRulesSettings(company={
                "semantic_min_score": 60,
                "semantic_high_confidence_score": 60,
            })
        with self.assertRaises(ValidationError):
            SearchRulesSettings(b2b_product={
                "semantic_min_score": 70,
                "semantic_high_confidence_score": 65,
            })

    def test_rejects_unknown_settings(self):
        with self.assertRaises(ValidationError):
            SearchRulesSettings(not_a_real_setting=5)

    def test_settings_are_immutable(self):
        settings = SearchRulesSettings()
        with self.assertRaises(ValidationError):
            settings.semantic_candidate_limit = 75


if __name__ == "__main__":
    unittest.main()
