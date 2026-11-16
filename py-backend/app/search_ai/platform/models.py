"""Validated configuration and document contracts for generic search categories."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFieldConfig(StrictModel):
    """Maps one source column to a searchable field in the common document."""

    key: str = Field(min_length=1, max_length=64)
    source_column: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=100)
    role: Literal["title", "skill", "location", "category", "description", "general"] = "general"
    weight: float = Field(default=1.0, ge=0.0, le=100.0)
    searchable: bool = True
    semantic: bool = True
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip().lower()
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("Field key must contain only letters, numbers and underscores")
        return value

    @field_validator("source_column")
    @classmethod
    def validate_source_column(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("Source column must be a simple SQL identifier")
        return value

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        return [item.strip().lower() for item in values if item.strip()]


class SQLServerSourceConfig(StrictModel):
    type: Literal["sqlserver"] = "sqlserver"
    # The actual credential stays on the server. The admin stores only the
    # name of an environment variable configured by an operator.
    connection_env: str = Field(pattern=r"^SEARCH_SOURCE_[A-Z0-9_]{1,80}$")
    schema: str = Field(default="dbo", min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)
    id_column: str = Field(min_length=1, max_length=128)

    @field_validator("schema", "table", "id_column")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("Schema, table and id column must be simple SQL identifiers")
        return value


class PushSourceConfig(StrictModel):
    """Source for applications that push normalized records to the ingestion API."""

    type: Literal["push"] = "push"


SourceConfig = Annotated[SQLServerSourceConfig | PushSourceConfig, Field(discriminator="type")]


class CategoryRules(StrictModel):
    lexical_enabled: bool = True
    semantic_enabled: bool = True
    semantic_min_score: float = Field(default=20.0, ge=0.0, le=100.0)
    semantic_candidate_limit: int = Field(default=50, ge=1, le=200)
    lexical_weight: float = Field(default=0.65, ge=0.0, le=1.0)
    semantic_weight: float = Field(default=0.35, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def at_least_one_search_method(self) -> "CategoryRules":
        if not self.lexical_enabled and not self.semantic_enabled:
            raise ValueError("At least one of lexical_enabled or semantic_enabled must be true")
        if self.lexical_weight + self.semantic_weight <= 0:
            raise ValueError("At least one ranking weight must be greater than zero")
        return self


class CategoryConfig(StrictModel):
    category_id: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    source: SourceConfig
    fields: list[SearchFieldConfig] = Field(min_length=1, max_length=30)
    rules: CategoryRules = Field(default_factory=CategoryRules)
    active: bool = True
    # Categories are private by default. Public endpoints must never expose
    # people or other restricted records unless an operator explicitly opts in.
    public_search_enabled: bool = False

    @field_validator("category_id")
    @classmethod
    def validate_category_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not _CATEGORY_RE.fullmatch(value):
            raise ValueError("Category ID must start with a letter and contain only letters, numbers, '-' or '_'")
        return value

    @model_validator(mode="after")
    def validate_unique_field_keys(self) -> "CategoryConfig":
        keys = [item.key for item in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Field keys must be unique within a category")
        columns = [item.source_column.lower() for item in self.fields]
        if len(columns) != len(set(columns)):
            raise ValueError("Source columns must be unique within a category")
        if not any(item.searchable for item in self.fields):
            raise ValueError("At least one field must be searchable")
        if not any(item.role == "title" for item in self.fields):
            raise ValueError("At least one field must have the title role")
        return self


class SearchDocument(StrictModel):
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=1000)
    fields: dict[str, str | int | float | bool | None]
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class IngestRequest(StrictModel):
    documents: list[SearchDocument] = Field(min_length=1, max_length=5000)
    replace_all: bool = False


class PlatformSearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=2, max_length=64)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)
