"""Validation tests for generic search category configuration."""
import unittest

from pydantic import ValidationError

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest


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
            {"key": "skills", "source_column": "Skills", "label": "Skills", "role": "skill", "weight": 4},
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

    def test_platform_search_request_requires_category(self):
        with self.assertRaises(ValidationError):
            PlatformSearchRequest.model_validate({"query": "Shreyansh OCR expert"})


if __name__ == "__main__":
    unittest.main()
