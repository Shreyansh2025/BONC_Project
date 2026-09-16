"""
Local product search: exact match -> priority partial-word match -> AI
(FAISS) fallback. Same structure as app/utils/b2b_product_search.py, but
over app/db.py:products_table() — the brochure-extracted Products table
populated by POST /api/products (see app/routes/brochure.py), NOT the
imported B2B catalog (that's b2b_product_search.py's job).

Field names here intentionally match the local schema (productName,
category, model, description, price, features, specifications, images,
imagePath, slug, sourceFileName) as shaped by serialize_row("Products", ...)
— this table has no categoryName/brandName/keyWords/businessName columns
like B2BProducts does.

Shares the SentenceTransformer singleton with b2b_search /
b2b_product_search (see embedding_model.py) so the model is only loaded
into memory once.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import faiss
import numpy as np
from sqlalchemy import select

from app.db import get_engine, products_table
from app.logger import logger
from app.utils.embedding_model import EMBEDDING_DIM, get_model
from app.utils.serialize import serialize_row
from app.utils.search_common import (
    build_vocabulary,
    clean_term,
    contains_loose,
    correct_query,
    get_closest_word,
    get_matching_words,
)

STOP_WORDS = {
    "in", "at", "near", "and", "for", "the", "of", "to", "company",
    "ltd", "pvt", "limited", "private", "enterprises", "industries",
}

# Ordered list of product docs — position i mirrors row i of _index.
_products: list[dict[str, Any]] = []
_index: faiss.Index | None = None
# Word-frequency table built from _products' own text, used to spell-
# correct query words toward real catalog words (see search_common.py).
_vocabulary: Counter = Counter()


def index_status() -> dict[str, Any]:
    """Snapshot of in-memory index state, for the /api/debug/index-status route."""
    return {"count": len(_products), "index_built": _index is not None}


def warm_up_model() -> None:
    """Loads the (shared) embedding model immediately — no-op if
    b2b_search / b2b_product_search already warmed it up."""
    get_model()


def _specs_text(specs: Any) -> str:
    if not isinstance(specs, dict) or not specs:
        return ""
    return " ".join(f"{k} {v}" for k, v in specs.items())


def _combined_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("productName", ""),
        doc.get("category", ""),
        doc.get("model", ""),
        doc.get("description", ""),
        " ".join(doc.get("features") or []),
        _specs_text(doc.get("specifications")),
        doc.get("sourceFileName", ""),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(products_table())).mappings().all()
        return [serialize_row("Products", r) for r in rows]


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
    """Loads all brochure-extracted products from SQL Server and
    (re)builds the in-memory FAISS index. Safe to call more than once —
    e.g. right after a new product is saved via POST /products — to pick
    up fresh data without restarting the server."""
    global _products, _index, _vocabulary

    docs = await asyncio.to_thread(_load_products_sync)
    if not docs:
        logger.info("No local product rows found — local product search index left empty")
        _products = []
        _index = None
        _vocabulary = Counter()
        return

    index = await asyncio.to_thread(_build_index_sync, docs)

    _products = docs
    _index = index
    _vocabulary = build_vocabulary(docs, _combined_text)
    logger.info(f"Local product search index built with {len(docs)} products")


def _contains_term(doc: dict[str, Any], term: str, fields: list[str]) -> bool:
    return any(contains_loose(term, doc.get(f, "")) for f in fields)


def search_products_sync(query: str) -> list[dict[str, Any]]:
    """Runs the exact -> partial -> AI-fallback search over the in-memory
    local product cache. CPU-bound — call via asyncio.to_thread from the route."""
    search_term = clean_term(query)
    if not search_term or not _products:
        return []

    # Spell-correct against the catalog's own vocabulary before matching,
    # so a typo like "chiar" is corrected to "chair" instead of skipping
    # straight to the much fuzzier AI fallback. Only touches words that
    # aren't already a real catalog word, so this never changes a query
    # that already matches something.
    search_term = correct_query(search_term, _vocabulary)

    # Layer 1: exact match (full phrase) against name / category / model
    # / source file. contains_loose also matches across a one-word/
    # two-word spelling difference on either side.
    exact_fields = ["productName", "category", "model", "sourceFileName"]
    exact_results = []
    for doc in _products:
        if _contains_term(doc, search_term, exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                get_matching_words(search_term, row.get("productName", ""))
                or get_matching_words(search_term, row.get("category", ""))
                or get_matching_words(search_term, row.get("model", ""))
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

            category_text = str(doc.get("category", "")).lower()
            desc_text = str(doc.get("description", "")).lower()
            specs_text = _specs_text(doc.get("specifications")).lower()
            model_text = str(doc.get("model", "")).lower()

            match_score = 0.0
            if contains_loose(word, p_name):
                match_score = 90.0 - position_penalty
            elif contains_loose(word, model_text):
                match_score = 85.0 - position_penalty
            elif contains_loose(word, category_text):
                match_score = 80.0 - position_penalty
            elif contains_loose(word, desc_text):
                match_score = 75.0 - position_penalty
            elif contains_loose(word, specs_text):
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
        matched_in_name = get_matching_words(search_term, p_name)
        row["matchedKeyword"] = (
            matched_in_name
            if matched_in_name
            else f"{get_closest_word(search_term, p_name)} (~AI Match)"
        )
        ai_results.append(row)

    return sorted(ai_results, key=lambda r: r["matchPercentage"], reverse=True)


async def search_products(query: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(search_products_sync, query)