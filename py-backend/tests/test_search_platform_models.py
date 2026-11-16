"""Validation tests for generic search category configuration."""
import unittest

from pydantic import ValidationError

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest
from app.search_ai.services.platform_search import _lexical_score, _query_understanding, _contains_phrase


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


if __name__ == "__main__":
    unittest.main()
