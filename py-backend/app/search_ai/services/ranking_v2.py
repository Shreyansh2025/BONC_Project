"""Shared BM25F + reciprocal-rank-fusion ranking for generic search categories.

The implementation is schema-driven: field role and configured weight are the
only field-specific inputs. It has no category-name branches and no model/API
calls. Semantic retrieval remains an independent candidate source.
"""
from __future__ import annotations

from collections import Counter
from math import log
from typing import Any

from app.search_ai.services.platform_common import platform_tokens


DEFAULT_STOP_WORDS = {
    "a", "an", "and", "at", "for", "in", "near", "of", "on", "the", "to", "with",
}
ROLE_BOOSTS = {
    "title": 2.6,
    "skill": 2.2,
    "category": 1.45,
    "location": 1.25,
    "description": 1.0,
    "general": 1.0,
}
BM25_K1 = 1.2
BM25_B = 0.75
RRF_K = 60


def _token_forms(token: str) -> set[str]:
    """Conservative plural variants; keep technical tokens such as C++ intact."""
    forms = {token}
    if not token or "+" in token or "#" in token or token.isdigit():
        return forms
    if token.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")
    elif token.endswith("es") and len(token) > 4:
        forms.add(token[:-2])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        forms.add(token[:-1])
    return forms


def _field_boost(field: Any) -> float:
    weight = max(0.0, float(getattr(field, "weight", 1.0)))
    role = str(getattr(field, "role", "general"))
    return weight * ROLE_BOOSTS.get(role, 1.0)


def _normalised_phrase(value: Any) -> str:
    return " ".join(platform_tokens(value))


def _contains_phrase(value: Any, phrase: Any) -> bool:
    text = _normalised_phrase(value)
    needle = _normalised_phrase(phrase)
    return bool(needle and f" {needle} " in f" {text} ")


