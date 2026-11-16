"""Generic category-driven lexical + semantic search pipeline."""
from __future__ import annotations

import threading
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
)
from app.search_ai.services.search_common import (
    build_inverted_index,
    build_vocabulary,
    candidate_indices,
    correct_query,
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


_INDEXES: dict[str, CategoryIndex] = {}


def _normalise(value: Any) -> str:
    return normalise_query(value)


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

    texts = [_field_text(config, doc, semantic_only=True) for doc in documents]
    vectors_index = None
    if config.rules.semantic_enabled and any(text.strip() for text in texts):
        vectors = get_model().encode(texts, convert_to_numpy=True)
        vectors = np.asarray(vectors, dtype="float32")
        faiss.normalize_L2(vectors)
        vectors_index = faiss.IndexFlatIP(EMBEDDING_DIM)
        vectors_index.add(vectors)

    combined_text = lambda doc: _field_text(config, doc)
    vocabulary = build_vocabulary(documents, combined_text)
    inverted_index = build_inverted_index(documents, combined_text)
    field_values: dict[str, set[str]] = {}
    for field in config.fields:
        if not field.searchable:
            continue
        values: set[str] = set()
        for document in documents:
            raw_value = document.get("fields", {}).get(field.key)
            if raw_value is None:
                continue
            for raw_item in str(raw_value).split(","):
                clean_item = raw_item.strip()
                min_value_length = 1 if field.role == "skill" else 2
                normalized_item = _normalise(clean_item).replace(" ", "")
                if len(normalized_item) >= min_value_length:
                    values.add(clean_item)
        field_values[field.key] = values

    document_id_lookup: dict[str, set[int]] = {}
    for position, document in enumerate(documents):
        normalized_id = _normalise(document.get("document_id"))
        if normalized_id:
            document_id_lookup.setdefault(normalized_id, set()).add(position)

    built = CategoryIndex(
        version=version,
        documents=documents,
        vectors_index=vectors_index,
        vocabulary=vocabulary,
        inverted_index=inverted_index,
        field_values=field_values,
        document_id_lookup=document_id_lookup,
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
        field_tokens = set(value.split())
        matched_tokens = [token for token in set(query_tokens) if token in field_tokens]
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


def search_category(
    *,
    query: str,
    category_id: str,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    category = category_store.get_category(category_id)
    config: CategoryConfig = category["config"]
    if category["status"] != "active" or not config.active:
        raise ValueError(f"Category '{category_id}' is not active")

    index = _get_category_index(category)
    docs = index.documents
    understanding = interpret_query(
        query, config, field_values=index.field_values
    )
    # Only generic residual words are spell-corrected. Exact values such as
    # names, locations and skills are retained as entered; each engine's
    # indexed vocabulary remains isolated from every other engine.
    lexical_query = correct_query(understanding["lexical_query"], index.vocabulary)
    exact_identifier_positions = index.document_id_lookup.get(_normalise(query), set())
    synonym_expansions = understanding.get("synonym_expansions", [])
    expansion_terms = [
        term
        for expansion in synonym_expansions
        for term in expansion.get("alternatives", [])
    ]
    expanded_lexical_query = " ".join([lexical_query, *expansion_terms]).strip()
    scored: list[dict[str, Any]] = []

    semantic_scores: dict[int, float] = {}
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
            if position >= 0:
                semantic_scores[int(position)] = max(0.0, float(similarity) * 100.0)

    lexical_positions = (
        candidate_indices(expanded_lexical_query, index.inverted_index, len(docs))
        if config.rules.lexical_enabled and not exact_identifier_positions
        else set()
    )
    candidate_positions = (
        set(exact_identifier_positions)
        if exact_identifier_positions
        else lexical_positions | set(semantic_scores)
    )

    for position in sorted(candidate_positions):
        document = docs[position]
        exact_identifier_match = position in exact_identifier_positions
        lexical_score, matched_fields = (
            _lexical_score(
                lexical_query,
                config,
                document,
                synonym_expansions=synonym_expansions,
            )
            if position in lexical_positions else (0.0, [])
        )
        phrase_score = (
            _phrase_score(lexical_query, config, document)
            if position in lexical_positions else 0.0
        )
        phrase_bonus = min(10.0, phrase_score * 0.15)
        lexical_score = min(100.0, lexical_score + phrase_bonus)
        semantic_score = semantic_scores.get(position, 0.0)
        if exact_identifier_match:
            lexical_score = 100.0
            phrase_score = 0.0
            matched_fields = ["document_id"]
        if semantic_score < config.rules.semantic_min_score:
            semantic_score = 0.0

        # A score from a disabled/zero-weight signal must not silently
        # become the final score. Apply configured weights consistently.
        lexical_component = (
            lexical_score * config.rules.lexical_weight
            if config.rules.lexical_enabled and config.rules.lexical_weight > 0
            else 0.0
        )
        semantic_component = (
            semantic_score * config.rules.semantic_weight
            if config.rules.semantic_enabled and config.rules.semantic_weight > 0
            else 0.0
        )
        denominator = (
            (config.rules.lexical_weight if config.rules.lexical_enabled else 0.0)
            + (config.rules.semantic_weight if config.rules.semantic_enabled else 0.0)
        )
        if denominator <= 0:
            continue
        score = (
            100.0
            if exact_identifier_match
            else (lexical_component + semantic_component) / denominator
        )
        if score <= 0:
            continue

        result = {
            "id": document["document_id"],
            "title": document.get("title") or document["document_id"],
            "fields": document.get("fields", {}),
            "metadata": document.get("metadata", {}),
            "score": round(score, 2),
            "lexical_score": round(lexical_score, 2),
            "phrase_score": round(phrase_score, 2),
            "semantic_score": round(semantic_score, 2),
            "matched_fields": matched_fields,
            "exact_identifier_match": exact_identifier_match,
            "category": category_id,
        }
        scored.append(result)

    scored.sort(
        key=lambda item: (
            item["exact_identifier_match"],
            item["score"],
            item["semantic_score"],
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
            "filters_applied": False,
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
