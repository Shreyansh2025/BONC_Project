"""
Shared SentenceTransformer singleton for the two B2B FAISS-backed searches
(companies in b2b_search.py, catalog products in b2b_product_search.py).
Both use the same model — loading it once here instead of once per module
avoids holding two copies of the same ~90MB model in memory.
"""

from functools import lru_cache

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def warm_up_model() -> None:
    """Loads the embedding model immediately, so the cost happens at
    server startup instead of on a user's first search."""
    get_model()