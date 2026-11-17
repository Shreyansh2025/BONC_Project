"""Reusable primitives for category-driven search engines.

Isolated from legacy Company/Product search helpers; adapted to preserve
technical tokens such as C, C++, and C# in generic categories.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Any, Callable

from spellchecker import SpellChecker

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[+#]+)?")
MIN_CORRECTABLE_LEN = 4
MIN_COMPOUND_PART_LEN = 3
MAX_CORRECTION_CACHE = 5000
CORRECTION_CUTOFF = 0.75
_ENGLISH_SPELLCHECKER = SpellChecker(language="en")


def platform_tokens(value: Any) -> list[str]:
    """Tokenize while retaining technical terms such as C++ and C#."""
    return _TOKEN_RE.findall(str(value or "").lower())


def normalise_platform_text(value: Any) -> str:
    """Return normalized category-search text."""
    return " ".join(platform_tokens(value))


def _token_forms(token: str) -> set[str]:
    """Return conservative singular/plural variants for an ordinary token."""
    forms = {token}
    if not token or "+" in token or "#" in token or token.isdigit():
        return forms
    if token.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")
    elif token.endswith("es") and len(token) > 4:
        forms.add(token[:-2])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        forms.add(token[:-1])
    if token.endswith("y") and len(token) > 2 and token[-2] not in "aeiou":
        forms.add(token[:-1] + "ies")
    elif token.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(token + "es")
    else:
        forms.add(token + "s")
    return forms


def contains_loose(term: str, target: Any) -> bool:
    """Match whole tokens/phrases, simple plurals, and joined compounds.

    This avoids substring errors such as 'pan' matching 'panel', while allowing
    'airconditioner' to match 'air conditioner'.
    """
    query_tokens = platform_tokens(term)
    target_tokens = platform_tokens(target)
    if not query_tokens or not target_tokens:
        return False
    if len(query_tokens) <= len(target_tokens):
        query_forms = [_token_forms(token) for token in query_tokens]
        for start in range(len(target_tokens) - len(query_tokens) + 1):
            if all(target_tokens[start + offset] in forms for offset, forms in enumerate(query_forms)):
                return True
    query_compact = "".join(query_tokens)
    compact_forms = _token_forms(query_compact)
    for start in range(len(target_tokens)):
        joined = ""
        for token in target_tokens[start:]:
            joined += token
            if joined in compact_forms:
                return True
            if len(joined) >= max(map(len, compact_forms)):
                break
    return False


def build_vocabulary(
    documents: list[dict[str, Any]],
    combined_text_fn: Callable[[dict[str, Any]], str],
) -> Counter:
    """Build category-local token frequencies for spelling correction."""
    vocabulary: Counter = Counter()
    for document in documents:
        vocabulary.update(platform_tokens(combined_text_fn(document)))
    return vocabulary


def build_inverted_index(
    documents: list[dict[str, Any]],
    combined_text_fn: Callable[[dict[str, Any]], str],
) -> dict[str, set[int]]:
    """Build a token-to-document-position index once per category index version."""
    inverted_index: dict[str, set[int]] = {}
    for position, document in enumerate(documents):
        for token in set(platform_tokens(combined_text_fn(document))):
            inverted_index.setdefault(token, set()).add(position)
    return inverted_index


def candidate_indices(term: str, inverted_index: dict[str, set[int]], total: int) -> set[int]:
    """Return a safe candidate superset; fall back when token narrowing is unsafe."""
    if not inverted_index or total <= 0:
        return set(range(total))
    query_tokens = platform_tokens(term)
    if not query_tokens:
        return set(range(total))
    candidates: set[int] = set()
    for token in query_tokens:
        for variant in _token_forms(token):
            candidates.update(inverted_index.get(variant, set()))
    return candidates if candidates else set(range(total))


def _is_compound_of_vocabulary(word: str, vocabulary: Counter) -> bool:
    """Protect joined forms made from two or more known category tokens."""
    size = len(word)
    if size < MIN_COMPOUND_PART_LEN * 2:
        return False
    reachable = [False] * (size + 1)
    reachable[0] = True
    for end in range(MIN_COMPOUND_PART_LEN, size + 1):
        for start in range(0, end - MIN_COMPOUND_PART_LEN + 1):
            if reachable[start] and word[start:end] in vocabulary:
                reachable[end] = True
                break
    return reachable[size]


def correct_word(word: str, vocabulary: Counter) -> str:
    """Correct likely typos toward this category's vocabulary, conservatively."""
    if (
        len(word) < MIN_CORRECTABLE_LEN or word.isdigit()
        or "+" in word or "#" in word or word in vocabulary
    ):
        return word
    cache = getattr(vocabulary, "_platform_correction_cache", None)
    if cache is None:
        cache = {}
        vocabulary._platform_correction_cache = cache
    elif word in cache:
        return cache[word]
    if _is_compound_of_vocabulary(word, vocabulary) or word in _ENGLISH_SPELLCHECKER:
        if len(cache) < MAX_CORRECTION_CACHE:
            cache[word] = word
        return word
    candidates = difflib.get_close_matches(
        word, vocabulary.keys(), n=5, cutoff=CORRECTION_CUTOFF
    )
    max_length_delta = 1 if len(word) <= 6 else 2
    candidates = [candidate for candidate in candidates if abs(len(candidate) - len(word)) <= max_length_delta]
    if candidates:
        corrected = max(
            candidates,
            key=lambda candidate: (
                round(difflib.SequenceMatcher(None, word, candidate).ratio(), 4),
                vocabulary[candidate],
            ),
        )
    else:
        corrected = word
    if len(cache) < MAX_CORRECTION_CACHE:
        cache[word] = corrected
    return corrected


def correct_query(query: str, vocabulary: Counter) -> str:
    """Spell-correct a normalized query using the current category vocabulary."""
    if not vocabulary or not query:
        return query
    return " ".join(correct_word(token, vocabulary) for token in query.split())


def shares_any_word(
    term: str, target: Any, *, stop_words: set[str] | None = None
) -> bool:
    """Check literal overlap for conservative semantic-candidate safeguards."""
    ignored = stop_words or set()
    query_tokens = {
        variant for token in platform_tokens(term) for variant in _token_forms(token)
        if variant not in ignored
    }
    target_tokens = {
        variant for token in platform_tokens(target) for variant in _token_forms(token)
        if variant not in ignored
    }
    return bool(query_tokens & target_tokens)
