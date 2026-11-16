"""
Dynamic Entity Registry & Generic Search Engine.
Enables the Admin to add, upload, and search ANY custom entity (e.g. 'people',
'services', 'equipment') directly from the frontend without modifying Python backend code.

Each dynamic entity has an in-memory DynamicEntityIndex with:
- Word vocabulary (for spell correction)
- Inverted index (for instant candidate retrieval)
- FAISS vector index (for semantic AI similarity)
- Atomic state swapping for zero-downtime updates
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

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
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "entities"
REGISTRY_FILE = DATA_DIR / "registry.json"

STOP_WORDS = {"in", "at", "near", "and", "for", "the", "of", "to"}
AI_MIN_PERCENTAGE = 35.0
AI_HIGH_CONFIDENCE_PERCENTAGE = 60.0
MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI = 20


@dataclass(frozen=True)
class _EntityIndexState:
    docs: list[dict[str, Any]] = field(default_factory=list)
    index: Any | None = None
    vocabulary: Counter = field(default_factory=Counter)
    inverted_index: dict[str, set[int]] = field(default_factory=dict)


class DynamicEntityIndex:
    def __init__(self, entity_name: str, display_name: str):
        self.entity_name = entity_name
        self.display_name = display_name
        self._state = _EntityIndexState()

    def _combined_text(self, doc: dict[str, Any]) -> str:
        parts = [
            doc.get("name", ""),
            doc.get("title", ""),
            doc.get("category", ""),
            doc.get("skills", ""),
            doc.get("company", ""),
            doc.get("city", ""),
            doc.get("state", ""),
            doc.get("bio", ""),
            doc.get("description", ""),
        ]
        return " ".join(str(p) for p in parts if p).strip().lower()

    def _build_sync(self, docs: list[dict[str, Any]]):
        if not docs or faiss is None:
            return None
        try:
            model = get_model()
            texts = [self._combined_text(d) for d in docs]
            vectors = model.encode(texts)
            faiss.normalize_L2(vectors)

            index = faiss.IndexFlatL2(EMBEDDING_DIM)
            index.add(np.array(vectors).astype("float32"))
            return index
        except Exception as err:
            logger.warning(f"Could not build FAISS index for {self.entity_name}: {err}")
            return None

    async def build_index(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            self._state = _EntityIndexState()
            return

        index = await asyncio.to_thread(self._build_sync, docs)
        vocabulary = build_vocabulary(docs, self._combined_text)
        inverted = build_inverted_index(docs, self._combined_text)

        self._state = _EntityIndexState(
            docs=docs,
            index=index,
            vocabulary=vocabulary,
            inverted_index=inverted,
        )
        logger.info(f"Dynamic entity '{self.entity_name}' index built with {len(docs)} records.")

    def search_sync(self, query: str) -> list[dict[str, Any]]:
        state = self._state
        search_term = clean_term(query)
        if not search_term or not state.docs:
            return []

        search_term = correct_query(search_term, state.vocabulary)

        # Layer 1: Exact match against primary fields
        exact_fields = ["name", "title", "skills", "category", "city", "company"]
        exact_results = []
        seen_ids = set()

        candidates = candidate_indices(search_term, state.inverted_index, len(state.docs))
        for i in candidates:
            doc = state.docs[i]
            if any(contains_loose(search_term, doc.get(f, "")) for f in exact_fields):
                row = dict(doc)
                row["type"] = self.entity_name
                row["matchType"] = "Exact Match"
                row["matchPercentage"] = 100.0
                matched = (
                    get_matching_words(search_term, row.get("name", ""))
                    or get_matching_words(search_term, row.get("title", ""))
                    or get_matching_words(search_term, row.get("skills", ""))
                    or get_matching_words(search_term, row.get("city", ""))
                )
                row["matchedKeyword"] = matched or search_term.title()
                exact_results.append(row)
                seen_ids.add(str(doc.get("id", "")))

        # Layer 1.5: Partial match
        words = [
            w for w in search_term.split()
            if len(w) >= 2 and w not in STOP_WORDS and w not in FILLER_WORDS
        ]
        partial_results = []

        if words:
            all_candidates = set()
            for w in words:
                all_candidates |= candidate_indices(w, state.inverted_index, len(state.docs))

            for i in all_candidates:
                doc = state.docs[i]
                d_id = str(doc.get("id", ""))
                if d_id in seen_ids:
                    continue

                matched_words = []
                word_scores = []
                core_matched = False

                for w in words:
                    score = 0.0
                    if contains_loose(w, doc.get("name", "")):
                        score, core_matched = 90.0, True
                    elif contains_loose(w, doc.get("title", "")):
                        score, core_matched = 88.0, True
                    elif contains_loose(w, doc.get("skills", "")):
                        score, core_matched = 85.0, True
                    elif contains_loose(w, doc.get("category", "")):
                        score, core_matched = 82.0, True
                    elif contains_loose(w, doc.get("city", "")):
                        score = 80.0
                    elif contains_loose(w, doc.get("bio", "")):
                        score = 70.0

                    word_scores.append(score)
                    if score > 0:
                        matched_words.append(w)

                if not core_matched or len(matched_words) < (len(words) // 2 + 1):
                    continue

                combined_score = sum(word_scores) / len(words)
                if combined_score >= 10.0:
                    seen_ids.add(d_id)
                    row = dict(doc)
                    row["type"] = self.entity_name
                    row["matchType"] = "Partial Word Match"
                    row["matchPercentage"] = round(combined_score, 2)
                    row["matchedKeyword"] = ", ".join(w.title() for w in matched_words)
                    partial_results.append(row)

        lexical_results = exact_results + partial_results

        # Layer 2: AI / FAISS fallback
        ai_results = []
        if len(lexical_results) < MAX_LEXICAL_RESULTS_BEFORE_SKIPPING_AI and state.index is not None and faiss is not None:
            try:
                model = get_model()
                query_vector = model.encode([search_term])
                faiss.normalize_L2(query_vector)
                distances, indices = state.index.search(query_vector.astype("float32"), k=30)

                for i, idx in enumerate(indices[0]):
                    if idx == -1 or idx >= len(state.docs):
                        continue
                    distance_score = distances[0][i]
                    cosine_sim = 1 - (distance_score / 2)
                    percentage = max(0.0, round(cosine_sim * 100, 2))
                    if percentage < AI_MIN_PERCENTAGE:
                        continue

                    doc = state.docs[int(idx)]
                    d_id = str(doc.get("id", ""))
                    if d_id in seen_ids:
                        continue

                    if percentage < AI_HIGH_CONFIDENCE_PERCENTAGE and not shares_any_word(
                        search_term, self._combined_text(doc)
                    ):
                        continue

                    seen_ids.add(d_id)
                    row = dict(doc)
                    row["type"] = self.entity_name
                    row["matchType"] = "AI Similarity Match"
                    row["matchPercentage"] = float(percentage)
                    matched_in_name = get_matching_words(search_term, row.get("title", "") or row.get("name", ""))
                    row["matchedKeyword"] = matched_in_name or "Semantic Match"
                    ai_results.append(row)
            except Exception as err:
                logger.warning(f"Semantic search error for {self.entity_name}: {err}")

        combined = lexical_results + ai_results
        return sorted(combined, key=lambda r: r["matchPercentage"], reverse=True)


# Global entity index manager holding all dynamic entity index instances
_ACTIVE_ENTITY_INDEXES: dict[str, DynamicEntityIndex] = {}


def ensure_registry_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump({"entities": []}, f, indent=2)


def get_entity_registry() -> list[dict[str, Any]]:
    ensure_registry_dir()
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("entities", [])
    except Exception as err:
        logger.error(f"Error reading entity registry: {err}")
        return []


def save_entity_registry(entities: list[dict[str, Any]]) -> None:
    ensure_registry_dir()
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump({"entities": entities}, f, indent=2)


def load_entity_docs(data_file_name: str) -> list[dict[str, Any]]:
    file_path = DATA_DIR / data_file_name
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as err:
        logger.error(f"Error loading entity data {data_file_name}: {err}")
        return []


def save_entity_docs(data_file_name: str, docs: list[dict[str, Any]]) -> None:
    ensure_registry_dir()
    file_path = DATA_DIR / data_file_name
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)


async def init_dynamic_entities() -> None:
    """Loads all registered dynamic entities and builds their in-memory search indices."""
    ensure_registry_dir()
    entities = get_entity_registry()
    tasks = []

    for ent in entities:
        name = ent["name"]
        label = ent.get("label", name.title())
        data_file = ent.get("data_file") or f"{name}_data.json"
        docs = load_entity_docs(data_file)

        idx = DynamicEntityIndex(name, label)
        _ACTIVE_ENTITY_INDEXES[name] = idx
        tasks.append(idx.build_index(docs))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"Initialized {len(_ACTIVE_ENTITY_INDEXES)} dynamic entity indices: {list(_ACTIVE_ENTITY_INDEXES.keys())}")


def get_dynamic_index(entity_name: str) -> DynamicEntityIndex | None:
    return _ACTIVE_ENTITY_INDEXES.get(entity_name.lower())


def get_all_active_dynamic_entities() -> list[str]:
    return list(_ACTIVE_ENTITY_INDEXES.keys())


async def rebuild_all_dynamic_indices() -> None:
    """Rebuilds all dynamic indices when embedding model is hot-swapped."""
    tasks = []
    entities = get_entity_registry()
    for ent in entities:
        name = ent["name"]
        data_file = ent.get("data_file") or f"{name}_data.json"
        docs = load_entity_docs(data_file)
        if name in _ACTIVE_ENTITY_INDEXES:
            tasks.append(_ACTIVE_ENTITY_INDEXES[name].build_index(docs))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All dynamic entity indices rebuilt.")


async def register_new_entity(
    name: str,
    label: str,
    description: str,
    content: bytes | None,
    filename: str | None,
    column_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Registers a new entity, parses uploaded records (CSV/JSON/XLSX), and builds its search index."""
    name_clean = name.strip().lower().replace(" ", "_")
    if not name_clean.isalnum() and "_" not in name_clean:
        raise ValueError("Entity name must contain only letters, numbers, and underscores.")

    mapping = column_mapping or {}
    docs: list[dict[str, Any]] = []

    if content and filename:
        fn = filename.lower()
        if fn.endswith(".json"):
            raw = json.loads(content.decode("utf-8"))
            items = raw if isinstance(raw, list) else raw.get("data", [])
            for item in items:
                docs.append({
                    "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                    "name": str(item.get(mapping.get("name", "name")) or item.get("title", "")),
                    "title": str(item.get(mapping.get("title", "title")) or item.get("role", "") or item.get("designation", "")),
                    "category": str(item.get(mapping.get("category", "category")) or item.get("department", "")),
                    "skills": str(item.get(mapping.get("skills", "skills")) or item.get("specialization", "")),
                    "bio": str(item.get(mapping.get("bio", "bio")) or item.get("description", "")),
                    "company": str(item.get(mapping.get("company", "company")) or item.get("organization", "")),
                    "city": str(item.get(mapping.get("city", "city")) or ""),
                    "state": str(item.get(mapping.get("state", "state")) or ""),
                    "country": str(item.get(mapping.get("country", "country")) or "India"),
                    "image": item.get("image") or item.get("photo") or item.get("logo") or None,
                })
        elif fn.endswith(".csv"):
            text_str = content.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text_str))
            for row in reader:
                docs.append({
                    "id": str(row.get("id") or uuid.uuid4().hex[:8]),
                    "name": str(row.get(mapping.get("name", "name")) or row.get("Name", "")),
                    "title": str(row.get(mapping.get("title", "title")) or row.get("Title", "") or row.get("Designation", "")),
                    "category": str(row.get(mapping.get("category", "category")) or row.get("Category", "")),
                    "skills": str(row.get(mapping.get("skills", "skills")) or row.get("Skills", "")),
                    "bio": str(row.get(mapping.get("bio", "bio")) or row.get("Bio", "") or row.get("Description", "")),
                    "company": str(row.get(mapping.get("company", "company")) or row.get("Company", "")),
                    "city": str(row.get(mapping.get("city", "city")) or row.get("City", "")),
                    "state": str(row.get(mapping.get("state", "state")) or row.get("State", "")),
                    "country": str(row.get(mapping.get("country", "country")) or row.get("Country", "India")),
                    "image": row.get("image") or row.get("photo") or row.get("Image") or None,
                })

    data_file_name = f"{name_clean}_data.json"
    save_entity_docs(data_file_name, docs)

    entities = get_entity_registry()
    entities = [e for e in entities if e["name"] != name_clean]
    entities.append({
        "name": name_clean,
        "label": label or name_clean.title(),
        "description": description,
        "icon": "Layers",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_builtin": False,
        "data_file": data_file_name,
    })
    save_entity_registry(entities)

    idx = DynamicEntityIndex(name_clean, label)
    _ACTIVE_ENTITY_INDEXES[name_clean] = idx
    await idx.build_index(docs)

    return {
        "name": name_clean,
        "label": label,
        "record_count": len(docs),
        "status": "ready",
    }


def delete_dynamic_entity(name: str) -> bool:
    name_clean = name.strip().lower()
    entities = get_entity_registry()
    found = [e for e in entities if e["name"] == name_clean]
    if not found:
        return False

    entities = [e for e in entities if e["name"] != name_clean]
    save_entity_registry(entities)

    if name_clean in _ACTIVE_ENTITY_INDEXES:
        del _ACTIVE_ENTITY_INDEXES[name_clean]

    data_file = DATA_DIR / f"{name_clean}_data.json"
    if data_file.exists():
        data_file.unlink()

    return True
