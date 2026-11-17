"""Deterministic query interpretation for configuration-driven search engines.

The interpreter is category-local: aliases and values come only from the
selected category. Exact values can become actual query-time filters only when
an administrator explicitly configures that field as filterable with
query_filter_mode="required".
"""
from __future__ import annotations

import re
from typing import Any

from app.search_ai.platform.models import CategoryConfig


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[+#]+)?")
_STOP_WORDS = {"a", "an", "and", "at", "for", "in", "near", "of", "on", "the", "to", "with"}


def normalise_query(value: Any) -> str:
    """Return the token-normalised form used by the generic search pipeline."""
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def contains_phrase(normal_query: str, phrase: str) -> bool:
    """Match a whole phrase, not a substring inside another token."""
    phrase_text = normalise_query(phrase)
    if not normal_query or not phrase_text:
        return False
    pattern = r"(?<![a-z0-9+#])" + re.escape(phrase_text) + r"(?![a-z0-9+#])"
    return re.search(pattern, normal_query) is not None


def split_field_values(value: Any) -> list[str]:
    """Split common multi-value source fields without splitting ordinary phrases."""
    if value is None:
        return []
    return [
        item.strip()
        for item in re.split(r"[,;|\n]+", str(value))
        if item and item.strip()
    ]


def _phrase_variants(phrase: str) -> tuple[str, ...]:
    """Return conservative singular/plural forms for a configured alias."""
    words = phrase.split()
    if not words:
        return ()
    last = words[-1]
    if len(last) < 3:
        return (phrase,)
    if last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
        plural = last[:-1] + "ies"
    elif last.endswith(("s", "x", "z", "ch", "sh")):
        plural = last + "es"
    else:
        plural = last + "s"
    plural_phrase = " ".join([*words[:-1], plural])
    return (phrase, plural_phrase) if plural_phrase != phrase else (phrase,)


def _without_phrases(query: str, phrases: set[str]) -> str:
    result = query
    for phrase in sorted(phrases, key=lambda item: (-len(item), item)):
        if not phrase:
            continue
        pattern = r"(?<![a-z0-9+#])" + re.escape(phrase) + r"(?![a-z0-9+#])"
        result = re.sub(pattern, " ", result)
    return " ".join(result.split())


def _expand_synonym_groups(
    query: str,
    groups: list[Any],
    protected_phrases: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Expand category-local synonyms without rewriting exact indexed values."""
    normalised_query = normalise_query(query)
    additions: list[str] = []
    expansions: list[dict[str, Any]] = []
    seen_additions: set[str] = set(normalised_query.split())

    for group in groups:
        terms = [
            normalise_query(term)
            for term in getattr(group, "terms", [])
        ]
        terms = [term for term in terms if len(term.replace(" ", "")) >= 2]
        matched = [
            term for term in terms
            if contains_phrase(normalised_query, term) and term not in protected_phrases
        ]
        if not matched:
            continue

        matched_term = sorted(matched, key=lambda item: (-len(item), item))[0]
        alternatives = [
            term for term in terms
            if term != matched_term
            and term not in protected_phrases
            and not contains_phrase(normalised_query, term)
            and term not in seen_additions
        ]
        if not alternatives:
            continue

        expansions.append({
            "matched_term": matched_term,
            "alternatives": alternatives,
        })
        for alternative in alternatives:
            additions.extend(alternative.split())
            seen_additions.add(alternative)

    return " ".join([query, *additions]).strip(), expansions


def interpret_query(
    query: str,
    config: CategoryConfig,
    *,
    field_values: dict[str, set[str]] | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a transparent field/value plan using only this category's schema."""
    normalised = normalise_query(query)
    matched_aliases: dict[str, list[str]] = {}
    matched_values: dict[str, list[str]] = {}
    matched_fields: dict[str, list[str]] = {}
    alias_phrases: set[str] = set()
    recognised_phrases: set[str] = set()
    matched_value_phrases: set[str] = set()

    for field in config.fields:
        if not field.searchable and not field.filterable:
            continue

        aliases: set[str] = set()
        for alias in field.aliases:
            alias_text = normalise_query(alias)
            if len(alias_text.replace(" ", "")) < 2:
                continue
            for variant in _phrase_variants(alias_text):
                if contains_phrase(normalised, variant):
                    aliases.add(alias.strip())
                    alias_phrases.add(variant)
                    recognised_phrases.add(variant)
                    break

        if field_values is not None:
            candidates = field_values.get(field.key, set())
        else:
            candidates = set()
            for document in documents or []:
                candidates.update(split_field_values(document.get("fields", {}).get(field.key)))

        values: set[str] = set()
        for candidate in candidates:
            value_text = str(candidate).strip()
            normalised_value = normalise_query(value_text)
            if not normalised_value:
                continue
            minimum_length = 1 if field.role == "skill" or field.data_type in {"integer", "number"} else 2
            if len(normalised_value.replace(" ", "")) >= minimum_length and contains_phrase(normalised, normalised_value):
                values.add(value_text)
                recognised_phrases.add(normalised_value)
                matched_value_phrases.add(normalised_value)

        if aliases:
            matched_aliases[field.key] = sorted(aliases)
        if values:
            matched_values[field.key] = sorted(values)
        combined = aliases | values
        if combined:
            matched_fields[field.key] = sorted(combined)

    lexical_query = _without_phrases(normalised, alias_phrases)
    recognised_tokens = set(" ".join(recognised_phrases).split())
    unmatched_tokens = sorted({
        token for token in normalised.split()
        if token not in _STOP_WORDS and token not in recognised_tokens
    })

    constraints: list[dict[str, Any]] = []
    for field in config.fields:
        values = matched_values.get(field.key, [])
        if not values:
            continue
        hard_filter = bool(field.filterable and field.query_filter_mode == "required")
        constraints.append({
            "field": field.key,
            "role": field.role,
            "values": values,
            "match": "exact_index_value",
            "query_match": field.query_match,
            "applied_as": "hard_filter" if hard_filter else "ranking_hint_only",
        })

    expanded_lexical_query, synonym_expansions = _expand_synonym_groups(
        lexical_query,
        config.synonyms,
        matched_value_phrases,
    )
    has_recognised = bool(matched_aliases or matched_values)
    hard_filter_count = sum(
        1 for constraint in constraints if constraint["applied_as"] == "hard_filter"
    )

    if hard_filter_count:
        explanation = (
            f"Recognised configured field values and applied {hard_filter_count} required "
            "query constraint(s) as filters. Other terms remain in lexical/semantic search."
        )
    elif has_recognised:
        explanation = (
            "Recognised configured field aliases and values. They guide ranking only; "
            "no automatic hard filters are configured for these fields."
        )
    else:
        explanation = (
            "No configured field aliases or exact indexed values were recognised; "
            "the complete query is used for general search."
        )

    return {
        "normalised_query": normalised,
        "query_type": "field_guided" if has_recognised else "free_text",
        "field_hints": matched_aliases,
        "matched_aliases": matched_aliases,
        "matched_values": matched_values,
        "matched_fields": matched_fields,
        "constraints": constraints,
        "lexical_query": lexical_query,
        "expanded_lexical_query": expanded_lexical_query,
        "synonym_expansions": synonym_expansions,
        "unmatched_tokens": unmatched_tokens,
        "explanation": explanation,
    }
