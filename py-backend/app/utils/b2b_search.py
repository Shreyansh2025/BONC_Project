"""
B2B company search: exact match -> priority partial-word match -> AI
(FAISS) fallback.

Ported from the standalone Streamlit + MySQL "Bonc_Network" search engine.
Company data now lives in SQL Server's B2BCompanies table (see
app/db.py: b2b_companies_table) instead of MySQL's `companies_master`
table, and the FAISS index is built in memory from that table instead of
being loaded from a static `.bin` file whose row order was tied to
MySQL's auto-increment id.

The company set is small (~700 rows), so rather than translating every
MySQL query into a SQL Server equivalent inline, the whole table is
cached in memory as a plain list and the same three-layer scoring logic
from the original `app.py` runs directly against that list. `build_index()`
refreshes this cache; call it again after re-importing data.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any

import faiss
import numpy as np
from sqlalchemy import select

from app.db import b2b_companies_table, get_engine
from app.logger import logger
from app.utils.embedding_model import EMBEDDING_DIM, get_model
from app.utils.serialize import serialize_row

STOP_WORDS = {
    "in", "at", "near", "and", "for", "the", "of", "to", "company",
    "ltd", "pvt", "limited", "private", "enterprises", "industries",
}

# Ordered list of company docs — position i mirrors row i of _index, so a
# FAISS hit at position i maps directly to _companies[i].
_companies: list[dict[str, Any]] = []
_index: faiss.Index | None = None


def _combined_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("businessId", ""),
        doc.get("businessName", ""),
        doc.get("categoryId", ""),
        doc.get("businessSlug", ""),
        doc.get("businessDescription", ""),
        doc.get("tagline", ""),
        doc.get("aboutBrief", ""),
        doc.get("description", ""),
        doc.get("vision", ""),
        doc.get("whyChooseUs", ""),
        doc.get("address1", ""),
        doc.get("city", ""),
        doc.get("state", ""),
        doc.get("country", ""),
        doc.get("pincode", ""),
        doc.get("landmark", ""),
    ]
    return " ".join(str(p) for p in parts if p).strip().lower()


def _load_companies_sync() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(b2b_companies_table())).mappings().all()
        return [serialize_row("B2BCompanies", r) for r in rows]


async def build_index() -> None:
    """Loads all companies from SQL Server and (re)builds the in-memory
    FAISS index. Safe to call more than once — e.g. after re-running the
    import script — to pick up fresh data without restarting the server."""
    global _companies, _index

    docs = await asyncio.to_thread(_load_companies_sync)
    if not docs:
        logger.info("No B2B company rows found — search index left empty")
        _companies = []
        _index = None
        return

    model = get_model()
    texts = [_combined_text(d) for d in docs]
    vectors = model.encode(texts)
    faiss.normalize_L2(vectors)

    index = faiss.IndexFlatL2(EMBEDDING_DIM)
    index.add(np.array(vectors).astype("float32"))

    _companies = docs
    _index = index
    logger.info(f"B2B search index built with {len(docs)} companies")


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


def search_companies_sync(query: str) -> list[dict[str, Any]]:
    """Runs the exact -> partial -> AI-fallback search over the in-memory
    company cache. CPU-bound (regex + FAISS + embedding) — call this via
    asyncio.to_thread from the route so it doesn't block the event loop."""
    search_term = re.sub(r"[^a-zA-Z0-9\s]", "", query).lower().strip()
    if not search_term or not _companies:
        return []

    # Layer 1: exact match (full phrase) against name / slug / city / state
    exact_fields = ["businessName", "businessSlug", "city", "state"]
    exact_results = []
    for doc in _companies:
        if _contains_term(doc, search_term, exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                _get_matching_words(search_term, row.get("businessName", ""))
                or _get_matching_words(search_term, row.get("city", ""))
                or _get_matching_words(search_term, row.get("state", ""))
            )
            row["matchedKeyword"] = matched or search_term.title()
            exact_results.append(row)

    if exact_results:
        return sorted(exact_results, key=lambda r: r["matchPercentage"], reverse=True)

    # Layer 1.5: partial word match, weighted by which field it hit and
    # boosted for whichever query word appears first.
    words = [w for w in search_term.split() if len(w) > 2 and w not in STOP_WORDS]
    seen_names: set[str] = set()
    partial_results: list[dict[str, Any]] = []

    for word_index, word in enumerate(words):
        position_penalty = word_index * 2.0
        for doc in _companies:
            b_name = str(doc.get("businessName", ""))
            if b_name in seen_names:
                continue

            city_name = str(doc.get("city", "")).lower()
            desc_text = str(doc.get("description", "")).lower()
            cat_text = str(doc.get("categoryId", "")).lower()

            match_score = 0.0
            if word in b_name.lower():
                match_score = 90.0 - position_penalty
            elif word in cat_text:
                match_score = 85.0 - position_penalty
            elif word in desc_text:
                match_score = 80.0 - position_penalty
            elif word in city_name:
                match_score = 75.0 - position_penalty

            if word_index == 0 and match_score > 0:
                match_score = min(99.0, match_score + 10.0)

            if match_score >= 10.0:
                seen_names.add(b_name)
                row = dict(doc)
                row["matchType"] = "Partial Word Match"
                row["matchPercentage"] = round(match_score, 2)
                row["matchedKeyword"] = word.title()
                partial_results.append(row)

    if partial_results:
        return sorted(partial_results, key=lambda r: r["matchPercentage"], reverse=True)

    # Layer 2: AI fallback via FAISS similarity over the embedded company text
    if _index is None:
        return []

    model = get_model()
    query_vector = model.encode([search_term])
    faiss.normalize_L2(query_vector)
    distances, indices = _index.search(query_vector.astype("float32"), k=50)

    ai_results = []
    seen_names = set()
    for i, idx in enumerate(indices[0]):
        if idx == -1 or idx >= len(_companies):
            continue
        distance_score = distances[0][i]
        cosine_sim = 1 - (distance_score / 2)
        percentage = max(0.0, round(cosine_sim * 100, 2))
        if percentage < 10.0:
            continue

        doc = _companies[int(idx)]
        b_name = str(doc.get("businessName", ""))
        if b_name in seen_names:
            continue
        seen_names.add(b_name)

        row = dict(doc)
        row["matchType"] = "AI Similarity Match"
        row["matchPercentage"] = float(percentage)
        matched_in_name = _get_matching_words(search_term, b_name)
        row["matchedKeyword"] = (
            matched_in_name
            if matched_in_name
            else f"{_get_closest_word(search_term, b_name)} (~AI Match)"
        )
        ai_results.append(row)

    return sorted(ai_results, key=lambda r: r["matchPercentage"], reverse=True)