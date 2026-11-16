"""
Admin REST routes for B2B Semantic Search:
- Dynamic Entity Management (add/delete/upload custom entities directly from frontend)
- Training Dataset Hub (curate/upload/delete query-match pairs)
- Retraining Execution & Live Status Polling
- Model Registry, Hot-Swapping & Side-by-Side Test Sandbox
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.logger import logger
from app.utils.admin_auth import require_admin_key
from app.utils.embedding_model import get_active_model_name, get_model
from app.utils.dynamic_entities import (
    delete_dynamic_entity,
    get_all_active_dynamic_entities,
    get_dynamic_index,
    get_entity_registry,
    register_new_entity,
)
from app.utils.model_trainer import (
    activate_checkpoint,
    get_model_for_checkpoint,
    list_checkpoints,
    trainer_manager,
)
from app.utils.training_storage import (
    DuplicatePairError,
    add_training_pair,
    bulk_import_pairs,
    delete_training_pair,
    get_dataset_stats,
    get_training_pairs,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


# ─── 1. Entity Management ──────────────────────────────────────────────────
@router.get("/entities")
async def list_entities():
    """Returns all searchable entities, including built-in (companies, products) and dynamic entities."""
    from app.utils import b2b_product_search, b2b_search, product_search
    from app.utils.dynamic_entities import load_entity_docs

    builtin_entities = [
        {
            "name": "business",
            "label": "Companies & Businesses",
            "description": "Verified B2B suppliers, manufacturers, and distributors.",
            "icon": "Building2",
            "is_builtin": True,
            "count": b2b_search.index_status().get("count", 0),
        },
        {
            "name": "product",
            "label": "Products & Services",
            "description": "Published catalog products & brochure extracted items.",
            "icon": "Package",
            "is_builtin": True,
            "count": (
                b2b_product_search.index_status().get("count", 0)
                + product_search.index_status().get("count", 0)
            ),
        },
    ]

    dynamic_entities = []
    for ent in get_entity_registry():
        docs = load_entity_docs(ent.get("data_file") or f"{ent['name']}_data.json")
        dynamic_entities.append({
            "name": ent["name"],
            "label": ent.get("label", ent["name"].title()),
            "description": ent.get("description", ""),
            "icon": ent.get("icon", "Layers"),
            "is_builtin": False,
            "count": len(docs),
            "created_at": ent.get("created_at"),
        })

    return {
        "success": True,
        "data": builtin_entities + dynamic_entities,
    }


@router.post("/entities")
async def create_entity(
    name: str = Form(...),
    label: str = Form(...),
    description: str = Form(""),
    mapping: str = Form("{}"),
    file: UploadFile | None = File(None),
):
    """Registers a new custom entity with optional CSV/JSON data file."""
    try:
        column_mapping = json.loads(mapping) if mapping else {}
        content = await file.read() if file else None
        filename = file.filename if file else None

        result = await register_new_entity(
            name=name,
            label=label,
            description=description,
            content=content,
            filename=filename,
            column_mapping=column_mapping,
        )
        return {"success": True, "data": result}
    except Exception as err:
        logger.error(f"Error registering entity: {err}")
        raise HTTPException(status_code=400, detail=str(err))


@router.delete("/entities/{entity_name}")
async def remove_entity(entity_name: str):
    """Deletes a custom dynamic entity."""
    if entity_name.lower() in ("business", "product", "all"):
        raise HTTPException(status_code=400, detail="Cannot delete built-in entity.")
    success = delete_dynamic_entity(entity_name)
    if not success:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return {"success": True, "message": f"Entity '{entity_name}' removed."}


# ─── 2. Training Dataset Hub ───────────────────────────────────────────────
class TrainingPairCreateRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=300)
    target_text: str = Field(..., min_length=2)
    entity_type: str = Field(default="product")
    relevance: float = Field(default=1.0, ge=0.0, le=1.0)


@router.get("/training-pairs")
async def list_training_pairs(
    entity_type: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    """Retrieves paginated training pairs, with optional filtering."""
    result = get_training_pairs(
        entity_type=entity_type,
        query_filter=q,
        page=page,
        page_size=page_size,
    )
    return {"success": True, "data": result}


@router.get("/training-pairs/stats")
async def training_stats():
    """Returns dataset summary statistics by entity and source."""
    return {"success": True, "data": get_dataset_stats()}


@router.post("/training-pairs")
async def create_training_pair(body: TrainingPairCreateRequest):
    """Adds a single manual training pair (relevance < 0.5 = hard negative)."""
    try:
        pair = add_training_pair(
            query=body.query,
            target_text=body.target_text,
            entity_type=body.entity_type,
            relevance=body.relevance,
            source="manual",
        )
    except DuplicatePairError as err:
        raise HTTPException(status_code=409, detail=str(err))
    return {"success": True, "data": pair}


@router.post("/training-pairs/upload")
async def upload_training_pairs(
    file: UploadFile = File(...),
    default_entity_type: str = Form("product"),
):
    """Bulk uploads training pairs from CSV, Excel, or JSON."""
    try:
        content = await file.read()
        res = await asyncio.to_thread(
            bulk_import_pairs, content, file.filename or "data.csv", default_entity_type
        )
        return {"success": True, "data": res}
    except Exception as err:
        logger.error(f"Dataset upload failed: {err}")
        raise HTTPException(status_code=400, detail=str(err))


@router.delete("/training-pairs/{pair_id}")
async def remove_training_pair(pair_id: str):
    """Deletes a specific training pair."""
    deleted = delete_training_pair(pair_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pair not found.")
    return {"success": True, "message": "Pair deleted."}


# ─── 3. Retraining Execution ───────────────────────────────────────────────
class TrainRequest(BaseModel):
    epochs: int = Field(default=3, ge=1, le=20)
    batch_size: int = Field(default=16, ge=2, le=128)
    learning_rate: float = Field(default=2e-5, gt=0)
    entities: list[str] = Field(default_factory=lambda: ["all"])
    base_model: str | None = None


@router.post("/train")
async def trigger_training(body: TrainRequest):
    """Triggers background fine-tuning of the embedding model."""
    try:
        result = trainer_manager.start_training(
            epochs=body.epochs,
            batch_size=body.batch_size,
            learning_rate=body.learning_rate,
            entities=body.entities,
            base_model_name=body.base_model,
        )
        return result
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except Exception as err:
        logger.error(f"Cannot start training: {err}")
        raise HTTPException(status_code=400, detail=str(err))


@router.get("/train/status")
async def get_training_status():
    """Polls real-time training progress, loss, and log messages."""
    return {"success": True, "data": trainer_manager.get_status()}


# ─── 4. Model Registry & Hot-Swap ──────────────────────────────────────────
@router.get("/models")
async def get_models():
    """Lists all available model checkpoints and the current active model."""
    return {"success": True, "data": list_checkpoints()}


class ActivateModelRequest(BaseModel):
    checkpoint_id: str


@router.post("/models/activate")
async def activate_model(body: ActivateModelRequest):
    """Hot-swaps the active model in memory and rebuilds all vector indices."""
    try:
        result = await activate_checkpoint(body.checkpoint_id)
        return result
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except Exception as err:
        logger.error(f"Activation failed: {err}")
        raise HTTPException(status_code=400, detail=str(err))


# ─── 5. Side-by-Side Test Sandbox ──────────────────────────────────────────
POOL_PER_ENTITY = 25
COMPARE_TOP_K = 10


class CompareSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    candidate_checkpoint_id: str | None = None
    entity_type: str = "all"


def _collect_candidate_pool(q: str, entity_type: str) -> list[dict[str, Any]]:
    """Live search results (lexical + active-model AI) with the text each doc is embedded as."""
    from app.utils import b2b_product_search, b2b_search

    pool: list[dict[str, Any]] = []

    if entity_type in ("business", "all"):
        for r in b2b_search.search_companies_sync(q)[:POOL_PER_ENTITY]:
            pool.append({
                "type": "business",
                "title": r.get("businessName"),
                "subtitle": r.get("city"),
                "matchType": r.get("matchType"),
                "matchPercentage": r.get("matchPercentage"),
                "text": b2b_search._combined_text(r),
            })

    if entity_type in ("product", "all"):
        for r in b2b_product_search.search_products_sync(q)[:POOL_PER_ENTITY]:
            pool.append({
                "type": "product",
                "title": r.get("productName"),
                "subtitle": r.get("brandName") or r.get("categoryName"),
                "matchType": r.get("matchType"),
                "matchPercentage": r.get("matchPercentage"),
                "text": b2b_product_search._combined_text(r),
            })

    for dyn_name in get_all_active_dynamic_entities():
        if entity_type in (dyn_name, "all"):
            dyn_idx = get_dynamic_index(dyn_name)
            if dyn_idx:
                for r in dyn_idx.search_sync(q)[:POOL_PER_ENTITY]:
                    pool.append({
                        "type": dyn_name,
                        "title": r.get("name") or r.get("title") or "Item",
                        "subtitle": r.get("title") or r.get("city") or "",
                        "matchType": r.get("matchType"),
                        "matchPercentage": r.get("matchPercentage"),
                        "text": dyn_idx._combined_text(r),
                    })

    # Drop exact-duplicate docs (same type + text) so rankings aren't padded.
    seen: set[tuple[str, str]] = set()
    unique = []
    for item in pool:
        key = (item["type"], item["text"])
        if item["text"] and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _rank_pool(model, q: str, pool: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """Returns (pool_index, cosine score) sorted best-first."""
    q_emb = model.encode([q], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    d_emb = model.encode(
        [p["text"] for p in pool], batch_size=64, convert_to_numpy=True,
        normalize_embeddings=True, show_progress_bar=False,
    )
    scores = (d_emb @ q_emb[0]).tolist()
    return sorted(enumerate(scores), key=lambda x: x[1], reverse=True)


def _run_comparison(q: str, entity_type: str, candidate_id: str | None) -> dict[str, Any]:
    pool = _collect_candidate_pool(q, entity_type)

    live = sorted(pool, key=lambda x: x.get("matchPercentage") or 0, reverse=True)[:8]
    live_results = [{k: v for k, v in item.items() if k != "text"} for item in live]

    comparison = None
    if candidate_id:
        candidate_model = get_model_for_checkpoint(candidate_id)  # validates the id even if pool is empty
        active_rank = _rank_pool(get_model(), q, pool) if pool else []
        cand_rank = _rank_pool(candidate_model, q, pool) if pool else []
        active_pos = {idx: r for r, (idx, _) in enumerate(active_rank)}

        def fmt(ranking, include_delta: bool):
            out = []
            for r, (idx, score) in enumerate(ranking[:COMPARE_TOP_K]):
                item = pool[idx]
                row = {
                    "type": item["type"],
                    "title": item["title"],
                    "subtitle": item["subtitle"],
                    "rank": r + 1,
                    "score": round(max(0.0, float(score)) * 100, 1),
                }
                if include_delta:
                    row["active_rank"] = active_pos[idx] + 1
                    row["rank_change"] = active_pos[idx] - r  # positive = moved up
                out.append(row)
            return out

        comparison = {
            "candidate_checkpoint_id": candidate_id,
            "pool_size": len(pool),
            "active": fmt(active_rank, include_delta=False),
            "candidate": fmt(cand_rank, include_delta=True),
            "note": (
                "Both models re-rank the same pool of live search results. Documents only the "
                "candidate would retrieve on its own appear once it is activated."
            ),
        }

    return {
        "success": True,
        "query": q,
        "active_model": get_active_model_name(),
        "active_model_results": live_results,
        "comparison": comparison,
    }


@router.post("/test-query")
async def compare_search(body: CompareSearchRequest):
    """Runs a test query; with candidate_checkpoint_id, compares active vs candidate rankings."""
    q = body.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query is empty.")
    try:
        return await asyncio.to_thread(_run_comparison, q, body.entity_type, body.candidate_checkpoint_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))
