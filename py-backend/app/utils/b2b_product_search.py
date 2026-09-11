"""
B2B product catalog search: exact match -> priority partial-word match ->
AI (FAISS) fallback.

CHANGED (staging): now reads directly from the company's own live
`ProductsAndServices` table (joined with `Company`, `Industry`,
`Category`, `SubCategory`, and `Media`) instead of our own `B2BProducts`
import copy. This is what fixes the slug bug (real ProductsAndServices.Slug
is now used as-is — see routes/search.py for the matching slugify()
removal) and adds real IndustryId/CategoryId/ProductsAndServicesId, none
of which were ever present in the old xlsx import.

Image handling: ProductsAndServices has no image column of its own — a
product's images live in the shared `Media` table, linked by
Media.EntityTypeId = ProductsAndServices.ProductsAndServicesId. A product
can have multiple Media rows (gallery images) with no IsPrimary/SortOrder
flag to say which one is "the" thumbnail, so this picks the EARLIEST
uploaded image (MIN CreatedOn) per product as a reasonable default. If
the company's convention is actually "most recent" or something else,
flip ORDER BY m1.CreatedOn ASC to DESC in _PRODUCT_SQL below — that's the
only place this decision lives.

Faithfully keeps one quirk from the original tool: exact/partial match is
name-only for products (unlike companies, which check several fields) —
see search_products_sync below.

Same caching strategy as before: results are loaded into memory as a
plain list and the same three-layer scoring logic runs directly against
it. build_index() refreshes this cache; main.py's hourly loop calls it
automatically.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any

import faiss
import numpy as np
from sqlalchemy import text

from app.db import get_engine
from app.logger import logger
from app.utils.embedding_model import EMBEDDING_DIM, get_model

STOP_WORDS = {"in", "at", "near", "and", "for", "the", "of", "to"}

# Ordered list of product docs — position i mirrors row i of _index.
_products: list[dict[str, Any]] = []
_index: faiss.Index | None = None


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
    return {"count": len(_products), "index_built": _index is not None}


def _combined_text(doc: dict[str, Any]) -> str:
    # Extended vs. the old xlsx-backed version (which only had name +
    # description + keyWords) — brandName/categoryName/industryName are
    # now real joined data, so folding them in makes the AI-fallback
    # layer meaningfully better, not just parity with the old tool.
    parts = [
        doc.get("productName", ""),
        doc.get("description", ""),
        doc.get("keyWords", ""),
        doc.get("brandName", ""),
        doc.get("categoryName", ""),
        doc.get("industryName", ""),
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
                # Real slug straight from ProductsAndServices — no slugify()
                # fallback needed anymore, this is always populated for any
                # Publish-status row. See routes/search.py for the matching
                # removal of the slugify() fallback on the read side.
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
                "imagePath": r["ImagePath"],
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
    and (re)builds the in-memory FAISS index. Called once at startup and
    then every hour by main.py's auto-refresh loop — each call does a full
    read of ProductsAndServices + Industry + Category + SubCategory +
    Company + Media, so keep that in mind if refresh frequency ever needs
    to change for load reasons."""
    global _products, _index

    docs = await asyncio.to_thread(_load_products_sync)
    if not docs:
        logger.info("No Published products found — product search index left empty")
        _products = []
        _index = None
        return

    index = await asyncio.to_thread(_build_index_sync, docs)

    _products = docs
    _index = index
    logger.info(f"B2B product search index built with {len(docs)} products")


def _get_matching_words(query: str, target_text: Any) -> str | None:
    if not target_text:
        return None
    query_words = set(re.findall(r"\w+", query.lower()))
    target_words = set(re.findall(r"\w+", str(target_text).lower()))
    common = query_words.intersection(target_words)
    return ", ".join(common).title() if common else None


def _get_closest_word(query: str, target_text: Any) -> str:
    if not target_text:
        return "N/A"
    query_words = re.findall(r"\w+", query.lower())
    target_words = re.findall(r"\w+", str(target_text).lower())
    for qw in query_words:
        matches = difflib.get_close_matches(qw, target_words, n=1, cutoff=0.3)
        if matches:
            return matches[0].title()
    return " ".join(str(target_text).split()[:2]).title()


def search_products_sync(query: str) -> list[dict[str, Any]]:
    """Exact -> partial -> AI-fallback search over the in-memory product
    cache. CPU-bound — call via asyncio.to_thread from the route."""
    search_term = re.sub(r"[^a-zA-Z0-9\s]", "", query).lower().strip()
    if not search_term or not _products:
        return []

    # Layer 1: exact match on product name only (matches original tool's
    # behavior: products only ever matched on Product_Name, unlike
    # companies which checked several fields — kept for parity).
    exact_results = []
    for doc in _products:
        if search_term in str(doc.get("productName", "")).lower():
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            row["matchedKeyword"] = _get_matching_words(search_term, row.get("productName", "")) or search_term.title()
            exact_results.append(row)

    if exact_results:
        return sorted(exact_results, key=lambda r: r["matchPercentage"], reverse=True)

    # Layer 1.5: partial word match — candidates are name-matches only,
    # same as the original.
    words = [w for w in search_term.split() if len(w) > 2 and w not in STOP_WORDS]
    seen_names: set[str] = set()
    partial_results: list[dict[str, Any]] = []

    for word_index, word in enumerate(words):
        position_penalty = word_index * 2.0
        for doc in _products:
            p_name = str(doc.get("productName", ""))
            if p_name in seen_names:
                continue
            if word not in p_name.lower():
                continue

            match_score = 90.0 - position_penalty
            if word_index == 0:
                match_score = min(99.0, match_score + 10.0)

            if match_score >= 10.0:
                seen_names.add(p_name)
                row = dict(doc)
                row["matchType"] = "Partial Word Match"
                row["matchPercentage"] = round(match_score, 2)
                row["matchedKeyword"] = word.title()
                partial_results.append(row)

    if partial_results:
        return sorted(partial_results, key=lambda r: r["matchPercentage"], reverse=True)

    # Layer 2: AI fallback via FAISS similarity over the embedded product text
    if _index is None:
        return []

    model = get_model()
    query_vector = model.encode([search_term])
    faiss.normalize_L2(query_vector)
    distances, indices = _index.search(query_vector.astype("float32"), k=50)

    ai_results = []
    seen_names = set()
    for i, idx in enumerate(indices[0]):
        if idx == -1 or idx >= len(_products):
            continue
        distance_score = distances[0][i]
        cosine_sim = 1 - (distance_score / 2)
        percentage = max(0.0, round(cosine_sim * 100, 2))
        if percentage < 10.0:
            continue

        doc = _products[int(idx)]
        p_name = str(doc.get("productName", ""))
        if p_name in seen_names:
            continue
        seen_names.add(p_name)

        row = dict(doc)
        row["matchType"] = "AI Similarity Match"
        row["matchPercentage"] = float(percentage)
        matched_in_name = _get_matching_words(search_term, p_name)
        row["matchedKeyword"] = (
            matched_in_name if matched_in_name else f"{_get_closest_word(search_term, p_name)} (~AI Match)"
        )
        ai_results.append(row)

    return sorted(ai_results, key=lambda r: r["matchPercentage"], reverse=True)