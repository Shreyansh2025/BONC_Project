"""
B2B product search: exact match -> priority partial-word match -> AI
(FAISS) fallback. Same structure as app/utils/b2b_search.py — see that
module's docstring for the full rationale.

Product data lives in SQL Server's B2BProducts table (see app/db.py:
b2b_products_table), loaded from data/b2b_products.xlsx via
scripts/import_b2b_products.py. The FAISS index is built in memory at
startup rather than loaded from the old Bonc_Network product_mysql_index.bin
— that file was built against MySQL's `products` table (different schema,
different row order) and doesn't line up with this table at all.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from functools import lru_cache
from typing import Any

import faiss
import numpy as np
from sqlalchemy import select

from app.db import b2b_products_table, get_engine
from app.logger import logger
from app.utils.serialize import serialize_row

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

STOP_WORDS = {
    "in", "at", "near", "and", "for", "the", "of", "to", "company",
    "ltd", "pvt", "limited", "private", "enterprises", "industries",
}

# Ordered list of product docs — position i mirrors row i of _index.
_products: list[dict[str, Any]] = []
_index: faiss.Index | None = None


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def warm_up_model() -> None:
    """Loads the embedding model immediately (shared cache with
    b2b_search — if that already warmed it up, this is a no-op)."""
    _get_model()


def _combined_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("productName", ""),
        doc.get("industryName", ""),
        doc.get("categoryName", ""),
        doc.get("subCategoryName", ""),
        doc.get("itemType", ""),
        doc.get("productType", ""),
        doc.get("description", ""),
        doc.get("manufacturerName", ""),
        doc.get("brandName", ""),
        doc.get("keyWords", ""),
        doc.get("businessName", ""),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(b2b_products_table())).mappings().all()
        return [serialize_row("B2BProducts", r) for r in rows]


async def build_index() -> None:
    """Loads all products from SQL Server and (re)builds the in-memory
    FAISS index. Safe to call more than once — e.g. after re-running the
    import script — to pick up fresh data without restarting the server."""
    global _products, _index

    docs = await asyncio.to_thread(_load_products_sync)
    if not docs:
        logger.info("No B2B product rows found — search index left empty")
        _products = []
        _index = None
        return

    model = _get_model()
    texts = [_combined_text(d) for d in docs]
    vectors = model.encode(texts)
    faiss.normalize_L2(vectors)

    index = faiss.IndexFlatL2(EMBEDDING_DIM)
    index.add(np.array(vectors).astype("float32"))

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


def _contains_term(doc: dict[str, Any], term: str, fields: list[str]) -> bool:
    return any(term in str(doc.get(f, "")).lower() for f in fields)


def search_products_sync(query: str) -> list[dict[str, Any]]:
    """Runs the exact -> partial -> AI-fallback search over the in-memory
    product cache. CPU-bound — call via asyncio.to_thread from the route."""
    search_term = re.sub(r"[^a-zA-Z0-9\s]", "", query).lower().strip()
    if not search_term or not _products:
        return []

    # Layer 1: exact match (full phrase) against name / category / brand
    exact_fields = ["productName", "categoryName", "subCategoryName", "brandName", "businessName"]
    exact_results = []
    for doc in _products:
        if _contains_term(doc, search_term, exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                _get_matching_words(search_term, row.get("productName", ""))
                or _get_matching_words(search_term, row.get("categoryName", ""))
                or _get_matching_words(search_term, row.get("brandName", ""))
            )
            row["matchedKeyword"] = matched or search_term.title()
            exact_results.append(row)

    if exact_results:
        return sorted(exact_results, key=lambda r: r["matchPercentage"], reverse=True)

    # Layer 1.5: partial word match, weighted by which field it hit
    words = [w for w in search_term.split() if len(w) > 2 and w not in STOP_WORDS]
    seen_names: set[str] = set()
    partial_results: list[dict[str, Any]] = []

    for word_index, word in enumerate(words):
        position_penalty = word_index * 2.0
        for doc in _products:
            p_name = str(doc.get("productName", ""))
            if p_name in seen_names:
                continue

            category_text = str(doc.get("categoryName", "")).lower()
            desc_text = str(doc.get("description", "")).lower()
            keywords_text = str(doc.get("keyWords", "")).lower()
            brand_text = str(doc.get("brandName", "")).lower()

            match_score = 0.0
            if word in p_name.lower():
                match_score = 90.0 - position_penalty
            elif word in keywords_text:
                match_score = 85.0 - position_penalty
            elif word in category_text:
                match_score = 80.0 - position_penalty
            elif word in desc_text:
                match_score = 75.0 - position_penalty
            elif word in brand_text:
                match_score = 70.0 - position_penalty

            if word_index == 0 and match_score > 0:
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

    model = _get_model()
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
            matched_in_name
            if matched_in_name
            else f"{_get_closest_word(search_term, p_name)} (~AI Match)"
        )
        ai_results.append(row)

    return sorted(ai_results, key=lambda r: r["matchPercentage"], reverse=True)


async def search_products(query: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(search_products_sync, query)
