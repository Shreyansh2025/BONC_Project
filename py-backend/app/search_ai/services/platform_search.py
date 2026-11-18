"""Generic category-driven lexical + semantic search pipeline."""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import faiss
import numpy as np

from app.search_ai.platform.models import CategoryConfig
from app.search_ai.services import category_store
from app.search_ai.services.embedding_model import EMBEDDING_DIM, get_model
from app.search_ai.services.query_understanding import (
    contains_phrase as _contains_phrase,
    interpret_query,
    normalise_query,
    split_field_values,
)
from app.search_ai.services.platform_common import (
    build_inverted_index,
    build_vocabulary,
    candidate_indices,
    contains_loose,
    correct_query,
    shares_any_word,
)
from app.search_ai.services.ranking_v2 import (
    build_corpus_statistics,
    reciprocal_rank_fusion_score,
    score_bm25f,
)


_STOP_WORDS = {"a", "an", "and", "at", "for", "in", "near", "of", "on", "the", "to", "with"}
_INDEX_LOCK = threading.RLock()


@dataclass
class CategoryIndex:
    version: int
    documents: list[dict[str, Any]]
    vectors_index: faiss.Index | None
    vocabulary: Counter
    inverted_index: dict[str, set[int]]
    field_values: dict[str, set[str]]
    document_id_lookup: dict[str, set[int]] = field(default_factory=dict)
    ranking_statistics: dict[str, Any] = field(default_factory=dict)


_INDEXES: dict[str, CategoryIndex] = {}


def _normalise(value: Any) -> str:
    return normalise_query(value)


def _build_weighted_semantic_vectors(
    config: CategoryConfig,
    documents: list[dict[str, Any]],
) -> np.ndarray:
    """Build one normalized semantic vector per document using field weights."""
    vectors = np.zeros((len(documents), EMBEDDING_DIM), dtype="float32")
    total_weights = np.zeros(len(documents), dtype="float32")
    pending_texts: list[str] = []
    pending_rows: list[int] = []
    pending_weights: list[float] = []

    for field_config in config.fields:
        if (
            not field_config.searchable
            or not field_config.semantic
            or field_config.weight <= 0
        ):
            continue
        for row_index, document in enumerate(documents):
            value = document.get("fields", {}).get(field_config.key)
            if value is None or not str(value).strip():
                continue
            pending_texts.append(f"{field_config.label}: {str(value).strip()}")
            pending_rows.append(row_index)
            pending_weights.append(float(field_config.weight))

    if not pending_texts:
        return vectors

    model = get_model()
    chunk_size = 256
    for chunk_start in range(0, len(pending_texts), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(pending_texts))
        model_vectors = model.encode(
            pending_texts[chunk_start:chunk_end],
            convert_to_numpy=True,
            batch_size=64,
        )
        model_vectors = np.ascontiguousarray(model_vectors, dtype="float32")
        expected_shape = (chunk_end - chunk_start, EMBEDDING_DIM)
        if model_vectors.ndim != 2 or model_vectors.shape != expected_shape:
            raise ValueError("Embedding model returned an unexpected vector shape")
        # Normalize each field vector before applying its configured weight;
        # otherwise raw embedding magnitude can distort the intended weights.
        faiss.normalize_L2(model_vectors)

        for offset, vector in enumerate(model_vectors):
            row_index = pending_rows[chunk_start + offset]
            weight = pending_weights[chunk_start + offset]
            vectors[row_index] += vector * weight
            total_weights[row_index] += weight

    populated_rows = total_weights > 0
    vectors[populated_rows] /= total_weights[populated_rows, None]
    faiss.normalize_L2(vectors)
    return vectors


def _field_text(config: CategoryConfig, document: dict[str, Any], *, semantic_only: bool = False) -> str:
    parts: list[str] = []
    values = document.get("fields", {})
    for field in config.fields:
        if not field.searchable or (semantic_only and not field.semantic):
            continue
        value = values.get(field.key)
        if value is None:
            continue
        value_text = str(value).strip()
        if value_text:
            # Field labels give the embedding model context without requiring
            # category-specific embedding code.
            parts.append(f"{field.label}: {value_text}")
    return " ".join(parts)


