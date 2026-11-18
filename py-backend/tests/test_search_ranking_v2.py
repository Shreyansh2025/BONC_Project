"""Regression tests for the shared, category-agnostic BM25F/RRF ranker."""
import unittest
from types import SimpleNamespace as NS

from app.search_ai.services.ranking_v2 import (
    build_corpus_statistics,
    reciprocal_rank_fusion_score,
    score_bm25f,
)


def field(key, role, weight=1.0):
    return NS(key=key, role=role, weight=weight, searchable=True)


def config(*fields):
    return NS(fields=list(fields))


def document(document_id, **values):
    return {"document_id": document_id, "fields": values}


class RankingV2Tests(unittest.TestCase):
    def test_rare_specific_terms_outweigh_common_generic_terms(self):
        schema = config(field("title", "title"), field("summary", "description"))
        docs = [
            document("backend", title="Backend Developer", summary="Python API engineer"),
            document("frontend", title="Frontend Developer", summary="React UI engineer"),
            document("sales", title="Sales Developer", summary="Customer account growth"),
            document("generic", title="Developer", summary="General software role"),
        ]
        stats = build_corpus_statistics(schema, docs)
        focused, _, focused_signals = score_bm25f(
            "backend developer", schema, docs[0], stats
        )
        generic, _, generic_signals = score_bm25f(
            "backend developer", schema, docs[3], stats
        )
        self.assertGreater(focused, generic)
        self.assertGreater(focused_signals["query_term_coverage"], generic_signals["query_term_coverage"])

    def test_title_role_outweighs_description_for_same_match(self):
        schema = config(field("title", "title"), field("description", "description"))
        docs = [
            document("title-hit", title="Backend Engineer", description=""),
            document("description-hit", title="Designer", description="Backend Engineer"),
        ]
        stats = build_corpus_statistics(schema, docs)
        title_score, title_fields, _ = score_bm25f(
            "backend engineer", schema, docs[0], stats
        )
        description_score, description_fields, _ = score_bm25f(
            "backend engineer", schema, docs[1], stats
        )
        self.assertGreater(title_score, description_score)
        self.assertEqual(title_fields, ["title"])
        self.assertEqual(description_fields, ["description"])

    def test_full_query_coverage_outranks_partial_match(self):
        schema = config(field("title", "title"), field("skills", "skill"))
        docs = [
            document("complete", title="Backend Developer", skills="Python SQL"),
            document("partial", title="Backend", skills="Python"),
        ]
        stats = build_corpus_statistics(schema, docs)
        complete, _, complete_signals = score_bm25f(
            "backend developer python", schema, docs[0], stats
        )
        partial, _, partial_signals = score_bm25f(
            "backend developer python", schema, docs[1], stats
        )
        self.assertGreater(complete, partial)
        self.assertGreater(complete_signals["query_term_coverage"], partial_signals["query_term_coverage"])
        self.assertIn("developer", partial_signals["missing_terms"])

    def test_synonym_alternatives_count_as_one_concept(self):
        schema = config(field("title", "title"), field("description", "description"))
        docs = [
            document("synonym-hit", title="Server Side Developer", description="Python"),
            document("miss", title="Designer", description="Python"),
        ]
        stats = build_corpus_statistics(schema, docs)
        score, matched_fields, signals = score_bm25f(
            "python", schema, docs[0], stats,
            synonym_expansions=[{
                "matched_term": "backend",
                "alternatives": ["server side"],
            }],
        )
        self.assertGreater(score, 0)
        self.assertIn("title", matched_fields)
        self.assertIn("synonym:server side", signals["matched_terms"])

    def test_rrf_rewards_agreement_between_independent_retrievers(self):
        agreed = reciprocal_rank_fusion_score(
            1, 1, lexical_weight=0.65, semantic_weight=0.35
        )
        lexical_only = reciprocal_rank_fusion_score(
            1, None, lexical_weight=0.65, semantic_weight=0.35
        )
        self.assertGreater(agreed, lexical_only)

    def test_single_channel_fusion_is_normalized(self):
        score = reciprocal_rank_fusion_score(
            1, None, lexical_weight=1.0, semantic_weight=0.0
        )
        self.assertEqual(score, 100.0)

    def test_empty_or_stopword_only_query_has_zero_score(self):
        schema = config(field("title", "title"))
        docs = [document("one", title="Backend Developer")]
        stats = build_corpus_statistics(schema, docs)
        score, fields, signals = score_bm25f(
            "the and with", schema, docs[0], stats
        )
        self.assertEqual(score, 0.0)
        self.assertEqual(fields, [])
        self.assertEqual(signals["query_term_coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
