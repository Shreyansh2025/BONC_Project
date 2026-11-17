"""Regression tests for typed result filters in generic category search."""
from __future__ import annotations

import os
import unittest
from collections import Counter
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.search_ai.platform.models import CategoryConfig, PlatformSearchRequest
from app.search_ai.routes.platform import admin_router, router
from app.search_ai.services import category_store
from app.search_ai.services.platform_common import platform_tokens
from app.search_ai.services.platform_search import (
    CategoryIndex,
    _apply_result_filters,
    _document_matches_filter,
    _preferred_constraint_bonus,
    _semantic_candidate_is_acceptable,
    _validate_query_filters,
    search_category,
)
from app.search_ai.services.query_understanding import interpret_query
from app.search_ai.services.source_sync import build_source_mapping_suggestions


def config_payload():
    return {
        "category_id": "jobs",
        "display_name": "Jobs",
        "source": {"type": "push"},
        "fields": [
            {
                "key": "title",
                "source_column": "Title",
                "label": "Job title",
                "role": "title",
                "weight": 5,
                "searchable": True,
                "semantic": False,
            },
            {
                "key": "skills",
                "source_column": "KeySkills",
                "label": "Skills",
                "role": "skill",
                "weight": 4,
                "searchable": True,
                "semantic": False,
                "aliases": ["skill", "skills", "expertise"],
                "filterable": True,
                "data_type": "keyword",
                "query_filter_mode": "required",
                "query_match": "all",
            },
            {
                "key": "city",
                "source_column": "City",
                "label": "City",
                "role": "location",
                "weight": 3,
                "searchable": True,
                "semantic": False,
                "aliases": ["city", "location"],
                "filterable": True,
                "data_type": "keyword",
                "query_filter_mode": "required",
                "query_match": "all",
            },
            {
                "key": "experience",
                "source_column": "ExperienceMin",
                "label": "Minimum experience",
                "role": "general",
                "weight": 0,
                "searchable": False,
                "semantic": False,
                "filterable": True,
                "data_type": "integer",
                "query_filter_mode": "none",
            },
            {
                "key": "remote",
                "source_column": "IsRemote",
                "label": "Remote",
                "role": "category",
                "weight": 0,
                "searchable": False,
                "semantic": False,
                "filterable": True,
                "data_type": "boolean",
                "query_filter_mode": "preferred",
            },
            {
                "key": "deadline",
                "source_column": "Deadline",
                "label": "Deadline",
                "role": "general",
                "weight": 0,
                "searchable": False,
                "semantic": False,
                "filterable": True,
                "data_type": "date",
                "query_filter_mode": "none",
            },
        ],
        "rules": {
            "lexical_enabled": True,
            "semantic_enabled": False,
            "semantic_min_score": 20,
            "semantic_candidate_limit": 50,
            "lexical_weight": 1,
            "semantic_weight": 0,
        },
        "public_search_enabled": False,
    }


def sample_documents():
    return [
        {
            "document_id": "job-good",
            "title": "Backend Developer",
            "fields": {
                "title": "Backend Developer",
                "skills": "Python, SQL",
                "city": "Noida",
                "experience": 3,
                "remote": True,
                "deadline": "2026-12-31T00:00:00",
            },
            "metadata": {},
        },
        {
            "document_id": "job-missing-sql",
            "title": "Backend Developer",
            "fields": {
                "title": "Backend Developer",
                "skills": "Python",
                "city": "Noida",
                "experience": 2,
                "remote": False,
                "deadline": "2026-12-15T00:00:00",
            },
            "metadata": {},
        },
        {
            "document_id": "job-wrong-city",
            "title": "Backend Developer",
            "fields": {
                "title": "Backend Developer",
                "skills": "Python, SQL",
                "city": "Indore",
                "experience": 5,
                "remote": True,
                "deadline": "2026-12-20T00:00:00",
            },
            "metadata": {},
        },
    ]


def category_index(config, documents):
    def searchable_text(document):
        return " ".join(
            f'{field.label}: {document.get("fields", {}).get(field.key, "")}'
            for field in config.fields if field.searchable
        )

    vocabulary = Counter()
    inverted = {}
    values = {}
    for position, document in enumerate(documents):
        tokens = platform_tokens(searchable_text(document))
        vocabulary.update(tokens)
        for token in set(tokens):
            inverted.setdefault(token, set()).add(position)

    for field in config.fields:
        if not field.searchable and not field.filterable:
            continue
        field_values = set()
        for document in documents:
            raw = document.get("fields", {}).get(field.key)
            if raw is None:
                continue
            field_values.update(
                item.strip() for item in str(raw).replace("|", ",").replace(";", ",").split(",")
                if item.strip()
            )
        values[field.key] = field_values

    return CategoryIndex(
        version=1,
        documents=documents,
        vectors_index=None,
        vocabulary=vocabulary,
        inverted_index=inverted,
        field_values=values,
    )


