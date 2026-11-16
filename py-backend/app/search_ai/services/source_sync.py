"""Safe SQL Server source synchronization for configured search categories."""
from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.search_ai.platform.models import CategoryConfig, SQLServerSourceConfig, SearchDocument
from app.search_ai.services.category_store import replace_documents


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid SQL identifier in category source configuration")
    return f"[{value}]"


def _source_engine(source: SQLServerSourceConfig):
    connection_value = os.getenv(source.connection_env, "").strip()
    if not connection_value:
        raise RuntimeError(
            f"Source connection is not configured on the server: {source.connection_env}"
        )
    # Credentials never come from the browser or category JSON. An operator
    # provisions this environment variable on the Search AI host.
    if connection_value.lower().startswith("mssql+pyodbc://"):
        return create_engine(connection_value, pool_pre_ping=True, pool_size=1, max_overflow=1)
    return create_engine(
        URL.create("mssql+pyodbc", query={"odbc_connect": connection_value}),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=1,
    )


def sync_sqlserver_category(config: CategoryConfig, *, updated_by: str) -> int:
    if not isinstance(config.source, SQLServerSourceConfig):
        raise ValueError("This category does not use a SQL Server source")

    source = config.source
    columns = [field.source_column for field in config.fields]
    if len(columns) != len(set(columns)):
        raise ValueError("A source column can only be mapped once")

    selected = [_quote_identifier(source.id_column) + " AS [_document_id]"]
    selected.extend(
        f"{_quote_identifier(field.source_column)} AS [{field.key}]"
        for field in config.fields
    )
    qualified_table = f"{_quote_identifier(source.schema)}.{_quote_identifier(source.table)}"
    query = text(f"SELECT {', '.join(selected)} FROM {qualified_table}")

    engine = _source_engine(source)
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
    finally:
        engine.dispose()

    documents: list[SearchDocument] = []
    for row in rows:
        raw_id = row.get("_document_id")
        if raw_id is None:
            continue
        fields: dict[str, str | int | float | bool | None] = {}
        title = ""
        for field in config.fields:
            value: Any = row.get(field.key)
            if value is not None and not isinstance(value, (str, int, float, bool)):
                value = str(value)
            fields[field.key] = value
            if field.role == "title" and value is not None and not title:
                title = str(value)
        documents.append(SearchDocument(
            document_id=str(raw_id),
            title=title[:1000],
            fields=fields,
        ))

    if not documents:
        raise ValueError("Source query returned no records; refusing to replace the existing index")
    return replace_documents(config.category_id, documents, updated_by=updated_by)
