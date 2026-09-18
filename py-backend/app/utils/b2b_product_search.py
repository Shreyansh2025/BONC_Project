"""
B2B product catalog search: exact match -> priority partial-word match ->
AI (FAISS) fallback, blended into one hybrid-ranked result set.

Reads directly from the company's own live `ProductsAndServices` table,
joined with:
  - Industry / Category / SubCategory  -> human-readable names.
  - Company                            -> business name + slug.
  - Media                              -> product images (see RankedMedia
    below; ProductsAndServices has no image column of its own).
  - AddressDetails                     -> the product's business's City/
    State/Country/Pincode/Latitude/Longitude, joined via the same
    BusinessId used for the Company join (a product has no address of
    its own, it inherits its business's location). Confirmed:
    EntityType='Company', EntityTypeId=Company.BusinessId, one row per
    business (no multi-address handling needed).
  - BusinessType                       -> matches "IndustryType" seen in
    the company's own live search response ("Manufacturer"/"Distributor").
  - BoncUser                           -> UserSlug, via Company.UserRowId.

This closes the location gap (was previously undiscovered — the address
data was never missing from their system, just not in Company/
ProductsAndServices themselves) plus IndustryType/UserSlug/
ProductUniqueId/CreatedOn/IsAllowRfqRfiFromBusiness, all seen in the
company's own /api/commonService search response but missing here before.

Image handling: ProductsAndServices has no image column of its own — a
product's images live in the shared `Media` table, linked by
Media.EntityTypeId = ProductsAndServices.ProductsAndServicesId. A product
can have multiple Media rows (gallery images) with no IsPrimary/SortOrder
flag to say which one is "the" thumbnail, so this picks the EARLIEST
uploaded image (MIN CreatedOn) per product as a reasonable default. If
the company's convention is actually "most recent" or something else,
flip ORDER BY m1.CreatedOn ASC to DESC in _PRODUCT_SQL below — that's the
only place this decision lives.

Exact/partial matching checks productName, brandName, categoryName and
industryName (mirroring company search's field coverage) — an earlier
version of this file matched on productName only, which meant a
category/industry-style query (e.g. "healthcare") found businesses in
that industry via b2b_search.py but zero of their actual products, since
no product is ever literally NAMED "Healthcare". See
search_products_sync below.

Caching / concurrency strategy: results are loaded into memory as a plain
list. Everything the search functions touch (products list, FAISS index,
vocabulary, inverted index) lives in ONE `_ProductState` object behind a
single module-level reference, swapped atomically in build_index() — see
the matching note in b2b_search.py for why that matters (main.py rebuilds
this every hour while requests keep flowing on other threads).
"""

from __future__ import annotations

import asyncio
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
)

STOP_WORDS = {"in", "at", "near", "and", "for", "the", "of", "to"}

# See the identical constant in b2b_search.py: below this many lexical
# (exact + partial) hits, the AI/FAISS layer also runs and its results are
# merged in rather than skipped or blocked by an early return.
MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI = 20

# AI/FAISS layer confidence bands (B2B product search) -- same reasoning
# as the identical constants in b2b_search.py. Below AI_MIN_PERCENTAGE a
# result is dropped outright. Between AI_MIN_PERCENTAGE and
# AI_HIGH_CONFIDENCE_PERCENTAGE, a result also needs at least one literal
# shared word with the query (see shares_any_word) to be kept, since
# that band is where "same broad domain, different thing" false matches
# live. At/above AI_HIGH_CONFIDENCE_PERCENTAGE it's trusted on semantics
# alone. Starting numbers, not tuned against labeled data yet -- adjust
# these first once real good/bad examples are available.
AI_MIN_PERCENTAGE = 40.0
AI_HIGH_CONFIDENCE_PERCENTAGE = 65.0


@dataclass(frozen=True)
class _ProductState:
    # Ordered list of product docs — position i mirrors row i of `index`.
    products: list[dict[str, Any]] = field(default_factory=list)
    index: faiss.Index | None = None
    vocabulary: Counter = field(default_factory=Counter)
    inverted_index: dict[str, set[int]] = field(default_factory=dict)


_state = _ProductState()


