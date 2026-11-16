"""
Training dataset storage and management for B2B Semantic Search.
Stores query-to-target training pairs persistently in JSON storage,
with support for bulk CSV/Excel/JSON imports, manual CRUD, and dynamic entity-type tagging.

Relevance semantics (used by the trainer):
- relevance >= POSITIVE_THRESHOLD  -> positive pair (query should retrieve target)
- relevance <  POSITIVE_THRESHOLD  -> hard negative (query should NOT retrieve target)
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.logger import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "training"
PAIRS_FILE = DATA_DIR / "training_pairs.json"

POSITIVE_THRESHOLD = 0.5
MAX_REPORTED_ERRORS = 10

# Serializes every read-modify-write of PAIRS_FILE so concurrent requests
# (e.g. an upload racing a delete) can't drop each other's changes.
_store_lock = threading.RLock()

_QUERY_KEYS = ("query", "searchtext", "search_text", "q")
_TARGET_KEYS = ("target_text", "targettext", "target", "match", "text", "name")
_ENTITY_KEYS = ("entity_type", "entitytype", "type")
_RELEVANCE_KEYS = ("relevance", "score", "label")


class DuplicatePairError(ValueError):
    """Raised when a pair with the same query/target/entity already exists."""


def ensure_storage_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PAIRS_FILE.exists():
        save_all_pairs([])


def load_all_pairs() -> list[dict[str, Any]]:
    with _store_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not PAIRS_FILE.exists():
            return []
        try:
            with open(PAIRS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as err:
            logger.error(f"Error loading training pairs: {err}")
            return []


def save_all_pairs(pairs: list[dict[str, Any]]) -> None:
    """Atomic write: a crash mid-write can never leave a truncated JSON file."""
    with _store_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = PAIRS_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(pairs, f, indent=2)
        os.replace(tmp_path, PAIRS_FILE)


def _dedup_key(query: str, target_text: str, entity_type: str) -> tuple[str, str, str]:
    return (
        " ".join(query.lower().split()),
        " ".join(target_text.lower().split()),
        entity_type.strip().lower(),
    )


def _pair_key(p: dict[str, Any]) -> tuple[str, str, str]:
    return _dedup_key(p.get("query", ""), p.get("target_text", ""), p.get("entity_type", ""))


def _parse_relevance(value: Any) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 1.0
    rel = float(value)  # raises ValueError on junk -> row is reported as skipped
    if rel != rel:  # NaN
        raise ValueError("relevance is NaN")
    return min(1.0, max(0.0, rel))


def _make_pair(query: str, target_text: str, entity_type: str, relevance: float, source: str) -> dict[str, Any]:
    return {
        "id": f"pair-{uuid.uuid4().hex[:10]}",
        "query": query.strip(),
        "target_text": target_text.strip(),
        "entity_type": (entity_type or "product").strip().lower(),
        "relevance": float(relevance),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def get_training_pairs(
    entity_type: str | None = None,
    query_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    pairs = load_all_pairs()

    if entity_type and entity_type.lower() != "all":
        pairs = [p for p in pairs if p.get("entity_type", "").lower() == entity_type.lower()]

    if query_filter:
        qf = query_filter.lower().strip()
        pairs = [
            p for p in pairs
            if qf in p.get("query", "").lower() or qf in p.get("target_text", "").lower()
        ]

    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total_count = len(pairs)
    offset = (page - 1) * page_size
    paginated = pairs[offset : offset + page_size]

    return {
        "items": paginated,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
    }


def add_training_pair(
    query: str,
    target_text: str,
    entity_type: str = "product",
    relevance: float = 1.0,
    source: str = "manual",
) -> dict[str, Any]:
    new_pair = _make_pair(query, target_text, entity_type, _parse_relevance(relevance), source)
    with _store_lock:
        pairs = load_all_pairs()
        key = _pair_key(new_pair)
        if any(_pair_key(p) == key for p in pairs):
            raise DuplicatePairError("An identical training pair already exists for this entity.")
        pairs.insert(0, new_pair)
        save_all_pairs(pairs)
    return new_pair


def delete_training_pair(pair_id: str) -> bool:
    with _store_lock:
        pairs = load_all_pairs()
        remaining = [p for p in pairs if p.get("id") != pair_id]
        if len(remaining) < len(pairs):
            save_all_pairs(remaining)
            return True
    return False


def get_dataset_stats() -> dict[str, Any]:
    pairs = load_all_pairs()
    entity_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    positives = 0

    for p in pairs:
        etype = p.get("entity_type", "other").lower()
        entity_counts[etype] = entity_counts.get(etype, 0) + 1

        src = p.get("source", "manual")
        source_counts[src] = source_counts.get(src, 0) + 1

        if float(p.get("relevance", 1.0)) >= POSITIVE_THRESHOLD:
            positives += 1

    return {
        "total_pairs": len(pairs),
        "positive_pairs": positives,
        "negative_pairs": len(pairs) - positives,
        "by_entity": entity_counts,
        "by_source": source_counts,
    }


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return v
    return None


def _iter_raw_rows(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Returns every data row as a dict with lower-cased, stripped header keys."""
    name = filename.lower()

    if name.endswith(".json"):
        raw = json.loads(content.decode("utf-8-sig"))
        items = raw if isinstance(raw, list) else raw.get("pairs", [])
        return [
            {str(k).strip().lower(): v for k, v in item.items()}
            for item in items
            if isinstance(item, dict)
        ]

    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return [
            {str(k).strip().lower(): v for k, v in row.items() if k is not None}
            for row in reader
        ]

    if name.endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        sheet = wb.active
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = [str(h or "").strip().lower() for h in next(rows)]
        except StopIteration:
            return []
        return [dict(zip(headers, values)) for values in rows]

    raise ValueError("Unsupported file type. Upload a .csv, .xlsx, or .json file.")