class PlatformSourceMappingFilterSuggestionTests(unittest.TestCase):
    def test_mapping_suggests_filter_types_for_skill_location_and_typed_columns(self):
        catalog = {
            "profile_id": "JOBS",
            "database": "BONC_Dev",
            "tables": [{
                "schema": "dbo",
                "name": "Jobs",
                "type": "BASE TABLE",
                "primary_key_columns": ["JobId"],
                "columns": [
                    {"name": "JobId", "data_type": "int", "nullable": False, "is_primary_key": True},
                    {"name": "Title", "data_type": "nvarchar", "nullable": False},
                    {"name": "KeySkills", "data_type": "nvarchar", "nullable": True},
                    {"name": "City", "data_type": "nvarchar", "nullable": True},
                    {"name": "ExperienceMin", "data_type": "bigint", "nullable": True},
                    {"name": "IsRemote", "data_type": "bit", "nullable": True},
                    {"name": "ApplicationDeadline", "data_type": "datetime2", "nullable": True},
                ],
            }],
            "relationships": [],
        }
        result = build_source_mapping_suggestions(catalog, schema="dbo", table="Jobs")
        fields = {item["source_column"]: item for item in result["fields"]}

        self.assertEqual(fields["KeySkills"]["role"], "skill")
        self.assertTrue(fields["KeySkills"]["filterable"])
        self.assertEqual(fields["KeySkills"]["data_type"], "keyword")
        self.assertEqual(fields["KeySkills"]["query_filter_mode"], "preferred")
        self.assertTrue(fields["City"]["filterable"])
        self.assertEqual(fields["City"]["data_type"], "keyword")
        self.assertEqual(fields["ExperienceMin"]["data_type"], "integer")
        self.assertTrue(fields["ExperienceMin"]["filterable"])
        self.assertEqual(fields["IsRemote"]["data_type"], "boolean")
        self.assertFalse(fields["IsRemote"]["searchable"])
        self.assertEqual(fields["ApplicationDeadline"]["data_type"], "date")
        self.assertFalse(fields["ApplicationDeadline"]["searchable"])