_PRODUCT_SQL = text(
    """
    WITH RankedMedia AS (
        SELECT
            m1.EntityTypeId,
            m1.MediaPath,
            ROW_NUMBER() OVER (
                PARTITION BY m1.EntityTypeId ORDER BY m1.CreatedOn ASC
            ) AS rn
        FROM Media m1
        WHERE m1.MediaType = 'image'
    )
    SELECT
        p.ProductsAndServicesId,
        p.ProductsAndServicesUniqueId,
        p.ProductsAndServicesName,
        p.ItemType,
        p.ProductType,
        p.Description,
        p.KeyWords,
        p.BrandName,
        p.ManufacturerName,
        p.CountryOfOrigin,
        p.CustomizationAvailability,
        p.ExportCapabilities,
        p.ShipsGlobally,
        p.HazardousGoods,
        p.GSTPercentage,
        p.AverageDeliveryTime,
        p.ProcessingTime,
        p.MinimumOrderQuantity,
        p.CustomizedPrice,
        p.MinPrice,
        p.MaxPrice,
        p.Status,
        p.PublishDate,
        p.CreatedOn,
        p.Slug,
        p.CategoryId,
        p.IndustryId,
        p.SubCategoryId,
        p.BusinessId,
        i.IndustryName,
        cat.CategoryName,
        sub.SubCategoryName,
        c.BusinessName,
        c.BusinessSlug,
        c.IsAllowRfqRfiFromBusiness,
        bt.BusinessTypeName,
        addr.City,
        addr.State,
        addr.Country,
        addr.Pincode,
        addr.Latitude,
        addr.Longitude,
        u.Slug AS UserSlug,
        img.MediaPath AS ImagePath
    FROM ProductsAndServices p
    LEFT JOIN Industry i
        ON i.IndustryId = p.IndustryId AND i.IsActive = 1 AND ISNULL(i.IsDeleted, 0) = 0
    LEFT JOIN Category cat
        ON cat.CategoryId = p.CategoryId AND cat.IsActive = 1 AND ISNULL(cat.IsDeleted, 0) = 0
    LEFT JOIN SubCategory sub
        ON sub.SubCategoryId = p.SubCategoryId AND sub.IsActive = 1 AND ISNULL(sub.IsDeleted, 0) = 0
    LEFT JOIN Company c
        ON c.BusinessId = p.BusinessId
    LEFT JOIN BusinessType bt
        ON bt.BusinessTypeId = c.BusinessTypeId
    LEFT JOIN AddressDetails addr
        ON addr.EntityTypeId = p.BusinessId AND addr.EntityType = 'Company'
    LEFT JOIN BoncUser u
        ON u.UserRowId = c.UserRowId AND ISNULL(u.IsDeleted, 0) = 0
    LEFT JOIN RankedMedia img
        ON img.EntityTypeId = p.ProductsAndServicesId AND img.rn = 1
    WHERE ISNULL(p.IsDeleted, 0) = 0 AND p.Status = 'Publish'
    """
)


def _s(value: Any) -> str | None:
    """SQL Server `uniqueidentifier` columns come back as uuid.UUID objects,
    not strings — this stringifies them (and passes through None)."""
    return str(value) if value is not None else None


def index_status() -> dict[str, Any]:
    """Snapshot of in-memory index state, for the /api/debug/index-status route."""
    state = _state
    return {"count": len(state.products), "index_built": state.index is not None}


