"""Validation tests for generic search category configuration."""
import unittest
from collections import Counter
from unittest.mock import patch

from pydantic import ValidationError

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest
from app.search_ai.services.platform_search import _lexical_score, _query_understanding, _contains_phrase
from app.search_ai.services.query_understanding import interpret_query
from app.search_ai.services.platform_search import CategoryIndex, search_category


def people_config():
    return {
        "category_id": "people",
        "display_name": "People",
        "source": {
            "type": "sqlserver",
            "connection_env": "SEARCH_SOURCE_PEOPLE",
            "schema": "dbo",
            "table": "Users",
            "id_column": "UserId",
        },
        "fields": [
            {"key": "name", "source_column": "FullName", "label": "Name", "role": "title", "weight": 5},
            {"key": "skills", "source_column": "Skills", "label": "Skills", "role": "skill", "weight": 4, "aliases": ["expert", "expertise"]},
            {"key": "bio", "source_column": "Bio", "label": "Biography", "role": "description", "weight": 2},
            {"key": "location", "source_column": "City", "label": "Location", "role": "location", "weight": 1},
        ],
    }


class SearchPlatformModelTests(unittest.TestCase):
    def test_people_category_config_is_valid(self):
        config = CategoryConfig.model_validate(people_config())
        self.assertEqual(config.category_id, "people")
        self.assertEqual(len(config.fields), 4)

    def test_category_rejects_unsafe_sql_identifier(self):
        payload = people_config()
        payload["source"]["table"] = "Users; DROP TABLE Users"
        with self.assertRaises(ValidationError):
            CategoryConfig.model_validate(payload)

    def test_category_rejects_duplicate_field_keys(self):
        payload = people_config()
        payload["fields"][1]["key"] = "name"
        with self.assertRaises(ValidationError):
            CategoryConfig.model_validate(payload)

    def test_category_requires_title_field(self):
        payload = people_config()
        payload["fields"][0]["role"] = "general"
        with self.assertRaises(ValidationError):
            CategoryConfig.model_validate(payload)


    def test_query_understanding_extracts_values_by_configured_field(self):
        config = CategoryConfig.model_validate(people_config())
        documents = [{
            "document_id": "person-1",
            "title": "Shreyansh",
            "fields": {
                "name": "Shreyansh",
                "skills": "OCR, Python",
                "bio": "Document processing developer",
                "location": "Indore",
            },
            "metadata": {},
        }]
        understood = _query_understanding("Shreyansh OCR expert in Indore", config, documents)
        self.assertIn("Shreyansh", understood["matched_fields"]["name"])
        self.assertIn("OCR", understood["matched_fields"]["skills"])
        self.assertIn("expert", understood["matched_fields"]["skills"])
        self.assertIn("Indore", understood["matched_fields"]["location"])

    def test_field_weighting_combines_name_skill_and_location(self):
        config = CategoryConfig.model_validate(people_config())
        document = {
            "document_id": "person-1",
            "title": "Shreyansh",
            "fields": {
                "name": "Shreyansh",
                "skills": "OCR, Python",
                "bio": "Document processing developer",
                "location": "Indore",
            },
            "metadata": {},
        }
        score, matched_fields = _lexical_score(
            "Shreyansh OCR expert in Indore", config, document
        )
        self.assertAlmostEqual(score, 83.33, places=1)
        self.assertEqual(set(matched_fields), {"name", "skills", "location"})

    def test_query_understanding_uses_word_boundaries(self):
        self.assertFalse(_contains_phrase("carton box", "art"))
        self.assertTrue(_contains_phrase("find articles", "articles"))

    def test_platform_search_request_requires_category(self):
        with self.assertRaises(ValidationError):
            PlatformSearchRequest.model_validate({"query": "Shreyansh OCR expert"})



    def test_query_plan_removes_field_alias_but_keeps_exact_values(self):
        config = CategoryConfig.model_validate(people_config())
        interpreted = interpret_query(
            "Find Python expertise in Indore",
            config,
            field_values={
                "name": {"Shreyansh"},
                "skills": {"Python", "OCR"},
                "bio": {"Document processing developer"},
                "location": {"Indore"},
            },
        )
        self.assertEqual(interpreted["query_type"], "field_guided")
        self.assertEqual(interpreted["matched_aliases"]["skills"], ["expertise"])
        self.assertEqual(interpreted["matched_values"]["skills"], ["Python"])
        self.assertEqual(interpreted["matched_values"]["location"], ["Indore"])
        self.assertEqual(interpreted["lexical_query"], "find python in indore")
        self.assertEqual(interpreted["unmatched_tokens"], ["find"])
        self.assertEqual(
            interpreted["constraints"],
            [
                {
                    "field": "skills",
                    "role": "skill",
                    "values": ["Python"],
                    "match": "exact_index_value",
                    "applied_as": "ranking_hint_only",
                },
                {
                    "field": "location",
                    "role": "location",
                    "values": ["Indore"],
                    "match": "exact_index_value",
                    "applied_as": "ranking_hint_only",
                },
            ],
        )

    def test_query_plan_uses_free_text_when_no_alias_or_indexed_value_matches(self):
        config = CategoryConfig.model_validate(people_config())
        interpreted = interpret_query(
            "help me find a backend engineer",
            config,
            field_values={"name": set(), "skills": set(), "bio": set(), "location": set()},
        )
        self.assertEqual(interpreted["query_type"], "free_text")
        self.assertEqual(interpreted["matched_aliases"], {})
        self.assertEqual(interpreted["matched_values"], {})
        self.assertEqual(interpreted["lexical_query"], "help me find a backend engineer")
        self.assertFalse(interpreted["constraints"])

    def test_query_plan_supports_one_letter_skill_terms(self):
        config = CategoryConfig.model_validate(people_config())
        interpreted = interpret_query(
            "C developer",
            config,
            field_values={"skills": {"C"}, "name": set(), "bio": set(), "location": set()},
        )
        self.assertEqual(interpreted["matched_values"]["skills"], ["C"])
        self.assertEqual(interpreted["lexical_query"], "c developer")

    def test_query_understanding_does_not_treat_substrings_as_aliases(self):
        config = CategoryConfig.model_validate(people_config())
        interpreted = interpret_query(
            "expertly written python article",
            config,
            field_values={"skills": {"Python"}},
        )
        self.assertNotIn("skills", interpreted["matched_aliases"])
        self.assertEqual(interpreted["lexical_query"], "expertly written python article")


    def test_search_uses_query_plan_for_lexical_query_without_hard_filtering(self):
        config = CategoryConfig.model_validate(people_config())
        documents = [{
            "document_id": "person-1",
            "title": "Shreyansh",
            "fields": {
                "name": "Shreyansh",
                "skills": "Python, OCR",
                "bio": "Document processing developer",
                "location": "Indore",
            },
            "metadata": {},
        }]
        category = {
            "config": config,
            "status": "active",
            "config_version": 4,
            "index_version": 9,
        }
        index = CategoryIndex(
            version=9,
            documents=documents,
            vectors_index=None,
            vocabulary=Counter({"shreyansh": 1, "python": 1, "ocr": 1, "indore": 1}),
            inverted_index={
                "shreyansh": {0},
                "python": {0},
                "ocr": {0},
                "indore": {0},
            },
            field_values={
                "name": {"Shreyansh"},
                "skills": {"Python", "OCR"},
                "bio": {"Document processing developer"},
                "location": {"Indore"},
            },
        )
        with (
            patch("app.search_ai.services.platform_search.category_store.get_category", return_value=category),
            patch("app.search_ai.services.platform_search._get_category_index", return_value=index),
        ):
            result = search_category(
                query="Find Python expertise in Indore",
                category_id="people",
            )

        self.assertEqual(result["corrected_query"], "find python in indore")
        self.assertEqual(result["query_plan"]["query_type"], "field_guided")
        self.assertEqual(result["query_plan"]["matched_aliases"]["skills"], ["expertise"])
        self.assertEqual(result["query_plan"]["matched_values"]["skills"], ["Python"])
        self.assertEqual(result["query_plan"]["matched_values"]["location"], ["Indore"])
        self.assertFalse(result["query_plan"]["filters_applied"])
        self.assertEqual([item["id"] for item in result["data"]], ["person-1"])
        self.assertEqual(result["config_version"], 4)
        self.assertEqual(result["index_version"], 9)


    def test_query_plan_recognises_plural_field_alias_without_substring_match(self):
        config = CategoryConfig.model_validate(people_config())
        interpreted = interpret_query(
            "Python experts in Indore",
            config,
            field_values={"skills": {"Python"}, "location": {"Indore"}},
        )
        self.assertEqual(interpreted["matched_aliases"]["skills"], ["expert"])
        self.assertEqual(interpreted["lexical_query"], "python in indore")
        self.assertEqual(interpreted["matched_values"]["skills"], ["Python"])
        self.assertEqual(interpreted["matched_values"]["location"], ["Indore"])

if __name__ == "__main__":
    unittest.main()