class PlatformResultFilterTests(unittest.TestCase):
    def setUp(self):
        self.config = CategoryConfig.model_validate(config_payload())
        self.documents = sample_documents()
        self.index = category_index(self.config, self.documents)
        self.category = {
            "config": self.config,
            "status": "active",
            "config_version": 1,
            "index_version": 1,
        }

    def test_old_category_payloads_keep_backward_compatible_defaults(self):
        payload = config_payload()
        for field in payload["fields"]:
            field.pop("filterable", None)
            field.pop("data_type", None)
            field.pop("query_filter_mode", None)
            field.pop("query_match", None)
        # Old persisted configurations may contain inconsistent legacy
        # searchable/semantic flags. New defaults must not invalidate them.
        payload["fields"][0]["searchable"] = False
        payload["fields"][0]["semantic"] = True
        config = CategoryConfig.model_validate(payload)
        self.assertFalse(config.fields[0].filterable)
        self.assertEqual(config.fields[0].data_type, "text")
        self.assertEqual(config.fields[0].query_filter_mode, "none")
        self.assertEqual(config.fields[0].query_match, "all")
        self.assertEqual(config.rules.semantic_high_confidence_score, 75)

    def test_numeric_required_fields_are_not_inferred_as_equality_filters(self):
        payload = config_payload()
        experience = next(field for field in payload["fields"] if field["key"] == "experience")
        experience["query_filter_mode"] = "required"
        config = CategoryConfig.model_validate(payload)
        understanding = interpret_query(
            "3 years experience",
            config,
            field_values={"experience": {"2", "3", "5"}},
        )
        constraint = next(item for item in understanding["constraints"] if item["field"] == "experience")
        self.assertEqual(constraint["applied_as"], "ranking_hint_only")

    def test_required_skill_and_city_values_are_applied_as_hard_filters(self):
        understanding = interpret_query(
            "backend developer Python SQL Noida",
            self.config,
            field_values=self.index.field_values,
        )
        required = {
            item["field"]: item for item in understanding["constraints"]
            if item["applied_as"] == "hard_filter"
        }
        self.assertEqual(required["skills"]["values"], ["Python", "SQL"])
        self.assertEqual(required["city"]["values"], ["Noida"])

        with (
            patch("app.search_ai.services.platform_search._INDEXES", {}),
            patch("app.search_ai.services.platform_search.category_store.get_category", return_value=self.category),
            patch("app.search_ai.services.platform_search._get_category_index", return_value=self.index),
        ):
            result = search_category(
                query="backend developer Python SQL Noida",
                category_id="jobs",
            )

        self.assertEqual([item["id"] for item in result["data"]], ["job-good"])
        self.assertTrue(result["query_plan"]["filters_applied"])
        self.assertEqual(len(result["query_plan"]["automatic_filters"]), 2)
        self.assertEqual(result["query_plan"]["eligible_document_count"], 1)

    def test_required_any_skill_mode_accepts_one_of_multiple_values(self):
        payload = config_payload()
        payload["fields"][1]["query_match"] = "any"
        config = CategoryConfig.model_validate(payload)
        index = category_index(config, self.documents)
        understanding = interpret_query(
            "Python SQL",
            config,
            field_values=index.field_values,
        )
        constraints = understanding["constraints"]
        eligible, _info = _apply_result_filters(self.documents, config, constraints, [])
        self.assertEqual(eligible, {0, 1, 2})

    def test_required_all_skill_mode_rejects_partial_skill_coverage(self):
        understanding = interpret_query(
            "Python SQL",
            self.config,
            field_values=self.index.field_values,
        )
        eligible, _info = _apply_result_filters(
            self.documents, self.config, understanding["constraints"], []
        )
        self.assertEqual(eligible, {0, 2})

    def test_explicit_numeric_and_location_filters_are_combined_with_and(self):
        filters = [
            {"field": "city", "operator": "eq", "value": "Noida"},
            {"field": "experience", "operator": "gte", "value": "3"},
        ]
        with (
            patch("app.search_ai.services.platform_search._INDEXES", {}),
            patch("app.search_ai.services.platform_search.category_store.get_category", return_value=self.category),
            patch("app.search_ai.services.platform_search._get_category_index", return_value=self.index),
        ):
            result = search_category(
                query="backend developer",
                category_id="jobs",
                filters=filters,
            )
        self.assertEqual([item["id"] for item in result["data"]], ["job-good"])
        self.assertEqual(result["query_plan"]["explicit_filters"], filters)
        self.assertTrue(result["query_plan"]["filters_applied"])

    def test_between_filter_supports_integer_ranges(self):
        field = next(item for item in self.config.fields if item.key == "experience")
        item = {"field": "experience", "operator": "between", "values": [2, 4]}
        self.assertTrue(_document_matches_filter(self.documents[0], field, item))
        self.assertTrue(_document_matches_filter(self.documents[1], field, item))
        self.assertFalse(_document_matches_filter(self.documents[2], field, item))

    def test_null_filter_treats_delimiter_only_multivalue_text_as_empty(self):
        field = next(item for item in self.config.fields if item.key == "skills")
        empty_document = {"fields": {"skills": ", ; | "}}
        self.assertTrue(_document_matches_filter(
            empty_document, field, {"field": "skills", "operator": "is_null"}
        ))
        self.assertFalse(_document_matches_filter(
            empty_document, field, {"field": "skills", "operator": "not_null"}
        ))

    def test_boolean_filter_accepts_boolean_and_string_representations(self):
        field = next(item for item in self.config.fields if item.key == "remote")
        self.assertTrue(_document_matches_filter(
            self.documents[0], field, {"field": "remote", "operator": "eq", "value": "true"}
        ))
        self.assertFalse(_document_matches_filter(
            self.documents[1], field, {"field": "remote", "operator": "eq", "value": True}
        ))

    def test_ne_on_multivalue_field_excludes_any_document_containing_value(self):
        field = next(item for item in self.config.fields if item.key == "skills")
        document = {"fields": {"skills": "Python, SQL"}}
        self.assertFalse(_document_matches_filter(
            document, field, {"field": "skills", "operator": "ne", "value": "Python"}
        ))

    def test_date_filter_normalizes_timezone_aware_and_naive_values(self):
        field = next(item for item in self.config.fields if item.key == "deadline")
        self.assertTrue(_document_matches_filter(
            self.documents[0],
            field,
            {"field": "deadline", "operator": "gte", "value": "2026-12-30T18:30:00Z"},
        ))

    def test_non_filterable_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not configured as filterable"):
            _validate_query_filters(
                self.config,
                [{"field": "title", "operator": "eq", "value": "Backend Developer"}],
            )

    def test_invalid_operator_for_data_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not valid for keyword"):
            _validate_query_filters(
                self.config,
                [{"field": "skills", "operator": "gte", "value": "Python"}],
            )

    def test_invalid_numeric_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Expected an integer"):
            _validate_query_filters(
                self.config,
                [{"field": "experience", "operator": "gte", "value": "many"}],
            )

    def test_invalid_between_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "lower bound exceeds upper"):
            _validate_query_filters(
                self.config,
                [{"field": "experience", "operator": "between", "values": [5, 2]}],
            )

    def test_non_finite_numeric_filter_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            _validate_query_filters(
                self.config,
                [{"field": "experience", "operator": "gte", "value": "NaN"}],
            )

    def test_moderate_semantic_match_requires_meaningful_word_overlap(self):
        document = {
            "document_id": "job-irrelevant",
            "fields": {"title": "Graphic designer", "skills": "Photoshop", "city": "Mumbai"},
        }
        self.assertFalse(
            _semantic_candidate_is_acceptable(55.0, "backend developer python", self.config, document)
        )
        self.assertTrue(
            _semantic_candidate_is_acceptable(80.0, "backend developer python", self.config, document)
        )
        related_document = {
            "document_id": "job-related",
            "fields": {"title": "Backend engineer", "skills": "Python", "city": "Noida"},
        }
        self.assertTrue(
            _semantic_candidate_is_acceptable(55.0, "backend developer python", self.config, related_document)
        )

    def test_preferred_query_constraint_adds_a_bounded_ranking_bonus(self):
        constraints = [{
            "field": "remote",
            "role": "category",
            "values": ["True"],
            "match": "exact_index_value",
            "query_match": "all",
            "applied_as": "ranking_hint_only",
        }]
        bonus = _preferred_constraint_bonus(
            self.documents[0], self.config, constraints
        )
        self.assertGreater(bonus, 0)
        self.assertLessEqual(bonus, 8)
        self.assertEqual(
            _preferred_constraint_bonus(self.documents[1], self.config, constraints),
            0,
        )


class PlatformSearchFilterApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.include_router(admin_router)
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer platform-filter-test-token"}

    def test_search_request_accepts_explicit_typed_filters(self):
        request = PlatformSearchRequest.model_validate({
            "query": "backend developer",
            "category": "jobs",
            "filters": [
                {"field": "city", "operator": "eq", "value": "Noida"},
                {"field": "experience", "operator": "gte", "value": 3},
            ],
        })
        self.assertEqual(len(request.filters), 2)
        self.assertEqual(request.filters[1].value, 3)

    def test_search_request_rejects_invalid_filter_shapes(self):
        invalid_payloads = [
            {"field": "experience", "operator": "between", "values": [2]},
            {"field": "city", "operator": "in", "values": ["Noida"], "value": "Noida"},
        ]
        for item in invalid_payloads:
            with self.subTest(item=item), self.assertRaises(ValidationError):
                PlatformSearchRequest.model_validate({
                    "query": "backend developer",
                    "category": "jobs",
                    "filters": [item],
                })

    def test_admin_search_forwards_filters_to_engine(self):
        expected = {"query": "backend developer", "category": "jobs", "data": []}
        with (
            patch.dict(os.environ, {"SEARCH_ADMIN_API_TOKEN": "platform-filter-test-token"}, clear=True),
            patch(
                "app.search_ai.routes.platform.platform_search.search_category",
                return_value=expected,
            ) as search_mock,
        ):
            response = self.client.post(
                "/api/admin/platform/search",
                headers=self.auth,
                json={
                    "query": "backend developer",
                    "category": "jobs",
                    "filters": [{"field": "city", "operator": "eq", "value": "Noida"}],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(search_mock.call_args.kwargs["filters"], [
            {"field": "city", "operator": "eq", "value": "Noida", "values": []}
        ])

    def test_public_search_forwards_filters_when_category_is_public(self):
        config = CategoryConfig.model_validate(config_payload() | {"public_search_enabled": True})
        record = {"config": config, "status": "active", "config_version": 1, "index_version": 1}
        expected = {"query": "backend developer", "category": "jobs", "data": []}
        with (
            patch("app.search_ai.routes.platform.category_store.get_category", return_value=record),
            patch(
                "app.search_ai.routes.platform.platform_search.search_category",
                return_value=expected,
            ) as search_mock,
        ):
            response = self.client.post(
                "/api/platform/search",
                json={
                    "query": "backend developer",
                    "category": "jobs",
                    "filters": [{"field": "experience", "operator": "gte", "value": 3}],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(search_mock.call_args.kwargs["filters"][0]["field"], "experience")


if __name__ == "__main__":
    unittest.main()
