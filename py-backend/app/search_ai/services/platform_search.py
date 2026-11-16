"""Generic category-driven lexical + semantic search pipeline."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import faiss
import numpy as np

from app.search_ai.platform.models import CategoryConfig
from app.search_ai.services import category_store
from app.search_ai.services.embedding_model import EMBEDDING_DIM, get_model


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_INDEX_LOCK = threading.RLock()


@dataclass
class CategoryIndex:
    version: int
    documents: list[dict[str, Any]]
    vectors_index: faiss.Index | None


_INDEXES: dict[str, CategoryIndex] = {}


def _normalise(value: Any) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


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

    built = CategoryIndex(version=version, documents=documents, vectors_index=vectors_index)
    with _INDEX_LOCK:
        _INDEXES[category_id] = built
    return built


def _query_understanding(query: str, config: CategoryConfig, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Lightweight, data-driven field hint extraction.

    It identifies field values/aliases explicitly present in the query. It
    does not pretend to infer entities that are absent from the source data;
    semantic matching still evaluates the complete natural-language query.
    """
    normal_query = _normalise(query)
    query_tokens = set(normal_query.split())
    field_hints: dict[str, list[str]] = {}
    for field in config.fields:
        if not field.searchable:
            continue
        recognised: set[str] = set()
        for alias in field.aliases:
            alias_text = _normalise(alias)
            if alias_text and alias_text in normal_query:
                recognised.add(alias)
        for doc in documents:
            raw = doc.get("fields", {}).get(field.key)
            if raw is None:
                continue
            for value in str(raw).split(","):
                candidate = _normalise(value)
                if len(candidate) >= 2 and candidate in normal_query:
                    recognised.add(str(value).strip())
        if recognised:
            field_hints[field.key] = sorted(recognised)
    return {
        "normalised_query": normal_query,
        "matched_fields": field_hints,
        "unmatched_tokens": sorted(
            token for token in query_tokens
            if not any(token in _normalise(value).split() for values in field_hints.values() for value in values)
        ),
    }


def _lexical_score(query: str, config: CategoryConfig, document: dict[str, Any]) -> tuple[float, list[str]]:
    query_tokens = _normalise(query).split()
    if not query_tokens:
        return 0.0, []
    total_weight = sum(field.weight for field in config.fields if field.searchable and field.weight > 0)
    if total_weight <= 0:
        return 0.0, []

    values = document.get("fields", {})
    matched_weight = 0.0
    matched_fields: list[str] = []
    normal_query = _normalise(query)

    for field in config.fields:
        if not field.searchable or field.weight <= 0:
            continue
        value = _normalise(values.get(field.key))
        if not value:
            continue
        field_tokens = set(value.split())
        hits = sum(1 for token in set(query_tokens) if token in field_tokens)
        phrase_hit = len(normal_query) > 0 and normal_query in value
        if hits:
            coverage = hits / len(set(query_tokens))
            contribution = field.weight * coverage
            if phrase_hit:
                contribution = field.weight
            matched_weight += contribution
            matched_fields.append(field.key)

    return min(100.0, (matched_weight / total_weight) * 100.0), matched_fields


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
    understanding = _query_understanding(query, config, docs)
    scored: list[dict[str, Any]] = []

    semantic_scores: dict[int, float] = {}
    if config.rules.semantic_enabled and index.vectors_index is not None:
        query_vector = get_model().encode([query], convert_to_numpy=True)
        query_vector = np.asarray(query_vector, dtype="float32")
        faiss.normalize_L2(query_vector)
        limit = min(config.rules.semantic_candidate_limit, len(docs))
        similarities, positions = index.vectors_index.search(query_vector, limit)
        for similarity, position in zip(similarities[0], positions[0]):
            if position >= 0:
                semantic_scores[int(position)] = max(0.0, float(similarity) * 100.0)

    for position, document in enumerate(docs):
        lexical_score, matched_fields = (
            _lexical_score(query, config, document) if config.rules.lexical_enabled else (0.0, [])
        )
        semantic_score = semantic_scores.get(position, 0.0)
        if semantic_score < config.rules.semantic_min_score:
            semantic_score = 0.0

        if lexical_score <= 0 and semantic_score <= 0:
            continue
        if lexical_score > 0 and semantic_score > 0:
            denominator = config.rules.lexical_weight + config.rules.semantic_weight
            score = (
                lexical_score * config.rules.lexical_weight
                + semantic_score * config.rules.semantic_weight
            ) / denominator if denominator else max(lexical_score, semantic_score)
        else:
            score = lexical_score or semantic_score

        result = {
            "id": document["document_id"],
            "title": document.get("title") or document["document_id"],
            "fields": document.get("fields", {}),
            "metadata": document.get("metadata", {}),
            "score": round(score, 2),
            "lexical_score": round(lexical_score, 2),
            "semantic_score": round(semantic_score, 2),
            "matched_fields": matched_fields,
            "category": category_id,
        }
        scored.append(result)

    scored.sort(key=lambda item: (item["score"], item["semantic_score"]), reverse=True)
    total = len(scored)
    offset = (page - 1) * page_size
    return {
        "query": query,
        "category": category_id,
        "query_understanding": understanding,
        "data": scored[offset: offset + page_size],
        "total_count": total,
        "page": page,
        "page_size": page_size,
        "index_version": index.version,
    }