def _field_values_text(config: CategoryConfig, document: dict[str, Any]) -> str:
    """Join actual searchable values without synthetic field labels."""
    values = document.get("fields", {})
    return " ".join(
        str(values.get(field.key)).strip()
        for field in config.fields
        if field.searchable
        and values.get(field.key) is not None
        and str(values.get(field.key)).strip()
    )


def invalidate_category_index(category_id: str) -> None:
    with _INDEX_LOCK:
        _INDEXES.pop(category_id, None)


def _get_category_index(category: dict[str, Any]) -> CategoryIndex:
    config: CategoryConfig = category["config"]
    category_id = config.category_id
    version = int(category["index_version"])
    with _INDEX_LOCK:
        cached = _INDEXES.get(category_id)
        if cached is not None and cached.version == version:
            return cached

    documents = category_store.list_documents(category_id)
    if not documents:
        raise ValueError(f"Category '{category_id}' has no indexed documents. Sync or ingest data first.")

    vectors_index = None
    has_semantic_fields = any(
        field.searchable and field.semantic and field.weight > 0
        for field in config.fields
    )
    has_semantic_values = any(
        document.get("fields", {}).get(field.key) is not None
        and str(document.get("fields", {}).get(field.key)).strip()
        for document in documents
        for field in config.fields
        if field.searchable and field.semantic and field.weight > 0
    )
    if config.rules.semantic_enabled and has_semantic_fields and has_semantic_values:
        vectors = _build_weighted_semantic_vectors(config, documents)
        vectors_index = faiss.IndexFlatIP(EMBEDDING_DIM)
        vectors_index.add(vectors)

    combined_text = lambda doc: _field_text(config, doc)
    vocabulary = build_vocabulary(documents, combined_text)
    inverted_index = build_inverted_index(documents, combined_text)
    field_values: dict[str, set[str]] = {}
    for field in config.fields:
        if not field.searchable and not field.filterable:
            continue
        values: set[str] = set()
        for document in documents:
            raw_value = document.get("fields", {}).get(field.key)
            if raw_value is None:
                continue
            for clean_item in split_field_values(raw_value):
                min_value_length = 1 if field.role == "skill" or field.data_type in {"integer", "number", "boolean"} else 2
                normalized_item = _normalise(clean_item).replace(" ", "")
                if len(normalized_item) >= min_value_length:
                    values.add(clean_item)
        field_values[field.key] = values

    document_id_lookup: dict[str, set[int]] = {}
    for position, document in enumerate(documents):
        raw_id = str(document.get("document_id") or "").strip()
        if raw_id:
            document_id_lookup.setdefault(raw_id, set()).add(position)

    built = CategoryIndex(
        version=version,
        documents=documents,
        vectors_index=vectors_index,
        vocabulary=vocabulary,
        inverted_index=inverted_index,
        field_values=field_values,
        document_id_lookup=document_id_lookup,
        ranking_statistics=build_corpus_statistics(config, documents),
    )
    with _INDEX_LOCK:
        _INDEXES[category_id] = built
    return built


