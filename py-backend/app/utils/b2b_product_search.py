"""
B2B product catalog search: exact match -> priority partial-word match ->
AI (FAISS) fallback.

Ported from the standalone Streamlit + MySQL "Bonc_Network" search engine
(the Products tab in app.py). This is the REAL product catalog data
(ProductAndServiceDocuments.xlsx, ~2,500 published products) — distinct
from app/utils/product_search.py, which searches brochure-extracted items
in the Products table.

Faithfully replicates one quirk of the original: the original SQL for
products only ever fetched candidate rows where the search word appeared
in Product_Name (`WHERE Product_Name LIKE %word%`), unlike companies
which searched several columns. So even though the Python-level scoring
code checks a description field too, that branch could never actually
fire for products — a row only ever showed up if its name matched. This
port keeps that same effective behavior (name-only partial matching) for
parity with the original tool.

Same as b2b_search.py: cached in memory since this is a bulk import that
only changes when someone re-runs the import script.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any

import faiss
import numpy as np
from sqlalchemy import select

from app.db import b2b_products_table, get_engine
from app.logger import logger
from app.utils.embedding_model import EMBEDDING_DIM, get_model
from app.utils.serialize import serialize_row

STOP_WORDS = {"in", "at", "near", "and", "for", "the", "of", "to"}

# Ordered list of product docs — position i mirrors row i of _index.
_products: list[dict[str, Any]] = []
_index: faiss.Index | None = None


def _combined_text(doc: dict[str, Any]) -> str:
    # Matches sync_db_to_ai.py's sync_products(): Product_Name + Description + KeyWords only.
    parts = [doc.get("productName", ""), doc.get("description", ""), doc.get("keyWords", "")]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_products_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(b2b_products_table())).mappings().all()
        return [serialize_row("B2BProducts", r) for r in rows]


async def build_index() -> None:
    """Loads all catalog products from SQL Server and (re)builds the
    in-memory FAISS index. Safe to call again after re-running the import
    script to pick up fresh data without restarting the server."""
    global _products, _index

    docs = await asyncio.to_thread(_load_products_sync)
    if not docs:
        logger.info("No B2B product rows found — product search index left empty")
        _products = []
        _index = None
        return

    model = get_model()
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


def search_products_sync(query: str) -> list[dict[str, Any]]:
    """Exact -> partial -> AI-fallback search over the in-memory product
    cache. CPU-bound — call via asyncio.to_thread from the route."""
    search_term = re.sub(r"[^a-zA-Z0-9\s]", "", query).lower().strip()
    if not search_term or not _products:
        return []

    # Layer 1: exact match on product name (matches original: products only
    # ever matched on Product_Name, unlike companies which checked several fields)
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

    # Layer 1.5: partial word match — candidates are name-matches only
    # (same as the original's WHERE Product_Name LIKE %word%), so the score
    # is always the name-tier score.
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