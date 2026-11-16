"""
Background fine-tuning engine for the B2B semantic search embedding model.
Uses SentenceTransformers + MultipleNegativesRankingLoss (with optional hard negatives).
Tracks live progress, loss, logs, evaluation metrics, and checkpoints for the Admin Dashboard.

Training data semantics:
- Pairs with relevance >= POSITIVE_THRESHOLD are positives (query -> target).
- Pairs with relevance <  POSITIVE_THRESHOLD are hard negatives for their query;
  positives whose query has a hard negative are trained as (query, pos, neg) triplets.

Evaluation:
- Positive queries are split into train / held-out validation (by query, so the
  same query never appears on both sides). With too few queries, evaluation
  falls back to the training queries and is flagged as optimistic.
- Recall@1, Recall@5 and MRR@10 are computed for the starting model and the
  fine-tuned model on the same queries, against a corpus of every target text
  in the selected entities.
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.logger import logger
from app.utils.embedding_model import (
    BASE_MODEL_NAME,
    get_active_model_name,
    get_model,
    reload_active_model,
)
from app.utils.training_storage import POSITIVE_THRESHOLD, load_all_pairs

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"
CHECKPOINTS_DIR = MODELS_DIR / "checkpoints"
BASE_CHECKPOINT_ID = "base-minilm"

MIN_POSITIVE_PAIRS = 4
MIN_QUERIES_FOR_HOLDOUT = 10
VAL_FRACTION = 0.15
WARMUP_FRACTION = 0.1
MAX_GRAD_NORM = 1.0


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


# ─── Evaluation ──────────────────────────────────────────────────────────────
def evaluate_retrieval(model, eval_pairs: list[dict[str, Any]], corpus_texts: list[str]) -> dict[str, Any]:
    """Recall@1 / Recall@5 / MRR@10 of `model` retrieving each query's positive targets."""
    corpus = list(dict.fromkeys(t.strip() for t in corpus_texts if t and t.strip()))
    corpus_index = {_norm(t): i for i, t in enumerate(corpus)}

    groups: dict[str, dict[str, Any]] = {}
    for p in eval_pairs:
        key = _norm(p["query"])
        g = groups.setdefault(key, {"query": p["query"].strip(), "relevant": set()})
        idx = corpus_index.get(_norm(p["target_text"]))
        if idx is not None:
            g["relevant"].add(idx)
    groups = {k: g for k, g in groups.items() if g["relevant"]}

    if not groups or not corpus:
        return {"recall_at_1": 0.0, "recall_at_5": 0.0, "mrr_at_10": 0.0, "num_queries": 0, "corpus_size": len(corpus)}

    queries = [g["query"] for g in groups.values()]
    q_emb = model.encode(queries, batch_size=64, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    c_emb = model.encode(corpus, batch_size=64, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    sims = q_emb @ c_emb.T

    k = min(10, len(corpus))
    top = np.argsort(-sims, axis=1)[:, :k]

    r1 = r5 = mrr = 0.0
    for row, g in zip(top, groups.values()):
        rel = g["relevant"]
        if row[0] in rel:
            r1 += 1
        if any(i in rel for i in row[:5]):
            r5 += 1
        for rank, i in enumerate(row):
            if i in rel:
                mrr += 1.0 / (rank + 1)
                break

    n = len(groups)
    return {
        "recall_at_1": round(r1 / n, 4),
        "recall_at_5": round(r5 / n, 4),
        "mrr_at_10": round(mrr / n, 4),
        "num_queries": n,
        "corpus_size": len(corpus),
    }


def _split_by_query(positives: list[dict[str, Any]], seed: int = 13) -> tuple[list, list, str]:
    queries = sorted({_norm(p["query"]) for p in positives})
    if len(queries) < MIN_QUERIES_FOR_HOLDOUT:
        return positives, positives, "train"

    rng = random.Random(seed)
    rng.shuffle(queries)
    n_val = max(2, int(round(len(queries) * VAL_FRACTION)))
    val_queries = set(queries[:n_val])
    train = [p for p in positives if _norm(p["query"]) not in val_queries]
    val = [p for p in positives if _norm(p["query"]) in val_queries]
    return train, val, "holdout"


def _num_batches(n: int, batch_size: int) -> int:
    """Batches of size < 2 are dropped (MNRL needs in-batch negatives)."""
    full, rem = divmod(n, batch_size)
    return full + (1 if rem >= 2 else 0)


# ─── Training Manager ────────────────────────────────────────────────────────
class TrainingManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.status: str = "idle"  # "idle" | "running" | "completed" | "failed"
        self.phase: str = "idle"  # "preparing" | "evaluating_base" | "training" | "evaluating" | "saving"
        self.progress: float = 0.0  # 0.0 to 1.0
        self.current_epoch: int = 0
        self.total_epochs: int = 3
        self.current_loss: float = 0.0
        self.loss_history: list[dict[str, Any]] = []
        self.logs: list[dict[str, str]] = []
        self.error: str | None = None
        self.evaluation: dict[str, Any] | None = None
        self.last_trained_checkpoint: str | None = None
        self._thread: threading.Thread | None = None

        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)

    def log(self, message: str, level: str = "info") -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        }
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 500:
                self.logs.pop(0)
        (logger.error if level == "error" else logger.info)(f"[Trainer] {message}")

    def get_status(self) -> dict[str, Any]:
        active = get_active_model_name()
        with self._lock:
            return {
                "status": self.status,
                "phase": self.phase,
                "progress": round(self.progress * 100, 1),
                "current_epoch": self.current_epoch,
                "total_epochs": self.total_epochs,
                "current_loss": round(self.current_loss, 4),
                "loss_history": list(self.loss_history),
                "logs": list(self.logs[-100:]),
                "error": self.error,
                "evaluation": self.evaluation,
                "active_model": active,
                "last_trained_checkpoint": self.last_trained_checkpoint,
            }

    def start_training(
        self,
        epochs: int = 3,
        batch_size: int = 16,
        learning_rate: float = 2e-5,
        entities: list[str] | None = None,
        base_model_name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.status == "running":
                raise RuntimeError("A training run is already in progress.")

            self.status = "running"
            self.phase = "preparing"
            self.progress = 0.0
            self.current_epoch = 0
            self.total_epochs = epochs
            self.current_loss = 0.0
            self.loss_history = []
            self.logs = []
            self.error = None
            self.evaluation = None

        self._thread = threading.Thread(
            target=self._run_training_worker,
            args=(epochs, batch_size, learning_rate, entities, base_model_name),
            daemon=True,
        )
        self._thread.start()
        return {"success": True, "message": "Training job started."}

    def _safe_eval(self, model, label: str, eval_pairs, corpus) -> dict[str, Any] | None:
        try:
            m = evaluate_retrieval(model, eval_pairs, corpus)
            self.log(
                f"{label}: Recall@1 {m['recall_at_1']:.3f} | Recall@5 {m['recall_at_5']:.3f} | "
                f"MRR@10 {m['mrr_at_10']:.3f} ({m['num_queries']} queries, corpus {m['corpus_size']})"
            )
            return m
        except Exception as err:
            self.log(f"{label} evaluation failed (training continues): {err}", level="error")
            return None

    def _run_training_worker(
        self,
        epochs: int,
        batch_size: int,
        learning_rate: float,
        entities: list[str] | None,
        base_model_name: str | None,
    ) -> None:
        start_time = time.time()
        try:
            self.log("Initializing dataset for fine-tuning...")
            all_pairs = [p for p in load_all_pairs() if p.get("query") and p.get("target_text")]

            entity_list = [e.lower() for e in (entities or [])]
            if entity_list and "all" not in entity_list:
                target_entities = set(entity_list)
                selected = [p for p in all_pairs if p.get("entity_type", "").lower() in target_entities]
            else:
                selected = all_pairs
                entity_list = ["all"]

            positives = [p for p in selected if float(p.get("relevance", 1.0)) >= POSITIVE_THRESHOLD]
            negatives = [p for p in selected if float(p.get("relevance", 1.0)) < POSITIVE_THRESHOLD]

            if len(positives) < MIN_POSITIVE_PAIRS:
                raise ValueError(
                    f"Insufficient positive training pairs ({len(positives)} found). "
                    f"At least {MIN_POSITIVE_PAIRS} pairs with relevance >= {POSITIVE_THRESHOLD} are required."
                )

            train_pos, val_pos, eval_split = _split_by_query(positives)
            neg_by_query: dict[str, list[str]] = defaultdict(list)
            for p in negatives:
                neg_by_query[_norm(p["query"])].append(p["target_text"].strip())

            corpus = [p["target_text"] for p in selected]
            self.log(
                f"Dataset: {len(positives)} positives, {len(negatives)} hard negatives across entities {entity_list}. "
                f"Train {len(train_pos)} / eval {len(val_pos)} ({eval_split})."
            )
            if eval_split == "train":
                self.log(
                    f"Fewer than {MIN_QUERIES_FOR_HOLDOUT} distinct queries — evaluating on training queries "
                    "(metrics will be optimistic). Add more pairs for a real held-out score.",
                    level="warning",
                )

            base_to_load = base_model_name or get_active_model_name() or BASE_MODEL_NAME
            self.log(f"Loading starting model: {base_to_load}...")

            import torch
            from sentence_transformers import InputExample, SentenceTransformer, losses
            from sentence_transformers.util import batch_to_device
            from transformers import get_linear_schedule_with_warmup

            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.log(f"Computation device: {device.upper()}")
            model = SentenceTransformer(base_to_load, device=device)

            self._set(phase="evaluating_base")
            base_metrics = self._safe_eval(model, "Starting model", val_pos, corpus)

            # Build per-epoch examples: triplets where a hard negative exists, plain pairs otherwise.
            rng = random.Random(42)
            n_triplets = sum(1 for p in train_pos if neg_by_query.get(_norm(p["query"])))
            use_triplets = n_triplets >= 2  # a lone triplet would be dropped every epoch

            def build_groups() -> tuple[list[InputExample], list[InputExample]]:
                pairs_g, trip_g = [], []
                for p in train_pos:
                    negs = neg_by_query.get(_norm(p["query"])) if use_triplets else None
                    if negs:
                        trip_g.append(InputExample(texts=[p["query"], p["target_text"], rng.choice(negs)]))
                    else:
                        pairs_g.append(InputExample(texts=[p["query"], p["target_text"]]))
                return pairs_g, trip_g

            pairs_g, trip_g = build_groups()
            eff_bs = max(2, min(batch_size, len(train_pos)))
            steps_per_epoch = _num_batches(len(pairs_g), eff_bs) + _num_batches(len(trip_g), eff_bs)
            if steps_per_epoch == 0:
                raise ValueError("Not enough training examples to form a batch of at least 2.")
            total_steps = epochs * steps_per_epoch
            warmup_steps = max(1, int(total_steps * WARMUP_FRACTION))

            train_loss = losses.MultipleNegativesRankingLoss(model).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
            scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)

            self.log(
                f"Starting training: {epochs} epochs x {steps_per_epoch} steps, batch size {eff_bs}, "
                f"lr {learning_rate}, warmup {warmup_steps} steps, {len(trip_g)} triplets / {len(pairs_g)} pairs."
            )
            self._set(phase="training")
            model.train()
            step = 0
            log_every = max(1, steps_per_epoch // 3)

            for epoch in range(1, epochs + 1):
                self._set(current_epoch=epoch)
                self.log(f"--- Epoch {epoch}/{epochs} started ---")

                if epoch > 1:
                    pairs_g, trip_g = build_groups()  # resample hard negatives each epoch
                batches: list[list[InputExample]] = []
                for group in (pairs_g, trip_g):
                    rng.shuffle(group)
                    for i in range(0, len(group), eff_bs):
                        chunk = group[i : i + eff_bs]
                        if len(chunk) >= 2:
                            batches.append(chunk)
                rng.shuffle(batches)

                for batch_idx, chunk in enumerate(batches):
                    features, labels = model.smart_batching_collate(chunk)
                    features = [batch_to_device(f, device) for f in features]
                    labels = labels.to(device)

                    optimizer.zero_grad()
                    loss_value = train_loss(features, labels)
                    loss_value.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
                    optimizer.step()
                    scheduler.step()

                    step += 1
                    loss_float = float(loss_value.item())
                    record = batch_idx % log_every == 0 or batch_idx == len(batches) - 1
                    with self._lock:
                        self.current_loss = loss_float
                        self.progress = step / total_steps
                        if record:
                            self.loss_history.append({"epoch": epoch, "step": step, "loss": round(loss_float, 4)})
                    if record:
                        self.log(f"Epoch {epoch}/{epochs} | Batch {batch_idx + 1}/{len(batches)} | Loss: {loss_float:.4f}")

            model.eval()
            self._set(phase="evaluating")
            tuned_metrics = self._safe_eval(model, "Fine-tuned model", val_pos, corpus)

            evaluation = {
                "split": eval_split,
                "base": base_metrics,
                "finetuned": tuned_metrics,
            }

            checkpoint_id = f"ckpt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            output_path = CHECKPOINTS_DIR / checkpoint_id
            self._set(phase="saving")
            self.log(f"Saving checkpoint to {output_path.name}...")
            model.save(str(output_path))

            elapsed_sec = round(time.time() - start_time, 1)
            with self._lock:
                final_loss = round(self.current_loss, 4)
            meta = {
                "id": checkpoint_id,
                "name": f"Fine-tuned {checkpoint_id}",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "base_model": base_to_load,
                "epochs": epochs,
                "batch_size": eff_bs,
                "learning_rate": learning_rate,
                "pair_count": len(train_pos),
                "negative_count": len(negatives),
                "entities_trained": entity_list,
                "final_loss": final_loss,
                "evaluation": evaluation,
                "duration_seconds": elapsed_sec,
                "path": str(output_path),
            }
            with open(output_path / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            self._set(
                status="completed",
                phase="idle",
                progress=1.0,
                evaluation=evaluation,
                last_trained_checkpoint=checkpoint_id,
            )
            self.log(f"Finished training in {elapsed_sec}s. Checkpoint saved as '{checkpoint_id}'.")

        except Exception as err:
            logger.exception("Training worker failed")
            self._set(status="failed", phase="idle", error=str(err))
            self.log(f"Training failed: {err}", level="error")


trainer_manager = TrainingManager()


# ─── Checkpoint Registry ─────────────────────────────────────────────────────
def resolve_checkpoint_path(checkpoint_id: str) -> str:
    """Maps a checkpoint id to a loadable model path; rejects path traversal."""
    if checkpoint_id == BASE_CHECKPOINT_ID:
        return BASE_MODEL_NAME
    target_dir = (CHECKPOINTS_DIR / checkpoint_id).resolve()
    if target_dir.parent != CHECKPOINTS_DIR.resolve() or not (target_dir / "metadata.json").exists():
        raise FileNotFoundError(f"Checkpoint '{checkpoint_id}' not found.")
    return str(target_dir)


def _is_active(path: str) -> bool:
    active = get_active_model_name()
    if path == BASE_MODEL_NAME or active == BASE_MODEL_NAME:
        return active == path
    try:
        return Path(active).resolve() == Path(path).resolve()
    except Exception:
        return False


def list_checkpoints() -> list[dict[str, Any]]:
    checkpoints = [
        {
            "id": BASE_CHECKPOINT_ID,
            "name": "Base Model (all-MiniLM-L6-v2)",
            "created_at": "2026-01-01T00:00:00Z",
            "base_model": "sentence-transformers/all-MiniLM-L6-v2",
            "epochs": 0,
            "pair_count": 0,
            "entities_trained": ["base"],
            "final_loss": 0.0,
            "evaluation": None,
            "path": BASE_MODEL_NAME,
            "is_active": _is_active(BASE_MODEL_NAME),
            "is_base": True,
        }
    ]

    if CHECKPOINTS_DIR.exists():
        for d in sorted(CHECKPOINTS_DIR.iterdir(), reverse=True):
            if d.is_dir() and (d / "metadata.json").exists():
                try:
                    with open(d / "metadata.json", "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["is_active"] = _is_active(str(d))
                    meta["is_base"] = False
                    checkpoints.append(meta)
                except Exception as err:
                    logger.error(f"Error loading checkpoint {d.name}: {err}")

    return checkpoints


# Single-slot cache so repeated sandbox comparisons don't reload the candidate each time.
_candidate_lock = threading.Lock()
_candidate_cache: dict[str, Any] = {"path": None, "model": None}


def get_model_for_checkpoint(checkpoint_id: str):
    """Returns a loaded model for the checkpoint (the live model if it is already active)."""
    path = resolve_checkpoint_path(checkpoint_id)
    if _is_active(path):
        return get_model()
    with _candidate_lock:
        if _candidate_cache["path"] != path:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading candidate model for comparison: {path}")
            _candidate_cache["model"] = SentenceTransformer(path)
            _candidate_cache["path"] = path
        return _candidate_cache["model"]


async def activate_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    """Hot-swaps the active model and triggers index rebuilding across all search systems."""
    from app.utils import b2b_product_search, b2b_search, product_search
    from app.utils.dynamic_entities import rebuild_all_dynamic_indices

    target_path = resolve_checkpoint_path(checkpoint_id)

    # 1. Hot-swap the model in memory (off the event loop — loading takes seconds)
    await asyncio.to_thread(reload_active_model, target_path)
    with _candidate_lock:
        if _candidate_cache["path"] == target_path:
            _candidate_cache["path"] = _candidate_cache["model"] = None

    # 2. Concurrently rebuild all search vector indices
    results = await asyncio.gather(
        b2b_search.build_index(),
        b2b_product_search.build_index(),
        product_search.build_index(),
        rebuild_all_dynamic_indices(),
        return_exceptions=True,
    )
    failures = [str(r) for r in results if isinstance(r, Exception)]
    for f in failures:
        logger.error(f"Error rebuilding search index after model swap: {f}")

    return {
        "success": True,
        "message": f"Activated model '{checkpoint_id}' and rebuilt search indices."
        + (f" {len(failures)} index rebuild(s) failed — see server logs." if failures else ""),
        "active_model": target_path,
    }
