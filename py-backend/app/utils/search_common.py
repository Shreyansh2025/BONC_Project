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

A third, related problem fixed here: a correctly-spelled compact
compound word (e.g. "airconditioner" typed as one word) is never itself
a single vocabulary token, since the catalog text only ever has "air"
and "conditioner" as separate words. Without a guard, correct_word()
would treat that as a typo and "correct" it down to a shorter, less
specific catalog word (e.g. "conditioner"), silently dropping the "air"
qualifier. `_is_compound_of_vocab` detects this case and leaves such
words untouched, since contains_loose() already matches them correctly
via its compacted-string comparison.

Each vocabulary build also gets its own bounded word-correction cache
(attached to the Counter instance itself, so it's automatically scoped
to that vocabulary version and needs no manual invalidation) — the same
query words repeat heavily across real traffic between hourly rebuilds,
and without caching every uncached word pays a full linear scan over the
whole vocabulary via difflib on every single search request.
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

# When checking whether a word is really a compound of two+ known catalog
# words stuck together (see _is_compound_of_vocab), each piece must be at
# least this long. Keeps "isa" + "las" style false positives out of a word
# like "islas" from matching on accidental short fragments.
MIN_COMPOUND_PART_LEN = 3

# Per-vocabulary correction cache is capped so a burst of many distinct
# junk query words (bots, fuzzing, accidental spam) can't grow it without
# bound in memory for the lifetime of one vocabulary build (an hour, by
# default). Once full, new words simply stop being cached — correction
# still works, it just re-scans the vocabulary for the overflow, same as
# if caching didn't exist.
MAX_CORRECTION_CACHE = 5000


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


def _is_compound_of_vocab(word: str, vocabulary: Counter) -> bool:
    """True if `word` can be split end-to-end into two or more real
    catalog words, e.g. "airconditioner" -> "air" + "conditioner", or
    "waterheater" -> "water" + "heater".

    A compact one-word spelling of a two-word catalog term is, by
    definition, never itself a single vocabulary token (the catalog text
    only ever contains "air" and "conditioner" as separate words), so
    without this guard correct_word() would always fall through to the
    fuzzy-match branch below. That branch can then "correct" a perfectly
    valid compound spelling down to a shorter, less specific single word
    (observed in testing: "airconditioner" -> "conditioner",
    "airfryer" -> "fryer") — silently dropping a meaningful qualifier and
    broadening the search. contains_loose() already matches this spelling
    correctly downstream via compacted-string comparison, so the right
    fix is to leave words like this alone here rather than "fixing"
    something that isn't broken.

    Simple word-break-style check, capped to short catalog-word lengths
    for speed; query words are short (a handful of characters) so this is
    effectively instant."""
    n = len(word)
    if n < MIN_COMPOUND_PART_LEN * 2:
        return False

    reachable = [False] * (n + 1)
    reachable[0] = True
    for end in range(MIN_COMPOUND_PART_LEN, n + 1):
        for start in range(0, end - MIN_COMPOUND_PART_LEN + 1):
            if reachable[start] and word[start:end] in vocabulary:
                reachable[end] = True
                break
    return reachable[n]


def correct_word(word: str, vocabulary: Counter) -> str:
    """Spell-corrects one query word against the catalog vocabulary.
    Leaves it untouched if it's already a known word, too short to
    correct safely, numeric, or a compound of known catalog words stuck
    together (see _is_compound_of_vocab)."""
    if len(word) < MIN_CORRECTABLE_LEN or word.isdigit() or word in vocabulary:
        return word

    # Per-vocabulary correction cache, attached directly to this
    # vocabulary Counter instance. A fresh Counter is created on every
    # build_index() call, so the cache's lifetime is automatically scoped
    # to one vocabulary version — no manual invalidation needed, and it
    # can never serve a stale correction from a previous catalog build.
    cache = getattr(vocabulary, "_correction_cache", None)
    if cache is None:
        cache = {}
        vocabulary._correction_cache = cache
    elif word in cache:
        return cache[word]

    if _is_compound_of_vocab(word, vocabulary):
        if len(cache) < MAX_CORRECTION_CACHE:
            cache[word] = word
        return word

    # Shorter words need a tighter cutoff, or too many unrelated catalog
    # words look "close enough".
    cutoff = 0.8 if len(word) <= 6 else 0.72
    candidates = difflib.get_close_matches(word, vocabulary.keys(), n=5, cutoff=cutoff)
    if not candidates:
        result = word
    else:
        # Among the closest matches, prefer the one that's both common in
        # the catalog and closest in spelling — "chiar" -> "chair" (a
        # real, frequent product word) rather than some rarer
        # coincidental match.
        result = max(
            candidates,
            key=lambda c: (vocabulary[c], difflib.SequenceMatcher(None, word, c).ratio()),
        )

    if len(cache) < MAX_CORRECTION_CACHE:
        cache[word] = result
    return result


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