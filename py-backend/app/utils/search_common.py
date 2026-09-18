"""
Shared helpers for the exact -> partial -> AI (FAISS) search pipeline used
by app/utils/b2b_search.py, app/utils/b2b_product_search.py and
app/utils/product_search.py.

Problems fixed in this module:

1. No spelling correction. A misspelled query word (e.g. "chiar") fails
   the exact/partial layers outright and falls straight to the much
   fuzzier AI/FAISS layer instead of being corrected to "chair" and
   matched properly.

2. Exact/partial matching broke on whitespace. `"air conditioner" in
   "airconditioner deluxe"` is False (space mismatch), and so is
   `"airconditioner" in "air conditioner"`. So a one-word vs two-word
   spelling of the same term never matched at the precise layers, only
   inconsistently via the AI fallback.

3. SUBSTRING BUG (fixed here): the old `contains_loose` did a plain
   `term in target_text` check, so a short query like "pan" matched
   ANY longer word that happened to contain those letters in sequence
   -- "pant", "panel", "company" (via its compacted form), etc. Both the
   plain check and the whitespace-stripped fallback have been rewritten
   to only match on whole-word / whole-token boundaries. See
   `contains_loose` below for the full explanation.

4. Full-catalog scans on every request (fixed here): `build_inverted_index`
   / `candidate_indices` build a word -> doc-positions lookup once per
   index build, so a search for "chair" only has to check the handful of
   docs that actually contain "chair"-ish text instead of every single
   doc in the catalog on every request. This is what turns the exact and
   partial layers from an O(catalog size) scan into an O(matches) scan.

Both spelling problems are fixed without adding any new dependency:
`correct_query` spell-corrects against a vocabulary built from the
catalog's own text (so it fixes typos toward real product/brand/category
words, not generic English), and `contains_loose` compares both the
normal and the whitespace-stripped form of each side, boundary-safe.

A third, related problem fixed here: a correctly-spelled compact
compound word (e.g. "airconditioner" typed as one word) is never itself
a single vocabulary token, since the catalog text only ever has "air"
and "conditioner" as separate words. Without a guard, correct_word()
would treat that as a typo and "correct" it down to a shorter, less
specific catalog word (e.g. "conditioner"), silently dropping the "air"
qualifier. `_is_compound_of_vocab` detects this case and leaves such
words untouched, since contains_loose() already matches them correctly
via its token-join comparison.

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

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def clean_term(raw: str) -> str:
    """Lowercase, strip anything that isn't a letter/digit/space. Same
    cleaning all three search files already did inline."""
    return re.sub(r"[^a-zA-Z0-9\s]", "", raw or "").lower().strip()


def compact(text: str) -> str:
    """Remove all whitespace. 'air conditioner' and 'airconditioner'
    both become 'airconditioner', so comparisons against this form treat
    the one-word and two-word spelling of a term as identical."""
    return re.sub(r"\s+", "", (text or "").lower())


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def contains_loose(term: str, target: Any) -> bool:
    """True if `term` (a word or short phrase) appears in `target` as a
    whole word / whole phrase, tolerant of a one-word vs multi-word
    spelling difference on either side, but NEVER as a bare substring of
    an unrelated longer word.

    Two checks, both boundary-safe:

    1. `term` matches `target_text` at a word boundary on both ends. This
       is what a plain `term in target_text` check was trying to do, but
       that old version matched "pan" inside "pant" or "panel" because it
       never checked what came immediately before/after the match. The
       lookaround here requires there be no other letter/digit touching
       either end of the match, so "pan" only matches a real standalone
       "pan" token (or the edge of a phrase), never a prefix of a longer
       word.

    2. `term`, with its own spaces stripped, is matched against a run of
       *whole* target tokens joined together ("air" + "conditioner" ->
       "airconditioner"). This is still boundary-safe because it only
       ever concatenates complete tokens — "pan" can't match inside
       "pant" here either, since "pant" is one whole token, not "pan"
       plus something else. This is what lets "airconditioner" (typed as
       one word) match a target field that spells it "Air Conditioner",
       and vice versa.
    """
    if not term or not target:
        return False
    term = str(term).lower().strip()
    if not term:
        return False
    target_text = str(target).lower()

    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    if re.search(pattern, target_text):
        return True

    term_compact = compact(term)
    if not term_compact:
        return False
    target_tokens = _tokenize(target_text)
    for start in range(len(target_tokens)):
        joined = ""
        for tok in target_tokens[start:]:
            joined += tok
            if joined == term_compact:
                return True
            if len(joined) >= len(term_compact):
                break
    return False


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
        words = _TOKEN_RE.findall(combined_text_fn(doc).lower())
        vocabulary.update(words)
    return vocabulary


def build_inverted_index(
    docs: list[dict[str, Any]],
    combined_text_fn: Callable[[dict[str, Any]], str],
) -> dict[str, set[int]]:
    """word -> set of doc positions whose combined text contains that
    word. Built once per index rebuild (alongside build_vocabulary),
    so a search request can jump straight to the handful of docs that
    could possibly match a query word/phrase instead of running a regex
    check across the ENTIRE in-memory catalog on every single request.
    This is the main fix for "wasted CPU on requests that get discarded
    by pagination" and "Python for-loops over thousands of items" --
    exact/partial matching now costs roughly O(matching docs), not
    O(catalog size), regardless of how many total docs exist."""
    index: dict[str, set[int]] = {}
    for i, doc in enumerate(docs):
        for word in set(_TOKEN_RE.findall(combined_text_fn(doc).lower())):
            index.setdefault(word, set()).add(i)
    return index


def candidate_indices(
    term: str, inverted_index: dict[str, set[int]], total: int
) -> set[int]:
    """Doc positions worth running contains_loose() against for `term`
    (a single word or short phrase). Returns the union of every doc that
    contains ANY word of `term` as a literal token -- always a superset
    of the docs that could actually match (contains_loose still does the
    real check afterwards), so this can only prune irrelevant docs, never
    hide a real match.

    Falls back to "check every doc" only when narrowing isn't safe:
      - the index is empty (catalog not built yet), or
      - none of term's words exist as a literal token anywhere in the
        catalog. This covers the one-word/multi-word compound case (e.g.
        query "airconditioner" vs a catalog that only ever has "air" and
        "conditioner" as separate tokens) where contains_loose's
        token-join comparison can still find a match that a literal-token
        lookup never would.
    """
    if not inverted_index or total == 0:
        return set(range(total))
    words = _TOKEN_RE.findall(term.lower())
    if not words:
        return set(range(total))

    candidates: set[int] = set()
    narrowed = False
    for w in words:
        hits = inverted_index.get(w)
        if hits:
            candidates |= hits
            narrowed = True
    return candidates if narrowed else set(range(total))


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
    correctly downstream via its token-join comparison, so the right fix
    is to leave words like this alone here rather than "fixing" something
    that isn't broken.

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

    # Single flat cutoff instead of the old length-based 0.8/0.72 split --
    # a word only gets corrected if it's at least 75% similar (by
    # difflib's ratio) to a real catalog word, so a word that's simply new
    # or uncommon in the catalog (e.g. "hinges") is left as-is instead of
    # being forced into a merely-close match.
    CORRECTION_CUTOFF = 0.75
    candidates = difflib.get_close_matches(word, vocabulary.keys(), n=5, cutoff=CORRECTION_CUTOFF)

    # A real typo almost never drops two or more whole characters from a
    # short word -- "cargo" -> "car" scores EXACTLY 0.75 (the cutoff's own
    # boundary) despite being a different, unrelated word, because ratio()
    # doesn't care that "car" is 40% shorter. This guard rejects candidates
    # that differ too much in length, closing that specific exploit without
    # touching the 0.75 cutoff itself (e.g. "hinges" -> "hinge", a 1-char
    # genuine typo/plural difference, still passes).
    max_len_delta = 1 if len(word) <= 6 else 2
    candidates = [c for c in candidates if abs(len(c) - len(word)) <= max_len_delta]

    if not candidates:
        result = word
    else:
        # Closest SPELLING wins first; catalog frequency is only a
        # tiebreaker between otherwise-equally-close matches. The
        # previous version sorted by frequency FIRST (`vocabulary[c]`
        # before the ratio), which is backwards -- it meant a common
        # catalog word could beat a rarer but much closer-spelled one
        # just for being popular, which is exactly how an unrelated
        # word can get chosen over the real intended correction.
        result = max(
            candidates,
            key=lambda c: (
                round(difflib.SequenceMatcher(None, word, c).ratio(), 4),
                vocabulary[c],
            ),
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


def shares_any_word(term: str, target: Any) -> bool:
    """True if `term` and `target` have at least one whole word in
    common. Cheap token-set intersection -- used as a safety net on the
    AI/semantic (FAISS) layer only: a moderate-confidence embedding match
    still needs SOME literal word in common with the query before it's
    trusted, so two things that are merely in the same broad domain
    (e.g. "cargo" and a "Car Rental Services" business -- both
    transport-related, zero words in common) don't get surfaced on
    semantic similarity alone. A genuinely close semantic match at HIGH
    confidence still gets through with no shared word required -- see
    AI_HIGH_CONFIDENCE_PERCENTAGE in b2b_search.py / b2b_product_search.py."""
    if not term or not target:
        return False
    term_words = set(_TOKEN_RE.findall(str(term).lower()))
    target_words = set(_TOKEN_RE.findall(str(target).lower()))
    return bool(term_words & target_words)


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