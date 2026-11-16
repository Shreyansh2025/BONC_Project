"""Deterministic query interpretation for configuration-driven search engines.

This module does not call an external LLM. It uses each engine's field aliases
and indexed field values to produce an explainable query plan.
"""
from __future__ import annotations

import re
from typing import Any

from app.search_ai.platform.models import CategoryConfig


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {"a", "an", "and", "at", "for", "in", "near", "of", "on", "the", "to", "with"}


def normalise_query(value: Any) -> str:
    """Return the token-normalised form used by the generic search pipeline."""
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def contains_phrase(normal_query: str, phrase: str) -> bool:
    """Match a whole phrase, not a substring inside another token."""
    phrase_text = normalise_query(phrase)
    if not normal_query or not phrase_text:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(phrase_text) + r"(?![a-z0-9])"
    return re.search(pattern, normal_query) is not None


def _without_phrases(query: str, phrases: set[str]) -> str:
    result = query
    # Remove longer expressions first so a short alias cannot partially consume
    # a longer phrase that also has an independent meaning.
    for phrase in sorted(phrases, key=lambda item: (-len(item), item)):
        if not phrase:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
        result = re.sub(pattern, " ", result)
    return " ".join(result.split())


def interpret_query(
    query: str,
    config: CategoryConfig,
    *,
    field_values: dict[str, set[str]] | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a transparent query plan from configured aliases and known field values.

    Exact values and field aliases are hints, not hard filters. This intentionally
    avoids dropping a potentially relevant result when a query phrase is ambiguous
    or when a source has incomplete metadata.
    """
    normalised = normalise_query(query)
    matched_aliases: dict[str, list[str]] = {}
    matched_values: dict[str, list[str]] = {}
    matched_fields: dict[str, list[str]] = {}
    removed_phrases: set[str] = set()

    for field in config.fields:
        if not field.searchable:
            continue

        aliases: set[str] = set()
        for alias in field.aliases:
            alias_text = normalise_query(alias)
            # Ignore one-character aliases: they are too likely to match noise.
            if len(alias_text.replace(" ", "")) >= 2 and contains_phrase(normalised, alias_text):
                aliases.add(alias.strip())
                removed_phrases.add(alias_text)

        if field_values is not None:
            candidates = field_values.get(field.key, set())
        else:
            candidates = set()
            for document in documents or []:
                raw = document.get("fields", {}).get(field.key)
                if raw is not None:
                    candidates.update(
                        value.strip() for value in str(raw).split(",") if value.strip()
                    )

        values: set[str] = set()
        for value in candidates:
            value_text = str(value).strip()
            normalised_value = normalise_query(value_text)
            if len(normalised_value.replace(" ", "")) >= 2 and contains_phrase(normalised, normalised_value):
                values.add(value_text)
                removed_phrases.add(normalised_value)

        if aliases:
            matched_aliases[field.key] = sorted(aliases)
        if values:
            matched_values[field.key] = sorted(values)
        combined = aliases | values
        if combined:
            matched_fields[field.key] = sorted(combined)

    # Aliases describe the kind of information sought (e.g. "expertise" means
    # the skills field); they are not necessarily literal terms in a document.
    lexical_query = _without_phrases(normalised, removed_phrases)
    recognised_tokens = set(" ".join(removed_phrases).split())
    unmatched_tokens = sorted({
        token for token in normalised.split()
        if token not in _STOP_WORDS and token not in recognised_tokens
    })

    constraints = []
    for field in config.fields:
        values = matched_values.get(field.key, [])
        if values:
            constraints.append({
                "field": field.key,
                "role": field.role,
                "values": values,
                "match": "exact_index_value",
                "applied_as": "ranking_hint_only",
            })

    return {
        "normalised_query": normalised,
        "query_type": "field_guided" if matched_aliases or matched_values else "free_text",
        "field_hints": matched_aliases,
        "matched_aliases": matched_aliases,
        "matched_values": matched_values,
        "matched_fields": matched_fields,
        "constraints": constraints,
        "lexical_query": lexical_query,
        "unmatched_tokens": unmatched_tokens,
        "explanation": (
            "Recognised field aliases and exact values from this engine's indexed data. "
            "They guide ranking; no hard filters were applied."
            if matched_aliases or matched_values
            else "No configured field aliases or exact indexed values were recognised; "
                 "the complete query is used for general search."
        ),
    }
