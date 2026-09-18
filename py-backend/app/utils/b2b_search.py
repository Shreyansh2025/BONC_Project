"""
B2B company search: exact match -> priority partial-word match -> AI
(FAISS) fallback, blended into one hybrid-ranked result set.

Reads directly from the company's own live `Company` table, joined with:
  - Industry / Category / SubCategory  -> human-readable names for
    Company.IndustryId / CategoryId / SubCategoryId (bare GUIDs otherwise).
  - AddressDetails                     -> City/State/Country/Pincode/
    Latitude/Longitude. Confirmed: EntityType='Company', EntityTypeId=
    Company.BusinessId, and confirmed no business has more than one
    address row, so this is a plain one-to-one join, no "pick one" logic
    needed (unlike the Media join in b2b_product_search.py).
  - BusinessType                       -> Company.BusinessTypeId's real
    name (e.g. "Manufacturer"/"Distributor" — matches "IndustryType" in
    the company's own /api/commonService search response).
  - BoncUser                           -> the account that owns the
    listing, via Company.UserRowId, for UserSlug.

This closes the location gap that existed since the start of this
migration (Company itself has no address columns — the data was always
in AddressDetails, a separate table, not missing entirely) plus two
fields (IndustryType, UserSlug) seen in the company's own search API
response that weren't available here before.

Caching / concurrency strategy: the company set is small enough to keep
in memory. Everything the search functions touch below (companies list,
FAISS index, vocabulary, inverted index) lives in ONE `_CompanyState`
object behind a single module-level reference, swapped atomically in
build_index(). This matters because build_index() runs every hour (see
main.py) while search requests keep flowing on other threads: swapping
four separate globals one at a time (as the previous version did) lets a
request in flight read, say, the NEW companies list but the OLD FAISS
index (built for the old list's length/order), which can silently
mismatch rows or throw an IndexError. Capturing `_state` once at the top
of each search function means a request always sees one fully-consistent
snapshot, whichever build it happens to land on.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import faiss
import numpy as np
from sqlalchemy import text

from app.db import get_engine
from app.logger import logger
from app.utils.embedding_model import EMBEDDING_DIM, get_model
from app.utils.search_common import (
    build_inverted_index,
    build_vocabulary,
    candidate_indices,
    clean_term,
    contains_loose,
    correct_query,
    get_closest_word,
    get_matching_words,
    shares_any_word,
    FILLER_WORDS,
    BUSINESS_TYPE_SYNONYMS,
    matches_business_type,
)

STOP_WORDS = {
    "in", "at", "near", "and", "for", "the", "of", "to",
}

# Once the exact + partial (lexical) layers already turned up at least
# this many results, skip the FAISS/AI layer entirely for this request --
# there's already ample coverage and the extra embedding + similarity
# search would only add latency for little relevance benefit. Below this
# count (including the common "0 lexical results" case that used to be
# the ONLY time AI ran), AI results are computed and MERGED in alongside
# the lexical ones instead of being skipped outright. This is the fix for
# "Early Return Relevance Killer": previously ANY exact or partial hit,
# even a single weak one, fully prevented the AI layer from ever running.
MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI = 20

# AI/FAISS layer confidence bands (company search). Below
# AI_MIN_PERCENTAGE, a result is dropped outright -- unrelated. Between
# AI_MIN_PERCENTAGE and AI_HIGH_CONFIDENCE_PERCENTAGE, the embedding
# thinks it's "somewhat related" but that band is exactly where two
# things in the same broad domain (e.g. "cargo" and a "Car Rental
# Services" business -- both transport-related, no word in common) can
# score highly enough to slip through on semantics alone. So in that
# middle band, a result also needs to share at least one literal word
# with the query (see shares_any_word in search_common.py) to be kept.
# At or above AI_HIGH_CONFIDENCE_PERCENTAGE the match is trusted on
# semantics alone, no shared word required -- this is what still lets a
# genuine typo or synonym ("chiar" already gets spell-corrected, but a
# true synonym the vocabulary doesn't know) through.
#
# There's no labeled test data behind these two exact numbers yet --
# they're a conservative starting split, not a tuned threshold. If real
# examples of good/bad AI matches turn up, these are the two numbers to
# adjust first.
AI_MIN_PERCENTAGE = 35.0
AI_HIGH_CONFIDENCE_PERCENTAGE = 60.0


@dataclass(frozen=True)
class _CompanyState:
    # Ordered list of company docs — position i mirrors row i of `index`.
    companies: list[dict[str, Any]] = field(default_factory=list)
    index: faiss.Index | None = None
    # Word-frequency table built from companies' own text, used to spell-
    # correct query words toward real catalog words (see search_common.py).
    vocabulary: Counter = field(default_factory=Counter)
    # word -> set of doc positions containing that word (see search_common.py).
    inverted_index: dict[str, set[int]] = field(default_factory=dict)


_state = _CompanyState()


_COMPANY_SQL = text(
    """
    SELECT
        c.BusinessId,
        c.BusinessName,
        c.BusinessSlug,
        c.CategoryId,
        c.IndustryId,
        c.SubCategoryId,
        c.BusinessDescription,
        c.Tagline,
        c.AboutBrief,
        c.Description,
        c.Vision,
        c.WhyChooseUs,
        c.WebsiteURL,
        c.CompanyLogo,
        c.Banner,
        c.CompanyUniqueId,
        c.IsAllowRfqRfiFromBusiness,
        i.IndustryName,
        cat.CategoryName,
        sub.SubCategoryName,
        bt.BusinessTypeName,
        addr.City,
        addr.State,
        addr.Country,
        addr.Pincode,
        addr.Latitude,
        addr.Longitude,
        u.Slug AS UserSlug
    FROM Company c
    LEFT JOIN Industry i
        ON i.IndustryId = c.IndustryId AND i.IsActive = 1 AND ISNULL(i.IsDeleted, 0) = 0
    LEFT JOIN Category cat
        ON cat.CategoryId = c.CategoryId AND cat.IsActive = 1 AND ISNULL(cat.IsDeleted, 0) = 0
    LEFT JOIN SubCategory sub
        ON sub.SubCategoryId = c.SubCategoryId AND sub.IsActive = 1 AND ISNULL(sub.IsDeleted, 0) = 0
    LEFT JOIN BusinessType bt
        ON bt.BusinessTypeId = c.BusinessTypeId
    LEFT JOIN AddressDetails addr
        ON addr.EntityTypeId = c.BusinessId AND addr.EntityType = 'Company'
    LEFT JOIN BoncUser u
        ON u.UserRowId = c.UserRowId AND ISNULL(u.IsDeleted, 0) = 0
    WHERE ISNULL(c.IsDeleted, 0) = 0 AND c.Status = 'Verified'
    """
)


def _s(value: Any) -> str | None:
    """SQL Server `uniqueidentifier` columns come back as uuid.UUID objects,
    not strings — this stringifies them (and passes through None) so every
    id field is JSON-safe without special-casing it at every call site."""
    return str(value) if value is not None else None


def index_status() -> dict[str, Any]:
    """Snapshot of in-memory index state, for the /api/debug/index-status route."""
    state = _state
    return {"count": len(state.companies), "index_built": state.index is not None}


def _combined_text(doc: dict[str, Any]) -> str:
    # City/state are back in the searchable text now that AddressDetails
    # is joined — this was previously a known, documented gap (Company
    # itself has no address columns; the data lives in AddressDetails,
    # found later). businessTypeName added too, matching the "IndustryType"
    # field seen in the company's own live search response.
    parts = [
        doc.get("businessName", ""),
        doc.get("businessSlug", ""),
        doc.get("industryName", ""),
        doc.get("categoryName", ""),
        doc.get("subCategoryName", ""),
        doc.get("businessTypeName", ""),
        doc.get("city", ""),
        doc.get("state", ""),
        doc.get("businessDescription", ""),
        doc.get("tagline", ""),
        doc.get("aboutBrief", ""),
        doc.get("description", ""),
        doc.get("vision", ""),
        doc.get("whyChooseUs", ""),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_companies_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(_COMPANY_SQL).mappings().all()

    docs: list[dict[str, Any]] = []
    for r in rows:
        docs.append(
            {
                "businessId": _s(r["BusinessId"]),
                "businessName": r["BusinessName"],
                "businessSlug": r["BusinessSlug"],
                "categoryId": _s(r["CategoryId"]),
                "industryId": _s(r["IndustryId"]),
                "subCategoryId": _s(r["SubCategoryId"]),
                "categoryName": r["CategoryName"],
                "industryName": r["IndustryName"],
                "subCategoryName": r["SubCategoryName"],
                "businessTypeName": r["BusinessTypeName"],
                "businessDescription": r["BusinessDescription"],
                "tagline": r["Tagline"],
                "aboutBrief": r["AboutBrief"],
                "description": r["Description"],
                "vision": r["Vision"],
                "whyChooseUs": r["WhyChooseUs"],
                "websiteUrl": r["WebsiteURL"],
                "companyUniqueId": r["CompanyUniqueId"],
                "isAllowRfqRfiFromBusiness": r["IsAllowRfqRfiFromBusiness"],
                "userSlug": r["UserSlug"],
                # Company has CompanyLogo/Banner directly on the row — no
                # Media table join needed here (unlike products).
                "imagePath": r["CompanyLogo"] or r["Banner"],
                # AddressDetails join — city/state real now, lat/long kept
                # as strings (source columns are nvarchar, not decimal).
                "city": r["City"],
                "state": r["State"],
                "country": r["Country"],
                "pincode": r["Pincode"],
                "latitude": r["Latitude"],
                "longitude": r["Longitude"],
            }
        )
    return docs


def _build_index_sync(docs: list[dict[str, Any]]):
    """CPU-bound: sentence-transformer encoding + FAISS index build.
    Runs on a worker thread via asyncio.to_thread — must not be awaited
    directly, or it blocks the event loop for the whole encode+build time."""
    model = get_model()
    texts = [_combined_text(d) for d in docs]
    vectors = model.encode(texts)
    faiss.normalize_L2(vectors)

    index = faiss.IndexFlatL2(EMBEDDING_DIM)
    index.add(np.array(vectors).astype("float32"))
    return index


async def build_index() -> None:
    """Loads all Verified companies from the live Company table and
    (re)builds the in-memory FAISS index + inverted index. Called once at
    startup and then every hour by main.py's auto-refresh loop — each call
    does a full read of Company + Industry + Category + SubCategory +
    BusinessType + AddressDetails + BoncUser, so keep that in mind if
    refresh frequency ever needs to change for load reasons."""
    global _state

    docs = await asyncio.to_thread(_load_companies_sync)
    if not docs:
        logger.info("No Verified companies found — search index left empty")
        _state = _CompanyState()
        return

    index = await asyncio.to_thread(_build_index_sync, docs)
    vocabulary = build_vocabulary(docs, _combined_text)
    inverted = build_inverted_index(docs, _combined_text)

    # Single atomic pointer swap — any search already in flight keeps using
    # the previous (still fully self-consistent) _CompanyState until it
    # finishes; the next call to a search function picks up this one.
    _state = _CompanyState(
        companies=docs, index=index, vocabulary=vocabulary, inverted_index=inverted
    )
    logger.info(f"B2B search index built with {len(docs)} companies")


def _contains_term(doc: dict[str, Any], term: str, fields: list[str]) -> bool:
    return any(contains_loose(term, doc.get(f, "")) for f in fields)


def search_companies_sync(query: str) -> list[dict[str, Any]]:
    """Runs the exact -> partial -> AI-fallback hybrid search over the
    in-memory company cache. CPU-bound (regex + FAISS + embedding) — call
    this via asyncio.to_thread from the route so it doesn't block the
    event loop."""
    state = _state  # one consistent snapshot for the whole call
    search_term = clean_term(query)
    if not search_term or not state.companies:
        return []

    # Spell-correct against the catalog's own vocabulary before matching,
    # so a typo like "chiar" is corrected to "chair" instead of skipping
    # straight to the much fuzzier AI fallback. Only touches words that
    # aren't already a real catalog word, so this never changes a query
    # that already matches something.
    search_term = correct_query(search_term, state.vocabulary)

    # Layer 1: exact match (full phrase) against name / slug / industry /
    # category / city. City is back in this list now that AddressDetails
    # is joined — was removed here earlier when Company alone had no
    # address columns to check. contains_loose also matches across a
    # one-word/two-word spelling difference on either side, and is now
    # boundary-safe (a query for "pan" can no longer match "pant"/"panel").
    exact_fields = ["businessName", "businessSlug", "industryName", "categoryName", "city"]
    exact_results = []
    # Dedup by businessId, not by name -- two different companies that
    # happen to share a display name are still two different sellers and
    # must both be able to appear. The old seen_names (name-string) set
    # meant the second one silently vanished from the results.
    seen_ids: set[str] = set()
    candidates = candidate_indices(search_term, state.inverted_index, len(state.companies))
    for i in candidates:
        doc = state.companies[i]
        if _contains_term(doc, search_term, exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                get_matching_words(search_term, row.get("businessName", ""))
                or get_matching_words(search_term, row.get("categoryName", ""))
                or get_matching_words(search_term, row.get("industryName", ""))
                or get_matching_words(search_term, row.get("city", ""))
            )
            row["matchedKeyword"] = matched or search_term.title()
            exact_results.append(row)
            seen_ids.add(str(doc.get("businessId", "")))

    # Layer 1.5: partial word match, weighted by which field it hit and
    # boosted for whichever query word appears first. Words of length 2
    # are now kept (only true stop words like "in"/"at"/"to" are dropped)
    # -- the old `len(w) > 2` cutoff silently dropped real short product/
    # business terms like "AC" or "TV" from ever contributing a partial
    # match.
    words = [
        w for w in search_term.split()
        if len(w) >= 2 and w not in STOP_WORDS and w not in FILLER_WORDS
    ]
    partial_results: list[dict[str, Any]] = []

    if words:
        # Union of every word's candidate doc positions -- a doc gets
        # scored against EVERY query word, not just whichever word's loop
        # happens to reach it first.
        all_candidates: set[int] = set()
        for word in words:
            all_candidates |= candidate_indices(word, state.inverted_index, len(state.companies))

        for i in all_candidates:
            doc = state.companies[i]
            b_id = str(doc.get("businessId", ""))
            if b_id in seen_ids:
                continue

            b_name = str(doc.get("businessName", "")).lower()
            city_text = str(doc.get("city", "")).lower()
            state_text = str(doc.get("state", "")).lower()
            country_text = str(doc.get("country", "")).lower()
            cat_text = str(doc.get("categoryName", "")).lower()
            industry_text = str(doc.get("industryName", "")).lower()
            business_type_text = str(doc.get("businessTypeName", "")).lower()
            desc_text = str(doc.get("description", "")).lower()

            matched_words = []
            word_scores = []
            core_matched = False

            for word in words:
                score = 0.0
                
                # Check if this word is just acting as a geographic or business-type filter.
                # If a word is purely a location or a role (like "supplier"), it shouldn't
                # satisfy the mandatory core product requirement, EVEN IF it happens to
                # appear in the company's name (e.g., "Global Wood India").
                is_location = (
                    contains_loose(word, city_text) or 
                    contains_loose(word, state_text) or 
                    contains_loose(word, country_text)
                )
                is_biz_type = word in BUSINESS_TYPE_SYNONYMS
                can_satisfy_core = not is_location and not is_biz_type

                if contains_loose(word, b_name):
                    score = 90.0
                    if can_satisfy_core:
                        core_matched = True
                elif contains_loose(word, cat_text) or contains_loose(word, industry_text):
                    score = 85.0
                    if can_satisfy_core:
                        core_matched = True
                elif is_location:
                    if contains_loose(word, city_text):
                        score = 82.0
                    elif contains_loose(word, state_text):
                        score = 80.0
                    else:
                        score = 75.0
                elif is_biz_type:
                    # Only score if it actually matches THIS doc's real business type
                    if matches_business_type(word, business_type_text):
                        score = 80.0
                elif contains_loose(word, desc_text):
                    score = 70.0
                    
                word_scores.append(score)
                if score > 0:
                    matched_words.append(word)

            # Mandatory: at least one real product/category word must match.
            if not core_matched:
                continue
            # Majority: most of the remaining (non-filler) words must match too.
            if len(matched_words) < (len(words) // 2 + 1):
                continue

            combined_score = sum(word_scores) / len(words)
            if combined_score >= 10.0:
                seen_ids.add(b_id)
                row = dict(doc)
                row["matchType"] = "Partial Word Match"
                row["matchPercentage"] = round(combined_score, 2)
                row["matchedKeyword"] = ", ".join(w.title() for w in matched_words)
                partial_results.append(row)

    lexical_results = exact_results + partial_results

    # Layer 2: AI fallback via FAISS similarity over the embedded company
    # text. Previously this only ever ran when BOTH lexical layers came up
    # completely empty, which meant a single weak exact/partial hit could
    # hide much better semantic matches from the results. Now it runs
    # whenever lexical coverage is thin (below
    # MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI), and its results are MERGED
    # into the same ranked list instead of replacing or being blocked by
    # the lexical ones -- true hybrid scoring.
    # Layer 2: AI fallback via FAISS similarity over the embedded company text.
    ai_results: list[dict[str, Any]] = []
    
    # Filter down to true "core" words (no stop words, filler words, or business types)
    # so we don't let generic words like "suppliers" or "manufacturers" artificially 
    # validate a weak AI match.
    core_query_words = [w for w in words if w not in BUSINESS_TYPE_SYNONYMS]
    core_query_str = " ".join(core_query_words)

    if len(lexical_results) < MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI and state.index is not None:
        model = get_model()
        query_vector = model.encode([search_term])
        faiss.normalize_L2(query_vector)
        distances, indices = state.index.search(query_vector.astype("float32"), k=50)

        for i, idx in enumerate(indices[0]):
            if idx == -1 or idx >= len(state.companies):
                continue
            distance_score = distances[0][i]
            cosine_sim = 1 - (distance_score / 2)
            percentage = max(0.0, round(cosine_sim * 100, 2))
            if percentage < AI_MIN_PERCENTAGE:
                continue

            doc = state.companies[int(idx)]
            b_id = str(doc.get("businessId", ""))
            if b_id in seen_ids:
                continue

            # Middle-confidence band safety net: require a shared *core* word.
            if percentage < AI_HIGH_CONFIDENCE_PERCENTAGE:
                doc_text = _combined_text(doc)
                doc_tokens = set(re.findall(r"\w+", doc_text))
                
                # Grab all the words that make up this company's location
                geo_tokens = set(
                    str(doc.get("city", "")).lower().split() +
                    str(doc.get("state", "")).lower().split() +
                    str(doc.get("country", "")).lower().split()
                )
                
                has_valid_shared_word = False
                for cw in core_query_words:
                    # Strip trailing 's' to create a base word (resistors -> resistor)
                    base_cw = cw[:-1] if cw.endswith('s') and not cw.endswith('ss') else cw
                    
                    for dt in doc_tokens:
                        # Match exact word, or simple plurals (s, es, ies)
                        if dt == base_cw or dt == base_cw + "s" or dt == base_cw + "es" or dt == base_cw + "ies":
                            if dt in geo_tokens:
                                continue
                            has_valid_shared_word = True
                            break
                    
                    if has_valid_shared_word:
                        break
                
                if not has_valid_shared_word:
                    continue

            seen_ids.add(b_id)
            b_name = str(doc.get("businessName", ""))
            c_name = str(doc.get("categoryName", ""))

            row = dict(doc)
            row["matchType"] = "AI Similarity Match"
            row["matchPercentage"] = float(percentage)
            
            # Clean up the UI reason: use core words, check name and category, 
            # and stop showing weird fuzzy-match artifacts like "Private (~AI match)".
            matched_in_fields = (
                get_matching_words(core_query_str, b_name) or
                get_matching_words(core_query_str, c_name)
            )
            row["matchedKeyword"] = matched_in_fields if matched_in_fields else "Semantic Match"
            
            ai_results.append(row)

    combined = lexical_results + ai_results
    return sorted(combined, key=lambda r: r["matchPercentage"], reverse=True)