def bulk_import_pairs(content: bytes, filename: str, default_entity_type: str = "product") -> dict[str, Any]:
    """Imports training pairs from CSV, Excel, or JSON content.

    Bad rows are skipped and reported instead of failing the whole upload;
    rows that duplicate an existing pair (or an earlier row in the file) are skipped.
    """
    raw_rows = _iter_raw_rows(content, filename)
    source = f"upload:{filename}"

    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped = 0

    for idx, row in enumerate(raw_rows, start=2):  # row 1 is the header in CSV/XLSX
        q = _first_value(row, _QUERY_KEYS)
        t = _first_value(row, _TARGET_KEYS)
        if not q or not t:
            if any(v not in (None, "") for v in row.values()):  # ignore fully blank rows silently
                skipped += 1
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append(f"Row {idx}: missing query or target_text")
            continue
        try:
            rel = _parse_relevance(_first_value(row, _RELEVANCE_KEYS))
        except (TypeError, ValueError):
            skipped += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"Row {idx}: invalid relevance value")
            continue
        etype = str(_first_value(row, _ENTITY_KEYS) or default_entity_type)
        candidates.append(_make_pair(str(q), str(t), etype, rel, source))

    if not candidates and not raw_rows:
        raise ValueError("The file contains no data rows.")
    if not candidates:
        raise ValueError(
            "No valid query-target pairs found in file. Ensure columns 'query' and 'target_text' exist. "
            + ("; ".join(errors) if errors else "")
        )

    with _store_lock:
        current = load_all_pairs()
        seen = {_pair_key(p) for p in current}
        imported: list[dict[str, Any]] = []
        duplicates = 0
        for pair in candidates:
            key = _pair_key(pair)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            imported.append(pair)

        combined = imported + current
        if imported:
            save_all_pairs(combined)

    return {
        "imported_count": len(imported),
        "duplicate_count": duplicates,
        "skipped_count": skipped,
        "errors": errors,
        "total_count": len(combined),
    }