def _query_understanding(
    query: str,
    config: CategoryConfig,
    documents: list[dict[str, Any]] | None = None,
    field_values: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around the shared query interpretation module."""
    return interpret_query(
        query,
        config,
        documents=documents,
        field_values=field_values,
    )


def _lexical_score(
    query: str,
    config: CategoryConfig,
    document: dict[str, Any],
    *,
    synonym_expansions: list[dict[str, Any]] | None = None,
) -> tuple[float, list[str]]:
    expansions = synonym_expansions or []
    primary_synonym_tokens = {
        token
        for expansion in expansions
        for token in str(expansion.get("matched_term", "")).split()
    }
    query_tokens = [
        token for token in _normalise(query).split()
        if token not in _STOP_WORDS and token not in primary_synonym_tokens
    ]

    searchable_fields = [
        field for field in config.fields
        if field.searchable and field.weight > 0
    ]
    total_weight = sum(field.weight for field in searchable_fields)
    if total_weight <= 0:
        return 0.0, []

    values = document.get("fields", {})
    # A query token contributes the weight of the most important field in
    # which it appears. This allows a natural-language query to match across
    # multiple fields (name + skills + location) without dividing every
    # field's contribution by every word in the whole query.
    token_weights: dict[str, float] = {}
    matched_fields: list[str] = []
    for field in searchable_fields:
        value = _normalise(values.get(field.key))
        if not value:
            continue
        matched_tokens = [
            token for token in set(query_tokens) if contains_loose(token, value)
        ]
        if matched_tokens:
            matched_fields.append(field.key)
        for token in matched_tokens:
            token_weights[token] = max(token_weights.get(token, 0.0), field.weight)

        # A synonym group represents one concept, not a collection of extra
        # query terms. Count at most one group contribution per document and
        # prefer the direct term over an expanded alternative.
        for index, expansion in enumerate(expansions):
            primary = str(expansion.get("matched_term", ""))
            alternatives = [
                str(term) for term in expansion.get("alternatives", [])
            ]
            direct_match = bool(primary and _contains_phrase(value, primary))
            alternative_match = any(
                _contains_phrase(value, alternative)
                for alternative in alternatives
            )
            if direct_match:
                contribution = field.weight
            elif alternative_match:
                contribution = field.weight * 0.85
            else:
                continue
            group_key = f"__synonym_group_{index}"
            token_weights[group_key] = max(token_weights.get(group_key, 0.0), contribution)
            if field.key not in matched_fields:
                matched_fields.append(field.key)

    score = sum(token_weights.values()) / total_weight * 100.0
    return min(100.0, score), matched_fields


def _phrase_score(query: str, config: CategoryConfig, document: dict[str, Any]) -> float:
    """Return a weighted exact-phrase signal for phrases within a longer query."""
    tokens = _normalise(query).split()
    if len(tokens) < 2:
        return 0.0

    phrases = {
        " ".join(tokens[start:start + size])
        for size in range(2, min(4, len(tokens)) + 1)
        for start in range(0, len(tokens) - size + 1)
    }
    searchable_fields = [
        field for field in config.fields
        if field.searchable and field.weight > 0
    ]
    total_weight = sum(field.weight for field in searchable_fields)
    if total_weight <= 0:
        return 0.0

    values = document.get("fields", {})
    best_weight = 0.0
    for field in searchable_fields:
        field_text = _normalise(values.get(field.key))
        if field_text and any(_contains_phrase(field_text, phrase) for phrase in phrases):
            best_weight = max(best_weight, field.weight)
    return min(100.0, best_weight / total_weight * 100.0)



def _semantic_candidate_is_acceptable(
    score: float,
    query: str,
    config: CategoryConfig,
    document: dict[str, Any],
) -> bool:
    """Reject medium-confidence candidates supported by only one generic term.

    Very high model confidence remains a valid semantic-only path. Below that
    threshold, multi-term queries must share at least two meaningful terms
    (or every term for one-term queries), with field labels excluded from text.
    """
    query_tokens = {
        token for token in _normalise(query).split()
        if token not in _STOP_WORDS
    }
    target_tokens = {
        token for token in _normalise(_field_values_text(config, document)).split()
        if token not in _STOP_WORDS
    }
    overlap = query_tokens & target_tokens
    required_overlap = min(2, len(query_tokens))
    if query_tokens and len(overlap) >= required_overlap:
        return True
    # A semantic-only match with no/insufficient literal overlap needs a much
    # stronger margin than the configured normal high-confidence threshold.
    # This stops one generic shared word from admitting weak candidates.
    semantic_only_threshold = min(
        100.0,
        max(90.0, config.rules.semantic_high_confidence_score + 10.0),
    )
    return score >= semantic_only_threshold
}

def _parse_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value in {"true", "1", "yes", "y"}:
        return True
    if text_value in {"false", "0", "no", "n"}:
        return False
    return None


def _coerce_filter_value(value: Any, data_type: str) -> Any:
    if value is None:
        return None
    if data_type == "integer":
        try:
            numeric = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"Expected an integer filter value, received {value!r}") from None
        if not numeric.is_finite() or numeric != numeric.to_integral_value():
            raise ValueError(f"Expected a finite integer filter value, received {value!r}")
        return int(numeric)
    if data_type == "number":
        try:
            numeric = Decimal(str(value).strip())
            if not numeric.is_finite():
                raise InvalidOperation
            return numeric
        except (InvalidOperation, ValueError):
            raise ValueError(f"Expected a finite numeric filter value, received {value!r}") from None
    if data_type == "boolean":
        parsed = _parse_boolean(value)
        if parsed is None:
            raise ValueError(f"Expected a boolean filter value, received {value!r}")
        return parsed
    if data_type == "date":
        try:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, date):
                parsed = datetime.combine(value, datetime.min.time())
            else:
                text_value = str(value).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(text_value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except (TypeError, ValueError):
            raise ValueError(f"Expected an ISO date or datetime filter value, received {value!r}") from None
    return _normalise(value)


def _raw_field_values(value: Any, data_type: str) -> list[Any]:
    if value is None:
        return []
    if data_type in {"integer", "number", "boolean", "date"}:
        return [value]
    return split_field_values(value)


def _compare_filter_value(actual: Any, expected: Any, data_type: str, operator: str) -> bool:
    if operator == "contains":
        if actual is None:
            return False
        return _contains_phrase(_normalise(actual), _normalise(expected))
    try:
        actual_value = _coerce_filter_value(actual, data_type)
        expected_value = _coerce_filter_value(expected, data_type)
    except ValueError:
        return False
    if operator == "eq":
        return actual_value == expected_value
    if operator == "ne":
        return actual_value != expected_value
    if operator == "gte":
        return actual_value is not None and expected_value is not None and actual_value >= expected_value
    if operator == "lte":
        return actual_value is not None and expected_value is not None and actual_value <= expected_value
    return False


def _validate_query_filters(config: CategoryConfig, filters: list[dict[str, Any]]) -> None:
    fields = {field.key: field for field in config.fields}
    for item in filters:
        field = fields.get(str(item.get("field", "")).strip())
        if field is None or not field.filterable:
            raise ValueError(f"Field '{item.get('field')}' is not configured as filterable")
        operator = item.get("operator")
        data_type = field.data_type
        allowed = {"eq", "ne", "in", "is_null", "not_null"}
        if data_type in {"text", "keyword"}:
            allowed.add("contains")
        if data_type in {"integer", "number", "date"}:
            allowed.update({"gte", "lte", "between"})
        if operator not in allowed:
            raise ValueError(f"Operator '{operator}' is not valid for {data_type} field '{field.key}'")
        if operator in {"eq", "ne", "contains", "gte", "lte"}:
            _coerce_filter_value(item.get("value"), data_type)
        if operator == "in":
            if not item.get("values"):
                raise ValueError(f"Filter '{field.key}' requires one or more values")
            for value in item["values"]:
                _coerce_filter_value(value, data_type)
        if operator == "between":
            values = item.get("values") or []
            if len(values) != 2:
                raise ValueError(f"Between filter for '{field.key}' requires exactly two values")
            low, high = (_coerce_filter_value(value, data_type) for value in values)
            if low > high:
                raise ValueError(f"Between filter lower bound exceeds upper bound for '{field.key}'")


def _document_matches_filter(document: dict[str, Any], field: Any, item: dict[str, Any]) -> bool:
    raw = document.get("fields", {}).get(field.key)
    operator = item["operator"]
    values = _raw_field_values(raw, field.data_type)
    if operator == "is_null":
        return not values
    if operator == "not_null":
        return bool(values)
    if operator == "in":
        expected = item.get("values", [])
        return any(
            _compare_filter_value(actual, candidate, field.data_type, "eq")
            for actual in values for candidate in expected
        )
    if operator == "ne":
        # For multi-value fields, "not equal" means no stored value may equal
        # the excluded value. Any-different-value semantics would be misleading.
        return bool(values) and not any(
            _compare_filter_value(actual, item.get("value"), field.data_type, "eq")
            for actual in values
        )
    if operator == "between":
        low, high = item["values"]
        return any(
            _compare_filter_value(actual, low, field.data_type, "gte")
            and _compare_filter_value(actual, high, field.data_type, "lte")
            for actual in values
        )
    return any(
        _compare_filter_value(actual, item.get("value"), field.data_type, operator)
        for actual in values
    )


def _document_matches_query_constraint(
    document: dict[str, Any], field: Any, constraint: dict[str, Any]
) -> bool:
    actual_values = _raw_field_values(
        document.get("fields", {}).get(field.key), field.data_type
    )
    expected_values = constraint.get("values", [])
    if not actual_values or not expected_values:
        return False
    matched = [
        any(
            _compare_filter_value(actual, expected, field.data_type, "eq")
            for actual in actual_values
        )
        for expected in expected_values
    ]
    return all(matched) if field.query_match == "all" else any(matched)


def _apply_result_filters(
    documents: list[dict[str, Any]],
    config: CategoryConfig,
    constraints: list[dict[str, Any]],
    explicit_filters: list[dict[str, Any]],
) -> tuple[set[int], dict[str, Any]]:
    """Apply configured required query constraints and explicit API filters."""
    _validate_query_filters(config, explicit_filters)
    fields = {field.key: field for field in config.fields}
    required_constraints = [
        item for item in constraints if item.get("applied_as") == "hard_filter"
    ]
    eligible: set[int] = set()
    for position, document in enumerate(documents):
        matches_required = all(
            _document_matches_query_constraint(document, fields[item["field"]], item)
            for item in required_constraints
        )
        matches_explicit = all(
            _document_matches_filter(document, fields[item["field"]], item)
            for item in explicit_filters
        )
        if matches_required and matches_explicit:
            eligible.add(position)

    info = {
        "filters_applied": bool(required_constraints or explicit_filters),
        "automatic_filters": required_constraints,
        "explicit_filters": explicit_filters,
        "filtered_document_count": len(documents) - len(eligible),
        "eligible_document_count": len(eligible),
    }
    return eligible, info


def _preferred_constraint_bonus(
    document: dict[str, Any],
    config: CategoryConfig,
    constraints: list[dict[str, Any]],
) -> float:
    """Apply a small generic boost for recognized field values.

    This is ranking evidence, not a hard filter. It intentionally works for
    fields whose query_filter_mode is 'none'; only required constraints are
    applied as filters by _apply_result_filters.
    """
    fields = {field.key: field for field in config.fields}
    hints = [
        item for item in constraints
        if item.get("applied_as") == "ranking_hint_only"
        and item.get("field") in fields
    ]
    if not hints:
        return 0.0

    role_boosts = {
        "title": 1.5, "skill": 1.4, "category": 1.2,
        "location": 1.1, "description": 0.8, "general": 0.8,
    }
    matched_weight = 0.0
    total_weight = 0.0
    for item in hints:
        field = fields[item["field"]]
        weight = max(float(field.weight), 1.0) * role_boosts.get(field.role, 0.8)
        total_weight += weight
        if _document_matches_query_constraint(document, field, item):
            matched_weight += weight
    return round(6.0 * matched_weight / total_weight, 2) if total_weight else 0.0

def search_category(
    *,
    query: str,
    category_id: str,
    page: int = 1,
    page_size: int = 10,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search one category with shared BM25F retrieval and hybrid rank fusion."""
    category = category_store.get_category(category_id)
    config: CategoryConfig = category["config"]
    if category["status"] != "active" or not config.active:
        raise ValueError(f"Category '{category_id}' is not active")

    index = _get_category_index(category)
    docs = index.documents
    understanding = interpret_query(
        query, config, field_values=index.field_values
    )
    lexical_query = correct_query(understanding["lexical_query"], index.vocabulary)
    explicit_filters = filters or []
    eligible_positions, filter_info = _apply_result_filters(
        docs, config, understanding.get("constraints", []), explicit_filters
    )
    exact_identifier_positions = index.document_id_lookup.get(query.strip(), set())
    synonym_expansions = understanding.get("synonym_expansions", [])
    expansion_terms = [
        term
        for expansion in synonym_expansions
        for term in expansion.get("alternatives", [])
    ]
    expanded_lexical_query = " ".join([lexical_query, *expansion_terms]).strip()

    semantic_scores: dict[int, float] = {}
    semantic_rejected_count = 0
    if (
        not exact_identifier_positions
        and config.rules.semantic_enabled
        and index.vectors_index is not None
    ):
        query_vector = get_model().encode([query], convert_to_numpy=True)
        query_vector = np.asarray(query_vector, dtype="float32")
        faiss.normalize_L2(query_vector)
        limit = min(config.rules.semantic_candidate_limit, len(docs))
        similarities, positions = index.vectors_index.search(query_vector, limit)
        for similarity, position in zip(similarities[0], positions[0]):
            if position < 0:
                continue
            semantic_score = max(0.0, float(similarity) * 100.0)
            if not _semantic_candidate_is_acceptable(
                semantic_score,
                expanded_lexical_query,
                config,
                docs[int(position)],
            ):
                semantic_rejected_count += 1
                continue
            if semantic_score >= config.rules.semantic_min_score:
                semantic_scores[int(position)] = semantic_score
            else:
                semantic_rejected_count += 1

    # Build the candidate set for recognized field-value hints separately.
    # This supports a query made entirely from an exact indexed value even when
    # query understanding removes that value from the residual lexical query.
    searchable_config_fields = {field.key: field for field in config.fields}
    hint_constraints = [
        item for item in understanding.get("constraints", [])
        if item.get("applied_as") == "ranking_hint_only"
        and item.get("field") in searchable_config_fields
    ]
    hint_positions: set[int] = set()
    if hint_constraints and not exact_identifier_positions:
        for position in eligible_positions:
            document = docs[position]
            if any(
                _document_matches_query_constraint(
                    document,
                    searchable_config_fields[item["field"]],
                    item,
                )
                for item in hint_constraints
            ):
                hint_positions.add(position)

    lexical_terms = [
        token for token in _normalise(expanded_lexical_query).split()
        if token not in _STOP_WORDS
    ]
    lexical_positions = (
        candidate_indices(expanded_lexical_query, index.inverted_index, len(docs))
        if config.rules.lexical_enabled
        and not exact_identifier_positions
        and lexical_terms
        else set()
    )
    candidate_positions = (
        set(exact_identifier_positions)
        if exact_identifier_positions
        else lexical_positions | set(semantic_scores) | hint_positions
    ) & eligible_positions

    ranking_statistics = (
        index.ranking_statistics
        or build_corpus_statistics(config, docs)
    )
    lexical_evidence: dict[int, tuple[float, list[str], dict[str, Any]]] = {}
    if config.rules.lexical_enabled and not exact_identifier_positions:
        for position in lexical_positions & eligible_positions:
            lexical_evidence[position] = score_bm25f(
                lexical_query,
                config,
                docs[position],
                ranking_statistics,
                synonym_expansions=synonym_expansions,
                stop_words=_STOP_WORDS,
            )

    lexical_order = sorted(
        (
            position for position, evidence in lexical_evidence.items()
            if evidence[0] > 0
        ),
        key=lambda position: (
            lexical_evidence[position][0],
            lexical_evidence[position][2]["query_term_coverage"],
        ),
        reverse=True,
    )
    semantic_order = sorted(
        (position for position in semantic_scores if position in eligible_positions),
        key=lambda position: semantic_scores[position],
        reverse=True,
    )
    lexical_ranks = {
        position: rank for rank, position in enumerate(lexical_order, start=1)
    }
    semantic_ranks = {
        position: rank for rank, position in enumerate(semantic_order, start=1)
    }
    hybrid_available = bool(lexical_ranks and semantic_ranks)

    scored: list[dict[str, Any]] = []
    for position in sorted(candidate_positions):
        document = docs[position]
        exact_identifier_match = position in exact_identifier_positions
        lexical_score, matched_fields, lexical_details = lexical_evidence.get(
            position, (0.0, [], {
                "bm25f_raw": 0.0,
                "query_term_coverage": 0.0,
                "matched_terms": [],
                "missing_terms": [],
                "phrase_score": 0.0,
                "phrase_match": False,
            })
        )
        semantic_score = semantic_scores.get(position, 0.0)
        if exact_identifier_match:
            lexical_score = 100.0
            matched_fields = ["document_id"]
            lexical_details = {
                "bm25f_raw": 0.0,
                "query_term_coverage": 1.0,
                "matched_terms": ["document_id"],
                "missing_terms": [],
                "phrase_score": 0.0,
                "phrase_match": False,
            }

        lexical_rank = lexical_ranks.get(position)
        semantic_rank = semantic_ranks.get(position)
        preferred_bonus = _preferred_constraint_bonus(
            document, config, understanding.get("constraints", [])
        )
        # Fully recognized field-value queries can legitimately have no
        # residual lexical tokens. Keep matching hint documents instead of
        # dropping them before their bounded soft boost is applied.
        if (
            not exact_identifier_match
            and lexical_score <= 0
            and semantic_score <= 0
            and preferred_bonus <= 0
        ):
            continue

        if exact_identifier_match:
            base_score = 100.0
        elif hybrid_available:
            base_score = reciprocal_rank_fusion_score(
                lexical_rank,
                semantic_rank,
                lexical_weight=config.rules.lexical_weight if config.rules.lexical_enabled else 0.0,
                semantic_weight=config.rules.semantic_weight if config.rules.semantic_enabled else 0.0,
            )
        elif lexical_ranks:
            # Preserve the interpretable BM25F score when semantic retrieval
            # is unavailable or contributed no accepted candidates.
            base_score = lexical_score
        else:
            base_score = semantic_score

        score = min(100.0, base_score + preferred_bonus)
        if score <= 0:
            continue

        ranking_signals = {
            "engine": "bm25f_rrf_v2",
            "fusion": "rrf" if hybrid_available else "single_channel",
            "lexical_rank": lexical_rank,
            "semantic_rank": semantic_rank,
            "bm25f_raw": lexical_details["bm25f_raw"],
            "query_term_coverage": lexical_details["query_term_coverage"],
            "matched_terms": lexical_details["matched_terms"],
            "missing_terms": lexical_details["missing_terms"],
            "phrase_match": lexical_details["phrase_match"],
            "rank_fusion_score": round(base_score, 2),
            "constraint_boost": preferred_bonus,
        }
        result = {
            "id": document["document_id"],
            "title": document.get("title") or document["document_id"],
            "fields": document.get("fields", {}),
            "metadata": document.get("metadata", {}),
            "score": round(score, 2),
            "lexical_score": round(lexical_score, 2),
            "phrase_score": round(lexical_details["phrase_score"], 2),
            "semantic_score": round(semantic_score, 2),
            "preferred_filter_bonus": preferred_bonus,
            "matched_fields": matched_fields,
            "ranking_signals": ranking_signals,
            "exact_identifier_match": exact_identifier_match,
            "category": category_id,
        }
        scored.append(result)

    scored.sort(
        key=lambda item: (
            item["exact_identifier_match"],
            item["score"],
            item["lexical_score"],
            item["semantic_score"],
            item["ranking_signals"]["query_term_coverage"],
        ),
        reverse=True,
    )
    total = len(scored)
    offset = (page - 1) * page_size
    return {
        "query": query,
        "category": category_id,
        "query_understanding": understanding,
        "query_plan": {
            "query_type": understanding["query_type"],
            "matched_aliases": understanding["matched_aliases"],
            "matched_values": understanding["matched_values"],
            "constraints": understanding["constraints"],
            "unmatched_tokens": understanding["unmatched_tokens"],
            "lexical_query": lexical_query,
            "expanded_lexical_query": expanded_lexical_query,
            "synonym_expansions": synonym_expansions,
            "semantic_query": query,
            **filter_info,
            "ranking_engine": "bm25f_rrf_v2",
            "ranking_fusion": "reciprocal_rank_fusion" if hybrid_available else "single_channel",
            "lexical_candidates": len(lexical_order),
            "semantic_candidates_kept": len(semantic_order),
            "semantic_candidates_rejected": semantic_rejected_count,
            "semantic_high_confidence_score": config.rules.semantic_high_confidence_score,
            "explanation": understanding["explanation"],
        },
        "corrected_query": lexical_query,
        "data": scored[offset: offset + page_size],
        "total_count": total,
        "page": page,
        "page_size": page_size,
        "config_version": int(category["config_version"]),
        "index_version": index.version,
    }