def build_corpus_statistics(
    config: Any,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build document-frequency and per-field length statistics for one index."""
    document_frequency: Counter[str] = Counter()
    total_field_lengths: Counter[str] = Counter()
    searchable_fields = [
        field for field in config.fields
        if field.searchable and _field_boost(field) > 0
    ]

    for document in documents:
        values = document.get("fields", {})
        document_terms: set[str] = set()
        for field in searchable_fields:
            tokens = platform_tokens(values.get(field.key))
            total_field_lengths[field.key] += len(tokens)
            document_terms.update(tokens)
        document_frequency.update(document_terms)

    count = len(documents)
    average_field_lengths = {
        field.key: (
            total_field_lengths[field.key] / count if count else 0.0
        )
        for field in searchable_fields
    }
    return {
        "document_count": count,
        "document_frequency": dict(document_frequency),
        "average_field_lengths": average_field_lengths,
    }


def score_bm25f(
    query: str,
    config: Any,
    document: dict[str, Any],
    corpus_statistics: dict[str, Any],
    *,
    synonym_expansions: list[dict[str, Any]] | None = None,
    stop_words: set[str] | None = None,
) -> tuple[float, list[str], dict[str, Any]]:
    """Return a bounded BM25F score, matched fields, and explainable evidence.

    IDF is computed per category index. Field-length normalization and role
    boosts are applied before BM25 saturation, so title/skill matches can
    outrank generic-description matches without knowing the category name.
    Synonym alternatives count as one concept rather than many extra query terms.
    """
    ignored = DEFAULT_STOP_WORDS if stop_words is None else stop_words
    expansions = synonym_expansions or []
    primary_tokens = {
        token
        for item in expansions
        for token in platform_tokens(item.get("matched_term", ""))
    }
    query_tokens = list(dict.fromkeys(
        token for token in platform_tokens(query)
        if token not in ignored and token not in primary_tokens
    ))

    fields = [
        field for field in config.fields
        if field.searchable and _field_boost(field) > 0
    ]
    values = document.get("fields", {})
    doc_count = max(1, int(corpus_statistics.get("document_count", 0)))
    document_frequency = corpus_statistics.get("document_frequency", {})
    average_lengths = corpus_statistics.get("average_field_lengths", {})

    def inverse_document_frequency(term: str) -> float:
        frequency = min(doc_count, max(0, int(document_frequency.get(term, 0))))
        return log(1.0 + (doc_count - frequency + 0.5) / (frequency + 0.5))

    raw_score = 0.0
    matched_fields: set[str] = set()
    matched_concepts: list[str] = []
    missing_concepts: list[str] = []
    coverage_weight = 0.0
    matched_coverage_weight = 0.0
    best_phrase_boost = 0.0
    max_possible_field_boost = max((_field_boost(field) for field in fields), default=0.0)

    def saturated_term_score(normalised_tf: float) -> float:
        if normalised_tf <= 0:
            return 0.0
        return ((BM25_K1 + 1.0) * normalised_tf) / (BM25_K1 + normalised_tf)

    def score_term(term: str) -> tuple[float, float, list[str]]:
        idf = inverse_document_frequency(term)
        normalised_tf = 0.0
        term_fields: list[str] = []
        forms = _token_forms(term)
        for field in fields:
            field_tokens = platform_tokens(values.get(field.key))
            if not field_tokens:
                continue
            tf = sum(1 for token in field_tokens if token in forms)
            if not tf:
                continue
            average_length = max(
                1.0, float(average_lengths.get(field.key, 0.0) or 0.0)
            )
            length_norm = 1.0 - BM25_B + BM25_B * (len(field_tokens) / average_length)
            normalised_tf += _field_boost(field) * tf / max(0.25, length_norm)
            term_fields.append(field.key)
        return idf * saturated_term_score(normalised_tf), idf, term_fields

    for term in query_tokens:
        contribution, idf, term_fields = score_term(term)
        coverage_weight += idf
        if contribution > 0:
            raw_score += contribution
            matched_coverage_weight += idf
            matched_concepts.append(term)
            matched_fields.update(term_fields)
        else:
            missing_concepts.append(term)

    # Synonym groups are a single query concept. Prefer a direct phrase, while
    # still allowing an explicitly configured alternative to provide evidence.
    for expansion in expansions:
        primary = str(expansion.get("matched_term", "")).strip()
        alternatives = [
            str(value).strip() for value in expansion.get("alternatives", [])
            if str(value).strip()
        ]
        candidates = ([primary] if primary else []) + alternatives
        if not candidates:
            continue
        primary_group_tokens = platform_tokens(primary)
        concept_idf = max(
            (inverse_document_frequency(token) for token in primary_group_tokens),
            default=1.0,
        )
        coverage_weight += concept_idf
        best = 0.0
        best_field = None
        best_label = None
        for field in fields:
            field_value = values.get(field.key)
            for candidate_index, candidate in enumerate(candidates):
                if not _contains_phrase(field_value, candidate):
                    continue
                direct = candidate_index == 0 and bool(primary)
                factor = 1.0 if direct else 0.85
                candidate_score = concept_idf * saturated_term_score(
                    _field_boost(field) * factor
                )
                if candidate_score > best:
                    best = candidate_score
                    best_field = field.key
                    best_label = candidate
        if best > 0:
            raw_score += best
            matched_coverage_weight += concept_idf
            matched_concepts.append(f"synonym:{best_label}")
            if best_field:
                matched_fields.add(best_field)
        else:
            missing_concepts.append(f"synonym:{primary or candidates[0]}")

    normalised_query = _normalised_phrase(query)
    if normalised_query:
        for field in fields:
            if _contains_phrase(values.get(field.key), normalised_query):
                field_boost = _field_boost(field)
                best_phrase_boost = max(
                    best_phrase_boost,
                    field_boost / max(1.0, max_possible_field_boost),
                )
                matched_fields.add(field.key)
        if best_phrase_boost:
            # Phrase evidence is deliberately bounded so a phrase never
            # overwhelms multiple high-IDF term matches.
            raw_score += 0.75 * best_phrase_boost

    bounded_score = 100.0 * raw_score / (raw_score + 6.0) if raw_score > 0 else 0.0
    coverage = (
        matched_coverage_weight / coverage_weight
        if coverage_weight > 0 else 0.0
    )
    details = {
        "bm25f_raw": round(raw_score, 4),
        "query_term_coverage": round(coverage, 4),
        "matched_terms": matched_concepts,
        "missing_terms": missing_concepts,
        "phrase_score": round(best_phrase_boost * 100.0, 2),
        "phrase_match": bool(best_phrase_boost),
    }
    return round(min(100.0, bounded_score), 4), sorted(matched_fields), details


def reciprocal_rank_fusion_score(
    lexical_rank: int | None,
    semantic_rank: int | None,
    *,
    lexical_weight: float,
    semantic_weight: float,
) -> float:
    """Fuse independently ranked lists using weighted reciprocal-rank fusion."""
    active_weights = []
    score = 0.0
    if lexical_weight > 0:
        active_weights.append(float(lexical_weight))
        if lexical_rank is not None and lexical_rank > 0:
            score += float(lexical_weight) * (RRF_K + 1.0) / (RRF_K + lexical_rank)
    if semantic_weight > 0:
        active_weights.append(float(semantic_weight))
        if semantic_rank is not None and semantic_rank > 0:
            score += float(semantic_weight) * (RRF_K + 1.0) / (RRF_K + semantic_rank)
    denominator = sum(active_weights)
    return round(100.0 * score / denominator, 4) if denominator > 0 else 0.0
