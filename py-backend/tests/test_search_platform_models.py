"""Validation tests for generic search category configuration."""
import unittest
from collections import Counter
from unittest.mock import MagicMock, patch

import numpy as np

from pydantic import ValidationError

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest, SQLSourceFilter
from app.search_ai.services.platform_search import _lexical_score, _query_understanding, _contains_phrase
from app.search_ai.services.query_understanding import interpret_query
from app.search_ai.services.platform_search import (
    CategoryIndex,
    _build_weighted_semantic_vectors,
    _get_category_index,
    _phrase_score,
    search_category,
)
from app.search_ai.services.source_sync import (
    build_source_mapping_suggestions,
    generate_join_view_preview,
    validate_sqlserver_source,
)


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

    def test_platform_search_request_rejects_whitespace_only_query(self):
        with self.assertRaises(ValidationError):
            PlatformSearchRequest.model_validate({"query": "   ", "category": "people"})



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

    def test_real_category_index_keeps_single_character_skill_values(self):
        payload = people_config()
        payload["rules"] = {
            "lexical_enabled": True,
            "semantic_enabled": False,
            "semantic_min_score": 20,
            "semantic_candidate_limit": 50,
            "lexical_weight": 0.65,
            "semantic_weight": 0.35,
        }
        config = CategoryConfig.model_validate(payload)
        category = {
            "config": config,
            "index_version": 1,
        }
        documents = [{
            "document_id": "person-1",
            "title": "Person A",
            "fields": {
                "name": "A",
                "skills": "C, C++, C#",
                "bio": "Backend developer",
                "location": "Indore",
            },
            "metadata": {},
        }]
        with (
            patch("app.search_ai.services.platform_search._INDEXES", {}),
            patch("app.search_ai.services.platform_search.category_store.list_documents", return_value=documents),
        ):
            index = _get_category_index(category)

        self.assertEqual(index.field_values["skills"], {"C", "C++", "C#"})
        self.assertNotIn("A", index.field_values["name"])

    def test_semantic_vectors_respect_configured_field_weights(self):
        config = CategoryConfig.model_validate(people_config())
        document = {
            "document_id": "person-1",
            "fields": {
                "name": "A Person",
                "skills": "Python",
                "bio": None,
                "location": None,
            },
        }

        class FakeEmbeddingModel:
            def encode(self, texts, convert_to_numpy=True, batch_size=64):
                vectors = np.zeros((len(texts), 384), dtype="float32")
                for index, value in enumerate(texts):
                    if value.startswith("Name:"):
                        vectors[index, 0] = 1.0
                    elif value.startswith("Skills:"):
                        vectors[index, 1] = 1.0
                return vectors

        with patch(
            "app.search_ai.services.platform_search.get_model",
            return_value=FakeEmbeddingModel(),
        ):
            vectors = _build_weighted_semantic_vectors(config, [document])

        self.assertAlmostEqual(float(vectors[0, 0] / vectors[0, 1]), 5.0 / 4.0, places=5)
        self.assertAlmostEqual(float(np.linalg.norm(vectors[0])), 1.0, places=5)

    def test_exact_document_id_returns_result_without_content_field_match(self):
        payload = people_config()
        payload["rules"] = {
            "lexical_enabled": True,
            "semantic_enabled": False,
            "semantic_min_score": 20,
            "semantic_candidate_limit": 50,
            "lexical_weight": 0.65,
            "semantic_weight": 0.35,
        }
        config = CategoryConfig.model_validate(payload)
        document = {
            "document_id": "person-007",
            "title": "Unrelated title",
            "fields": {
                "name": "Different Person",
                "skills": "Python",
                "bio": "Backend developer",
                "location": "Indore",
            },
            "metadata": {},
        }
        category = {
            "config": config,
            "status": "active",
            "config_version": 1,
            "index_version": 1,
        }
        index = CategoryIndex(
            version=1,
            documents=[document],
            vectors_index=None,
            vocabulary=Counter({"different": 1, "person": 1, "python": 1}),
            inverted_index={"different": {0}, "person": {0}, "python": {0}},
            field_values={"name": {"Different Person"}, "skills": {"Python"}},
            document_id_lookup={"person-007": {0}},
        )
        with (
            patch("app.search_ai.services.platform_search.category_store.get_category", return_value=category),
            patch("app.search_ai.services.platform_search._get_category_index", return_value=index),
        ):
            result = search_category(query="person-007", category_id="people")

        self.assertEqual([item["id"] for item in result["data"]], ["person-007"])
        self.assertEqual(result["data"][0]["score"], 100.0)
        self.assertTrue(result["data"][0]["exact_identifier_match"])
        self.assertEqual(result["data"][0]["matched_fields"], ["document_id"])

    def test_synonym_group_expands_query_without_replacing_original_terms(self):
        payload = people_config()
        payload["synonyms"] = [{
            "terms": ["developer", "engineer", "programmer"],
        }]
        config = CategoryConfig.model_validate(payload)

        understood = interpret_query(
            "backend developer",
            config,
            field_values={"name": set(), "skills": set(), "bio": set(), "location": set()},
        )

        self.assertEqual(
            understood["synonym_expansions"],
            [{
                "matched_term": "developer",
                "alternatives": ["engineer", "programmer"],
            }],
        )
        self.assertEqual(
            understood["expanded_lexical_query"],
            "backend developer engineer programmer",
        )

    def test_exact_indexed_value_is_not_synonym_expanded(self):
        payload = people_config()
        payload["synonyms"] = [{"terms": ["Python", "Py"]}]
        config = CategoryConfig.model_validate(payload)

        understood = interpret_query(
            "Python developer",
            config,
            field_values={"skills": {"Python"}, "name": set(), "bio": set(), "location": set()},
        )

        self.assertEqual(understood["synonym_expansions"], [])
        self.assertEqual(understood["expanded_lexical_query"], "python developer")

    def test_category_rejects_overlapping_synonym_groups(self):
        payload = people_config()
        payload["synonyms"] = [
            {"terms": ["developer", "engineer"]},
            {"terms": ["engineer", "programmer"]},
        ]
        with self.assertRaisesRegex(ValidationError, "only one group"):
            CategoryConfig.model_validate(payload)

    def test_query_understanding_distinguishes_c_cplusplus_and_csharp(self):
        config = CategoryConfig.model_validate(people_config())
        values = {"skills": {"C", "C++", "C#"}, "name": set(), "bio": set(), "location": set()}

        c_query = interpret_query("C developer", config, field_values=values)
        cpp_query = interpret_query("C++ developer", config, field_values=values)
        csharp_query = interpret_query("C# developer", config, field_values=values)

        self.assertEqual(c_query["matched_values"]["skills"], ["C"])
        self.assertEqual(cpp_query["matched_values"]["skills"], ["C++"])
        self.assertEqual(csharp_query["matched_values"]["skills"], ["C#"])

    def test_synonym_alternative_contributes_once_and_is_discounted(self):
        config = CategoryConfig.model_validate(people_config())
        alternative_document = {
            "document_id": "person-1",
            "fields": {"name": "", "skills": "", "bio": "Engineer", "location": ""},
        }
        direct_document = {
            "document_id": "person-2",
            "fields": {"name": "", "skills": "", "bio": "Developer", "location": ""},
        }
        expansion = [{
            "matched_term": "developer",
            "alternatives": ["engineer", "programmer"],
        }]

        alternative_score, _ = _lexical_score(
            "developer", config, alternative_document, synonym_expansions=expansion
        )
        direct_score, _ = _lexical_score(
            "developer", config, direct_document, synonym_expansions=expansion
        )

        self.assertAlmostEqual(alternative_score, direct_score * 0.85, places=5)

    def test_phrase_score_rewards_exact_multiword_phrase(self):
        config = CategoryConfig.model_validate(people_config())
        document = {
            "document_id": "person-1",
            "fields": {
                "name": "",
                "skills": "",
                "bio": "Experienced machine learning developer",
                "location": "",
            },
        }
        self.assertGreater(_phrase_score("machine learning", config, document), 0)

    def test_source_preflight_detects_null_and_duplicate_document_ids(self):
        catalog = {
            "profile_id": "PEOPLE",
            "database": "BONC_Dev",
            "schemas": ["dbo"],
            "tables": [{
                "schema": "dbo",
                "name": "People",
                "type": "BASE TABLE",
                "primary_key_columns": ["PersonId"],
                "columns": [
                    {"name": "PersonId", "data_type": "int", "nullable": True},
                    {"name": "IsDeleted", "data_type": "bit", "nullable": True},
                ],
            }],
            "relationships": [],
        }
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.mappings.return_value.one.return_value = {
            "total_rows": 10,
            "non_null_ids": 8,
            "blank_ids": 0,
            "distinct_ids": 7,
        }
        engine = unittest.mock.MagicMock()
        engine.connect.return_value = connection

        with (
            patch(
                "app.search_ai.services.source_sync.discover_source_schema",
                return_value=catalog,
            ),
            patch(
                "app.search_ai.services.source_sync._engine_from_environment_name",
                return_value=engine,
            ),
        ):
            result = validate_sqlserver_source(
                "PEOPLE",
                schema="dbo",
                table="People",
                id_column="PersonId",
                filters=[SQLSourceFilter(column="IsDeleted", operator="is_null")],
            )

        self.assertFalse(result["is_valid"])
        self.assertEqual(result["null_id_count"], 2)
        self.assertEqual(result["duplicate_id_count"], 1)
        self.assertTrue(any("NULL" in warning for warning in result["warnings"]))
        self.assertTrue(any("duplicate" in warning for warning in result["warnings"]))
        query = connection.execute.call_args.args[0]
        self.assertIn("COUNT_BIG(DISTINCT [PersonId])", query.text)
        self.assertIn("[IsDeleted] IS NULL", query.text)

    def test_source_mapping_suggestions_choose_primary_key_and_skip_sensitive_columns(self):
        catalog = {
            "profile_id": "PEOPLE",
            "database": "BONC_Dev",
            "tables": [{
                "schema": "dbo",
                "name": "BoncUser",
                "type": "BASE TABLE",
                "primary_key_columns": ["UserId"],
                "columns": [
                    {"name": "UserId", "data_type": "int", "nullable": False},
                    {"name": "FullName", "data_type": "nvarchar", "nullable": True},
                    {"name": "Skills", "data_type": "nvarchar", "nullable": True},
                    {"name": "EmailId", "data_type": "nvarchar", "nullable": True},
                    {"name": "PasswordHash", "data_type": "nvarchar", "nullable": True},
                    {"name": "IsDeleted", "data_type": "bit", "nullable": True},
                ],
            }],
            "relationships": [],
        }

        result = build_source_mapping_suggestions(catalog, schema="dbo", table="BoncUser")
        mapped_columns = {field["source_column"] for field in result["fields"]}

        self.assertEqual(result["id_column_suggestion"]["column"], "UserId")
        self.assertIn("FullName", mapped_columns)
        self.assertIn("Skills", mapped_columns)
        self.assertNotIn("EmailId", mapped_columns)
        self.assertNotIn("PasswordHash", mapped_columns)
        self.assertIn("IsDeleted", result["potential_filter_columns"])

    def test_mapping_skips_primary_foreign_keys_and_sensitive_personal_fields(self):
        catalog = {
            "profile_id": "PEOPLE",
            "database": "BONC_Dev",
            "tables": [{
                "schema": "dbo",
                "name": "People",
                "type": "BASE TABLE",
                "primary_key_columns": ["PersonGuid"],
                "columns": [
                    {"name": "PersonGuid", "data_type": "uniqueidentifier", "nullable": False, "is_primary_key": True},
                    {"name": "FullName", "data_type": "nvarchar", "nullable": True},
                    {"name": "CompanyId", "data_type": "int", "nullable": True, "is_foreign_key": True},
                    {"name": "PANNumber", "data_type": "nvarchar", "nullable": True},
                    {"name": "SSN", "data_type": "nvarchar", "nullable": True},
                    {"name": "Gender", "data_type": "nvarchar", "nullable": True},
                ],
            }],
            "relationships": [],
        }

        result = build_source_mapping_suggestions(catalog, schema="dbo", table="People")
        mapped_columns = {field["source_column"] for field in result["fields"]}

        self.assertEqual(result["id_column_suggestion"]["column"], "PersonGuid")
        self.assertEqual(mapped_columns, {"FullName"})
        skipped_columns = {item["column"] for item in result["skipped_columns"]}
        self.assertTrue({"PersonGuid", "CompanyId", "PANNumber", "SSN", "Gender"}.issubset(skipped_columns))

    def test_join_view_preview_generates_many_to_one_left_join_only(self):
        catalog = {
            "profile_id": "CONTENT",
            "database": "BONC_Dev",
            "schemas": ["dbo"],
            "tables": [
                {
                    "schema": "dbo",
                    "name": "Articles",
                    "type": "BASE TABLE",
                    "primary_key_columns": ["Id"],
                    "columns": [
                        {"name": "Id", "data_type": "int", "nullable": False},
                        {"name": "CompanyId", "data_type": "int", "nullable": True},
                        {"name": "FirstName", "data_type": "nvarchar", "nullable": True},
                        {"name": "LastName", "data_type": "nvarchar", "nullable": True},
                        {"name": "Title", "data_type": "nvarchar", "nullable": True},
                    ],
                },
                {
                    "schema": "dbo",
                    "name": "Company",
                    "type": "BASE TABLE",
                    "primary_key_columns": ["CompanyId"],
                    "columns": [
                        {"name": "CompanyId", "data_type": "int", "nullable": False},
                        {"name": "CompanyName", "data_type": "nvarchar", "nullable": True},
                    ],
                },
            ],
            "relationships": [{
                "constraint_name": "FK_Articles_Company",
                "from_schema": "dbo",
                "from_table": "Articles",
                "to_schema": "dbo",
                "to_table": "Company",
                "columns": [{
                    "from_column": "CompanyId",
                    "to_column": "CompanyId",
                    "ordinal_position": 1,
                }],
            }],
        }
        with patch(
            "app.search_ai.services.source_sync.discover_source_schema",
            return_value=catalog,
        ):
            result = generate_join_view_preview(
                "CONTENT",
                base_schema="dbo",
                base_table="Articles",
                base_id_column="Id",
                view_schema="dbo",
                view_name="vw_Search_Articles",
                selected_relationships=[catalog["relationships"][0]],
            )

        self.assertIn("CREATE OR ALTER VIEW [dbo].[vw_Search_Articles]", result["view_sql"])
        self.assertIn("LEFT JOIN [dbo].[Company]", result["view_sql"])
        self.assertIn("AS [DocumentId]", result["view_sql"])
        self.assertIn("CONCAT(", result["view_sql"])
        self.assertIn("AS [dbo_Articles__DisplayName]", result["view_sql"])
        self.assertFalse(result["execution_performed"])

    def test_join_view_preview_rejects_one_to_many_document_multiplication(self):
        catalog = {
            "profile_id": "CONTENT",
            "database": "BONC_Dev",
            "schemas": ["dbo"],
            "tables": [{
                "schema": "dbo",
                "name": "Company",
                "type": "BASE TABLE",
                "primary_key_columns": ["CompanyId"],
                "columns": [{"name": "CompanyId", "data_type": "int", "nullable": False}],
            }, {
                "schema": "dbo",
                "name": "Articles",
                "type": "BASE TABLE",
                "primary_key_columns": ["Id"],
                "columns": [{"name": "Id", "data_type": "int", "nullable": False}],
            }],
            "relationships": [{
                "constraint_name": "FK_Articles_Company",
                "from_schema": "dbo",
                "from_table": "Articles",
                "to_schema": "dbo",
                "to_table": "Company",
                "columns": [{
                    "from_column": "CompanyId",
                    "to_column": "CompanyId",
                    "ordinal_position": 1,
                }],
            }],
        }
        with patch(
            "app.search_ai.services.source_sync.discover_source_schema",
            return_value=catalog,
        ):
            with self.assertRaisesRegex(ValueError, "could duplicate search documents"):
                generate_join_view_preview(
                    "CONTENT",
                    base_schema="dbo",
                    base_table="Company",
                    base_id_column="CompanyId",
                    view_schema="dbo",
                    view_name="vw_Search_Company",
                    selected_relationships=[catalog["relationships"][0]],
                )

if __name__ == "__main__":
    unittest.main()
