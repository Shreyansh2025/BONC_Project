from pydantic import ValidationError
import pytest

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest


def _people_config():
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


def test_people_category_config_is_valid():
    config = CategoryConfig.model_validate(_people_config())
    assert config.category_id == "people"
    assert len(config.fields) == 4


def test_category_rejects_unsafe_sql_identifier():
    payload = _people_config()
    payload["source"]["table"] = "Users; DROP TABLE Users"
    with pytest.raises(ValidationError):
        CategoryConfig.model_validate(payload)


def test_category_rejects_duplicate_field_keys():
    payload = _people_config()
    payload["fields"][1]["key"] = "name"
    with pytest.raises(ValidationError):
        CategoryConfig.model_validate(payload)


def test_category_requires_title_field():
    payload = _people_config()
    payload["fields"][0]["role"] = "general"
    with pytest.raises(ValidationError):
        CategoryConfig.model_validate(payload)


def test_platform_search_request_requires_category():
    with pytest.raises(ValidationError):
        PlatformSearchRequest.model_validate({"query": "Shreyansh OCR expert"})
