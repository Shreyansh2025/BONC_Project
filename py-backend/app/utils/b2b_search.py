"""
B2B company search: exact match -> priority partial-word match -> AI
(FAISS) fallback.

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

Same caching strategy as before: the company set is small, so the whole
result set is loaded into memory as a plain list and the same
three-layer scoring logic runs directly against that list. build_index()
refreshes this cache; the hourly auto-refresh loop in main.py calls it
automatically, or call it manually after data changes.
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

STOP_WORDS = {
    "in", "at", "near", "and", "for", "the", "of", "to", "company",
    "ltd", "pvt", "limited", "private", "enterprises", "industries",
}

# Ordered list of company docs — position i mirrors row i of _index, so a
# FAISS hit at position i maps directly to _companies[i].
_companies: list[dict[str, Any]] = []
_index: faiss.Index | None = None


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
    return {"count": len(_companies), "index_built": _index is not None}


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
    (re)builds the in-memory FAISS index. Called once at startup and then
    every hour by main.py's auto-refresh loop — each call does a full read
    of Company + Industry + Category + SubCategory + BusinessType +
    AddressDetails + BoncUser, so keep that in mind if refresh frequency
    ever needs to change for load reasons."""
    global _companies, _index

    docs = await asyncio.to_thread(_load_companies_sync)
    if not docs:
        logger.info("No Verified companies found — search index left empty")
        _companies = []
        _index = None
        return

    index = await asyncio.to_thread(_build_index_sync, docs)

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

    # Layer 1: exact match (full phrase) against name / slug / industry /
    # category / city. City is back in this list now that AddressDetails
    # is joined — was removed here earlier when Company alone had no
    # address columns to check.
    exact_fields = ["businessName", "businessSlug", "industryName", "categoryName", "city"]
    exact_results = []
    for doc in _companies:
        if _contains_term(doc, search_term, exact_fields):
            row = dict(doc)
            row["matchType"] = "Exact Match"
            row["matchPercentage"] = 100.0
            matched = (
                _get_matching_words(search_term, row.get("businessName", ""))
                or _get_matching_words(search_term, row.get("categoryName", ""))
                or _get_matching_words(search_term, row.get("industryName", ""))
                or _get_matching_words(search_term, row.get("city", ""))
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

            city_text = str(doc.get("city", "")).lower()
            cat_text = str(doc.get("categoryName", "")).lower()
            industry_text = str(doc.get("industryName", "")).lower()
            desc_text = str(doc.get("description", "")).lower()

            match_score = 0.0
            if word in b_name.lower():
                match_score = 90.0 - position_penalty
            elif word in city_text:
                # City match ranked just under name match — a location
                # word ("bangalore") should narrow results, not just add
                # noise to the AI embedding the way it did before this
                # join existed.
                match_score = 87.0 - position_penalty
            elif word in cat_text or word in industry_text:
                match_score = 85.0 - position_penalty
            elif word in desc_text:
                match_score = 80.0 - position_penalty

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
        if percentage < 35.0:
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