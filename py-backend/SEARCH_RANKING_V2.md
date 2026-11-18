# Search Ranking Engine V2

The platform's category-driven search path now uses one shared BM25F + RRF
ranking implementation. It does not branch on category names.

## Ranking flow

1. The configured category schema determines searchable fields, field roles,
   and field weights. No People/Jobs/Articles-specific ranking code is used.
2. BM25F scores lexical evidence using category-local document frequency (IDF),
   term frequency saturation, per-field length normalization, and role boosts.
   Rare query terms contribute more than common terms.
3. Exact phrase evidence and configured synonym groups are bounded signals.
   A synonym group counts as one concept, not several artificial query tokens.
4. Semantic candidates remain a separate retrieval channel. Medium-confidence
   candidates for multi-term queries need overlap on at least two meaningful
   terms; the configured high-confidence semantic path is retained.
5. Weighted Reciprocal Rank Fusion combines lexical and semantic result ranks.
   This avoids adding raw cosine similarity to a BM25 score with a different
   scale. When only one channel is available, its own interpretable score is
   preserved.
6. The response exposes ranking signals (BM25F, ranks, term coverage, matched
   and missing terms, phrase match, and constraint boost) for diagnosis.

Recognized field values provide a small ranking hint by default. They are not
turned into hard filters. Only explicitly configured required constraints and
caller-supplied typed filters can remove documents.

## Validation and rollout

- Unit tests cover rare-term IDF, field-role weighting, query coverage,
  synonyms, reciprocal-rank fusion, and empty queries.
- Existing platform filter/API tests must continue to pass.
- Use the existing evaluation toolkit with reviewed relevant document IDs to
  compare Precision@5, Recall@5, MRR@5, and NDCG@5 across every category.
  Do not treat API success, score changes, or unjudged examples as relevance
  improvements.
- The ranking score is an ordering signal, not a probability.
- This version does not introduce a cross-encoder or new external model/API
  dependency. A second-stage reranker should be added only after judged
  benchmarks show that the current hybrid ranker leaves measurable headroom.

The legacy Company/Product search path is not modified by this change.
