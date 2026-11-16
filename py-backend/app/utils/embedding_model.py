"""
Shared SentenceTransformer singleton for B2B FAISS-backed searches.
Supports hot-swapping active fine-tuned models at runtime without server restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from app.logger import logger

BASE_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "models_config.json"

_current_model_path: str = BASE_MODEL_NAME
_model_instance = None


def _load_persisted_model_path() -> str:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("active_model", BASE_MODEL_NAME)
        except Exception as err:
            logger.error(f"Error reading model config: {err}")
    return BASE_MODEL_NAME


def _save_persisted_model_path(model_path: str) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"active_model": model_path}, f, indent=2)


# Initialize active model path from disk if present
_current_model_path = _load_persisted_model_path()


def get_active_model_name() -> str:
    global _current_model_path
    return _current_model_path


def get_model():
    global _model_instance, _current_model_path
    if _model_instance is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading SentenceTransformer: {_current_model_path}")
        _model_instance = SentenceTransformer(_current_model_path)
    return _model_instance


def warm_up_model() -> None:
    """Loads the embedding model immediately, so the cost happens at
    server startup instead of on a user's first search."""
    get_model()


def reload_active_model(new_model_path: str) -> str:
    """Hot-swaps the active model in memory with zero downtime."""
    global _model_instance, _current_model_path
    from sentence_transformers import SentenceTransformer

    logger.info(f"Hot-swapping active embedding model to: {new_model_path}")
    # Load new model before swapping reference to guarantee validity
    new_instance = SentenceTransformer(new_model_path)
    
    _model_instance = new_instance
    _current_model_path = new_model_path
    _save_persisted_model_path(new_model_path)
    logger.info(f"Active model successfully hot-swapped to: {new_model_path}")
    return new_model_path
