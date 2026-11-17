"""Small, dependency-free ranking metrics used by the Search AI evaluation toolkit."""
from __future__ import annotations

import math
from typing import Iterable


def evaluate_ranking(
    retrieved_ids: Iterable[str],
    relevant_ids: Iterable[str],
    *,
    k: int = 5,
) -> dict[str, float | int]:
    """Calculate binary-relevance Precision@k, Recall@k, MRR@k and NDCG@k.

    Document identifiers are compared exactly and case-sensitively. Duplicate
    retrieved IDs are ignored after their first occurrence. Precision@k uses
    k as its denominator, even when fewer than k results are returned.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")

    unique_retrieved: list[str] = []
    seen: set[str] = set()
    for document_id in retrieved_ids:
        normalized_id = str(document_id)
        if normalized_id not in seen:
            unique_retrieved.append(normalized_id)
            seen.add(normalized_id)

    relevant = {str(document_id) for document_id in relevant_ids}
    if not relevant:
        raise ValueError("relevant_ids must contain at least one judged-relevant ID")

    top_results = unique_retrieved[:k]
    relevant_ranks = [
        rank for rank, document_id in enumerate(top_results, start=1)
        if document_id in relevant
    ]
    relevant_found = len(relevant_ranks)
    precision = relevant_found / k
    recall = relevant_found / len(relevant)
    reciprocal_rank = 1.0 / relevant_ranks[0] if relevant_ranks else 0.0

    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in relevant_ranks
    )
    ideal_relevant = min(k, len(relevant))
    ideal_dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_relevant + 1)
    )
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0

    return {
        "precision_at_k": precision,
        "recall_at_k": recall,
        "mrr_at_k": reciprocal_rank,
        "ndcg_at_k": ndcg,
        "relevant_found_at_k": relevant_found,
        "returned_at_k": len(top_results),
    }
