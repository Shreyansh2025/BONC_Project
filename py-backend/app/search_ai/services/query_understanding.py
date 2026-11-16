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
    # Remove longer expressions first so a short alias cannot partially consume
    # a longer phrase that also has an independent meaning.
    for phrase in sorted(phrases, key=lambda item: (-len(item), item)):
        if not phrase:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
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

        # Prefer the most specific phrase when synonym groups overlap.
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

    expanded_query = " ".join([query, *additions]).strip()
    return expanded_query, expansions


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
    alias_phrases: set[str] = set()
    recognised_phrases: set[str] = set()
    matched_value_phrases: set[str] = set()

    for field in config.fields:
        if not field.searchable:
            continue

        aliases: set[str] = set()
        for alias in field.aliases:
            alias_text = normalise_query(alias)
            # Ignore one-character aliases: they are too likely to match noise.
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
                raw = document.get("fields", {}).get(field.key)
                if raw is not None:
                    candidates.update(
                        value.strip() for value in str(raw).split(",") if value.strip()
                    )

        values: set[str] = set()
        for value in candidates:
            value_text = str(value).strip()
            normalised_value = normalise_query(value_text)
            min_value_length = 1 if field.role == "skill" else 2
            if len(normalised_value.replace(" ", "")) >= min_value_length and contains_phrase(normalised, normalised_value):
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

    # Aliases describe the kind of information sought (e.g. "expertise" means
    # the skills field); they are not necessarily literal terms in a document.
    lexical_query = _without_phrases(normalised, alias_phrases)
    recognised_tokens = set(" ".join(recognised_phrases).split())
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

    expanded_lexical_query, synonym_expansions = _expand_synonym_groups(
        lexical_query,
        config.synonyms,
        matched_value_phrases,
    )

    return {
        "normalised_query": normalised,
        "query_type": "field_guided" if matched_aliases or matched_values else "free_text",
        "field_hints": matched_aliases,
        "matched_aliases": matched_aliases,
        "matched_values": matched_values,
        "matched_fields": matched_fields,
        "constraints": constraints,
        "lexical_query": lexical_query,
        "expanded_lexical_query": expanded_lexical_query,
        "synonym_expansions": synonym_expansions,
        "unmatched_tokens": unmatched_tokens,
        "explanation": (
            "Recognised field aliases and exact values from this engine's indexed data. "
            "They guide ranking; no hard filters were applied."
            if matched_aliases or matched_values
            else "No configured field aliases or exact indexed values were recognised; "
                 "the complete query is used for general search."
        ),
    }