def _combined_text(doc: dict[str, Any]) -> str:
    # City/state added now that AddressDetails is joined (via the
    # product's business) — a query like "chair in bangalore" previously
    # had nothing to match "bangalore" against at all; now it does.
    parts = [
        doc.get("productName", ""),
        doc.get("description", ""),
        doc.get("keyWords", ""),
        doc.get("brandName", ""),
        doc.get("categoryName", ""),
        doc.get("industryName", ""),
        doc.get("city", ""),
        doc.get("state", ""),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(_PRODUCT_SQL).mappings().all()

    docs: list[dict[str, Any]] = []
    for r in rows:
        docs.append(
            {
                "productsAndServicesId": _s(r["ProductsAndServicesId"]),
                "productUniqueId": r["ProductsAndServicesUniqueId"],
                "productName": r["ProductsAndServicesName"],
                "itemType": r["ItemType"],
                "productType": r["ProductType"],
                "description": r["Description"],
                "keyWords": r["KeyWords"],
                "brandName": r["BrandName"],
                "manufacturerName": r["ManufacturerName"],
                "countryOfOrigin": r["CountryOfOrigin"],
                "customizationAvailability": r["CustomizationAvailability"],
                "exportCapabilities": r["ExportCapabilities"],
                "shipsGlobally": r["ShipsGlobally"],
                "hazardousGoods": r["HazardousGoods"],
                "gstPercentage": r["GSTPercentage"],
                "averageDeliveryTime": r["AverageDeliveryTime"],
                "processingTime": r["ProcessingTime"],
                "minimumOrderQuantity": r["MinimumOrderQuantity"],
                "customizedPrice": r["CustomizedPrice"],
                "minPrice": float(r["MinPrice"]) if r["MinPrice"] is not None else None,
                "maxPrice": float(r["MaxPrice"]) if r["MaxPrice"] is not None else None,
                "status": r["Status"],
                "publishDate": r["PublishDate"].isoformat() if r["PublishDate"] else None,
                "createdOn": r["CreatedOn"].isoformat() if r["CreatedOn"] else None,
                # Real slug straight from ProductsAndServices — no slugify()
                # fallback, always populated for a Publish-status row.
                "slug": r["Slug"],
                "categoryId": _s(r["CategoryId"]),
                "industryId": _s(r["IndustryId"]),
                "subCategoryId": _s(r["SubCategoryId"]),
                "businessId": _s(r["BusinessId"]),
                "industryName": r["IndustryName"],
                "categoryName": r["CategoryName"],
                "subCategoryName": r["SubCategoryName"],
                "businessName": r["BusinessName"],
                "businessSlug": r["BusinessSlug"],
                "isAllowRfqRfiFromBusiness": r["IsAllowRfqRfiFromBusiness"],
                "businessTypeName": r["BusinessTypeName"],
                "userSlug": r["UserSlug"],
                "imagePath": r["ImagePath"],
                # AddressDetails join, via the product's business.
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
    """Loads all Published products from the live ProductsAndServices table
    and (re)builds the in-memory FAISS index + inverted index. Called once
    at startup and then every hour by main.py's auto-refresh loop — each
    call does a full read of ProductsAndServices + Industry + Category +
    SubCategory + Company + BusinessType + AddressDetails + BoncUser +
    Media, so keep that in mind if refresh frequency ever needs to change
    for load reasons."""
    global _state

    docs = await asyncio.to_thread(_load_products_sync)
    if not docs:
        logger.info("No Published products found — product search index left empty")
        _state = _ProductState()
        return

    index = await asyncio.to_thread(_build_index_sync, docs)
    vocabulary = build_vocabulary(docs, _combined_text)
    inverted = build_inverted_index(docs, _combined_text)

    # Single atomic pointer swap — see b2b_search.py's build_index() for
    # why this matters instead of reassigning several globals one at a time.
    _state = _ProductState(
        products=docs, index=index, vocabulary=vocabulary, inverted_index=inverted
    )
    logger.info(f"B2B product search index built with {len(docs)} products")


def search_products_sync(query: str) -> list[dict[str, Any]]:
    """Exact -> partial -> AI-fallback hybrid search over the in-memory
    product cache. CPU-bound — call via asyncio.to_thread from the route."""
    state = _state  # one consistent snapshot for the whole call
    search_term = clean_term(query)
    if not search_term or not state.products:
        return []

    # Spell-correct against the catalog's own vocabulary before matching,
    # so a typo like "chiar" is corrected to "chair" instead of skipping
    # straight to the much fuzzier AI fallback. Only touches words that
    # aren't already a real catalog word, so this never changes a query
    # that already matches something.
    search_term = correct_query(search_term, state.vocabulary)

    # Layer 1: exact match against product name / brand / category /
    # industry. Previously this was NAME ONLY, unlike company search
    # (which already checked industryName/categoryName). That's the real,
    # general bug behind "healthcare finds 0 products but 14 businesses":
    # no product is literally named "Healthcare", but plenty belong to
    # the "Pharmaceutical Drugs Healthcare" industry -- and that applies
    # to EVERY category/industry/brand-style query, not just this one
    # word. contains_loose also matches across a one-word/two-word
    # spelling difference on either side, and is boundary-safe (a query
    # for "pan" can no longer match "pant"/"panel").
    exact_fields = ["productName", "brandName", "categoryName", "industryName"]
    exact_results = []
    # Dedup by productsAndServicesId, not by name -- two different
    # products that happen to share a display name (common across
    # different sellers) are still two different listings and must both
    # be able to appear. The old seen_names (name-string) set meant the
    # second one silently vanished from the results.
    seen_ids: set[str] = set()
    candidates = candidate_indices(search_term, state.inverted_index, len(state.products))
    for i in candidates:
        doc = state.products[i]
        if any(contains_loose(search_term, doc.get(f, "")) for f in exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                get_matching_words(search_term, row.get("productName", ""))
                or get_matching_words(search_term, row.get("brandName", ""))
                or get_matching_words(search_term, row.get("categoryName", ""))
                or get_matching_words(search_term, row.get("industryName", ""))
            )
            row["matchedKeyword"] = matched or search_term.title()
            exact_results.append(row)
            seen_ids.add(str(doc.get("productsAndServicesId", "")))

    # Layer 1.5: partial word match, weighted by which field it hit --
    # same tiered structure as company search: name beats brand beats
    # category/industry. Words of length 2 are kept (only true stop
    # words are dropped) so short but meaningful terms like "AC" or "TV"
    # can still contribute a partial match.
    words = [w for w in search_term.split() if len(w) >= 2 and w not in STOP_WORDS]
    partial_results: list[dict[str, Any]] = []

    for word_index, word in enumerate(words):
        position_penalty = word_index * 2.0
        word_candidates = candidate_indices(word, state.inverted_index, len(state.products))
        for i in word_candidates:
            doc = state.products[i]
            p_id = str(doc.get("productsAndServicesId", ""))
            if p_id in seen_ids:
                continue

            p_name = str(doc.get("productName", ""))
            brand_text = str(doc.get("brandName", "")).lower()
            cat_text = str(doc.get("categoryName", "")).lower()
            industry_text = str(doc.get("industryName", "")).lower()

            match_score = 0.0
            if contains_loose(word, p_name):
                match_score = 90.0 - position_penalty
            elif contains_loose(word, brand_text):
                match_score = 85.0 - position_penalty
            elif contains_loose(word, cat_text) or contains_loose(word, industry_text):
                match_score = 80.0 - position_penalty

            # Disabled: this used to jump a match straight to ~99% just
            # for hitting the FIRST query word, regardless of whether any
            # other word in a multi-word query matched at all -- see the
            # identical note in b2b_search.py. Left commented, not
            # deleted, in case a more careful version is wanted later.
            # if word_index == 0 and match_score > 0:
            #     match_score = min(99.0, match_score + 10.0)

            if match_score >= 10.0:
                seen_ids.add(p_id)
                row = dict(doc)
                row["matchType"] = "Partial Word Match"
                row["matchPercentage"] = round(match_score, 2)
                row["matchedKeyword"] = word.title()
                partial_results.append(row)

    lexical_results = exact_results + partial_results

    # Layer 2: AI fallback via FAISS similarity over the embedded product
    # text. Now runs whenever lexical coverage is thin (below
    # MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI), merged into the same ranked
    # list instead of being blocked outright by any single exact/partial
    # hit — see the matching note in b2b_search.py.
    ai_results: list[dict[str, Any]] = []
    if len(lexical_results) < MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI and state.index is not None:
        model = get_model()
        query_vector = model.encode([search_term])
        faiss.normalize_L2(query_vector)
        distances, indices = state.index.search(query_vector.astype("float32"), k=50)

        for i, idx in enumerate(indices[0]):
            if idx == -1 or idx >= len(state.products):
                continue
            distance_score = distances[0][i]
            cosine_sim = 1 - (distance_score / 2)
            percentage = max(0.0, round(cosine_sim * 100, 2))
            if percentage < AI_MIN_PERCENTAGE:
                continue

            doc = state.products[int(idx)]
            p_id = str(doc.get("productsAndServicesId", ""))
            if p_id in seen_ids:
                continue

            # Middle-confidence band safety net: without at least one
            # shared word, a same-domain-but-different-thing match gets
            # dropped here instead of surfacing on semantic proximity alone.
            if percentage < AI_HIGH_CONFIDENCE_PERCENTAGE and not shares_any_word(
                search_term, _combined_text(doc)
            ):
                continue

            seen_ids.add(p_id)
            p_name = str(doc.get("productName", ""))

            row = dict(doc)
            row["matchType"] = "AI Similarity Match"
            row["matchPercentage"] = float(percentage)
            matched_in_name = get_matching_words(search_term, p_name)
            row["matchedKeyword"] = (
                matched_in_name if matched_in_name else f"{get_closest_word(search_term, p_name)} (~AI Match)"
            )
            ai_results.append(row)

    combined = lexical_results + ai_results
    return sorted(combined, key=lambda r: r["matchPercentage"], reverse=True)