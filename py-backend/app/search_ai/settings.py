"""Validated Search AI settings schema.

This module defines the safe configuration shape for a future admin API.
It deliberately does not load or persist settings and does not alter current
search behavior until the service integration step wires these values in.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _RulesModel(BaseModel):
    """Shared strict/immutable defaults for nested settings groups."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanySearchRules(_RulesModel):
    semantic_min_score: float = Field(
        default=35.0, ge=0.0, le=100.0,
        description="Discard company semantic matches below this score.",
    )
    semantic_high_confidence_score: float = Field(
        default=60.0, ge=0.0, le=100.0,
        description="At this score, company semantic matches no longer require a shared core word.",
    )

    @model_validator(mode="after")
    def high_confidence_must_exceed_minimum(self) -> "CompanySearchRules":
        if self.semantic_high_confidence_score <= self.semantic_min_score:
            raise ValueError("semantic_high_confidence_score must be greater than semantic_min_score")
        return self


class B2BProductSearchRules(_RulesModel):
    semantic_min_score: float = Field(
        default=40.0, ge=0.0, le=100.0,
        description="Discard B2B catalogue semantic matches below this score.",
    )
    semantic_high_confidence_score: float = Field(
        default=65.0, ge=0.0, le=100.0,
        description="At this score, B2B catalogue semantic matches no longer require a shared word.",
    )

    @model_validator(mode="after")
    def high_confidence_must_exceed_minimum(self) -> "B2BProductSearchRules":
        if self.semantic_high_confidence_score <= self.semantic_min_score:
            raise ValueError("semantic_high_confidence_score must be greater than semantic_min_score")
        return self


class LocalProductSearchRules(_RulesModel):
    semantic_min_score: float = Field(
        default=10.0, ge=0.0, le=100.0,
        description="Discard BrochureIQ-local product semantic matches below this score.",
    )


class SearchRulesSettings(_RulesModel):
    """Settings candidates for the Search AI admin API, with current defaults."""

    semantic_candidate_limit: int = Field(
        default=50, ge=10, le=100,
        description="Maximum FAISS candidates requested per semantic search.",
    )
    lexical_result_threshold: int = Field(
        default=20, ge=1, le=100,
        description="Run the semantic layer when lexical results are below this count.",
    )
    company: CompanySearchRules = Field(default_factory=CompanySearchRules)
    b2b_product: B2BProductSearchRules = Field(default_factory=B2BProductSearchRules)
    local_product: LocalProductSearchRules = Field(default_factory=LocalProductSearchRules)
