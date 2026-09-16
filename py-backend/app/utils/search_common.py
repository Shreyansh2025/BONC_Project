"""
Shared helpers for the exact -> partial -> AI (FAISS) search pipeline used
by app/utils/b2b_search.py, app/utils/b2b_product_search.py and
app/utils/product_search.py.

Two problems these three files shared before this module existed:

1. No spelling correction. A misspelled query word (e.g. "chiar") fails
   the exact/partial layers outright and falls straight to the much
   fuzzier AI/FAISS layer instead of being corrected to "chair" and
   matched properly.

2. Exact/partial matching broke on whitespace. `"air conditioner" in
   "airconditioner deluxe"` is False (space mismatch), and so is
   `"airconditioner" in "air conditioner"`. So a one-word vs two-word
   spelling of the same term never matched at the precise layers, only
   inconsistently via the AI fallback.

Both are fixed here without adding any new dependency: `correct_query`
spell-corrects against a vocabulary built from the catalog's own text
(so it fixes typos toward real product/brand/category words, not
generic English), and `contains_loose` compares both the normal and the
whitespace-stripped form of each side.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Any, Callable

# Words shorter than this are left alone during correction — for very
# short words, almost every catalog word is within "close enough" edit
# distance, so correcting them causes more harm (wrong corrections) than
# the typos they'd occasionally fix.
MIN_CORRECTABLE_LEN = 4


def clean_term(raw: str) -> str:
    """Lowercase, strip anything that isn't a letter/digit/space. Same
    cleaning all three search files already did inline."""
    return re.sub(r"[^a-zA-Z0-9\s]", "", raw or "").lower().strip()


def compact(text: str) -> str:
    """Remove all whitespace. 'air conditioner' and 'airconditioner'
    both become 'airconditioner', so comparisons against this form treat
    the one-word and two-word spelling of a term as identical."""
    return re.sub(r"\s+", "", (text or "").lower())


def contains_loose(term: str, target: Any) -> bool:
    """True if `term` appears in `target`, regardless of whether either
    side splits the phrase with a space. Use this in place of a plain
    `term in target.lower()` substring check anywhere a query word or
    phrase is matched against a catalog field."""
    if not term or not target:
        return False
    target_text = str(target).lower()
    if term in target_text:
        return True
    return compact(term) in compact(target_text)


def build_vocabulary(
    docs: list[dict[str, Any]],
    combined_text_fn: Callable[[dict[str, Any]], str],
) -> Counter:
    """Word-frequency table built from every doc's searchable text (the
    same text each file already builds for FAISS embedding). Spell
    correction below matches query words against this vocabulary, so a
    typo is corrected toward a real catalog word (a brand, a product
    name, a city) rather than an unrelated generic-English word."""
    vocabulary: Counter = Counter()
    for doc in docs:
        words = re.findall(r"[a-z0-9]+", combined_text_fn(doc).lower())
        vocabulary.update(words)
    return vocabulary


def correct_word(word: str, vocabulary: Counter) -> str:
    """Spell-corrects one query word against the catalog vocabulary.
    Leaves it untouched if it's already a known word, too short to
    correct safely, or numeric."""
    if len(word) < MIN_CORRECTABLE_LEN or word.isdigit() or word in vocabulary:
        return word

    # Shorter words need a tighter cutoff, or too many unrelated catalog
    # words look "close enough".
    cutoff = 0.8 if len(word) <= 6 else 0.72
    candidates = difflib.get_close_matches(word, vocabulary.keys(), n=5, cutoff=cutoff)
    if not candidates:
        return word

    # Among the closest matches, prefer the one that's both common in the
    # catalog and closest in spelling — "chiar" -> "chair" (a real,
    # frequent product word) rather than some rarer coincidental match.
    best = max(
        candidates,
        key=lambda c: (vocabulary[c], difflib.SequenceMatcher(None, word, c).ratio()),
    )
    return best


def correct_query(term: str, vocabulary: Counter) -> str:
    """Word-by-word spelling correction of an already-cleaned search
    string. No-op if the vocabulary is empty (index not built yet)."""
    if not vocabulary or not term:
        return term
    return " ".join(correct_word(w, vocabulary) for w in term.split())


def get_matching_words(query: str, target_text: Any) -> str | None:
    if not target_text:
        return None
    query_words = set(re.findall(r"\w+", query.lower()))
    target_words = set(re.findall(r"\w+", str(target_text).lower()))
    common = query_words.intersection(target_words)
    return ", ".join(common).title() if common else None


def get_closest_word(query: str, target_text: Any) -> str:
    if not target_text:
        return "N/A"
    query_words = re.findall(r"\w+", query.lower())
    target_words = re.findall(r"\w+", str(target_text).lower())
    for qw in query_words:
        matches = difflib.get_close_matches(qw, target_words, n=1, cutoff=0.3)
        if matches:
            return matches[0].title()
    return " ".join(str(target_text).split()[:2]).title()
