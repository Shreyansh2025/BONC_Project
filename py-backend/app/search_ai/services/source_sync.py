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


def _engine_from_environment_name(environment_name: str):
    if not re.fullmatch(r"SEARCH_SOURCE_[A-Z0-9_]{1,80}", environment_name):
        raise ValueError("Invalid source profile name")
    connection_value = os.getenv(environment_name, "").strip()
    if not connection_value:
        raise RuntimeError(f"Source connection is not configured on the server: {environment_name}")
    # Credentials never come from the browser or engine configuration. An
    # operator provisions each reusable profile on the Search AI host.
    if connection_value.lower().startswith("mssql+pyodbc://"):
        return create_engine(connection_value, pool_pre_ping=True, pool_size=1, max_overflow=1)
    return create_engine(
        URL.create("mssql+pyodbc", query={"odbc_connect": connection_value}),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=1,
    )


def _source_engine(source: SQLServerSourceConfig):
    return _engine_from_environment_name(source.connection_env)


def list_source_profiles() -> list[dict[str, str]]:
    """List configured profile names without exposing connection strings or credentials."""
    prefix = "SEARCH_SOURCE_"
    return [
        {"profile_id": name[len(prefix):], "connection_env": name}
        for name, value in sorted(os.environ.items())
        if name.startswith(prefix) and name != prefix and value.strip()
        and re.fullmatch(r"[A-Z0-9_]{1,80}", name[len(prefix):])
    ]


def discover_source_schema(profile_id: str, schema: str | None = None) -> dict[str, Any]:
    """Discover tables, columns, primary keys and declared foreign-key relationships."""
    if not re.fullmatch(r"[A-Z0-9_]{1,80}", profile_id):
        raise ValueError("Invalid source profile ID")
    if schema is not None and not _IDENTIFIER.fullmatch(schema):
        raise ValueError("Invalid SQL schema name")

    engine = _engine_from_environment_name(f"SEARCH_SOURCE_{profile_id}")
    schema_clause = "AND tables_view.TABLE_SCHEMA = :schema_name" if schema else ""
    key_schema_clause = "AND tc.TABLE_SCHEMA = :schema_name" if schema else ""
    relationship_schema_clause = (
        "WHERE fk.TABLE_SCHEMA = :schema_name OR referenced.TABLE_SCHEMA = :schema_name"
        if schema else ""
    )
    query = text(f"""
        SELECT tables_view.TABLE_SCHEMA, tables_view.TABLE_NAME, tables_view.TABLE_TYPE,
               columns_view.COLUMN_NAME, columns_view.DATA_TYPE,
               columns_view.IS_NULLABLE, columns_view.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.TABLES AS tables_view
        INNER JOIN INFORMATION_SCHEMA.COLUMNS AS columns_view
            ON tables_view.TABLE_SCHEMA = columns_view.TABLE_SCHEMA
           AND tables_view.TABLE_NAME = columns_view.TABLE_NAME
        WHERE tables_view.TABLE_TYPE IN (N'BASE TABLE', N'VIEW')
          {schema_clause}
        ORDER BY tables_view.TABLE_SCHEMA, tables_view.TABLE_NAME, columns_view.ORDINAL_POSITION
    """)
    primary_key_query = text(f"""
        SELECT tc.TABLE_SCHEMA, tc.TABLE_NAME, kcu.COLUMN_NAME, kcu.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
        INNER JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS kcu
            ON tc.CONSTRAINT_CATALOG = kcu.CONSTRAINT_CATALOG
           AND tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
           AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
           AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
           AND tc.TABLE_NAME = kcu.TABLE_NAME
        WHERE tc.CONSTRAINT_TYPE = N'PRIMARY KEY'
          {key_schema_clause}
        ORDER BY tc.TABLE_SCHEMA, tc.TABLE_NAME, kcu.ORDINAL_POSITION
    """)
    foreign_key_query = text(f"""
        SELECT
            fk.CONSTRAINT_NAME,
            fk.TABLE_SCHEMA AS FROM_SCHEMA,
            fk.TABLE_NAME AS FROM_TABLE,
            fk.COLUMN_NAME AS FROM_COLUMN,
            referenced.TABLE_SCHEMA AS TO_SCHEMA,
            referenced.TABLE_NAME AS TO_TABLE,
            referenced.COLUMN_NAME AS TO_COLUMN,
            fk.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS AS rc
        INNER JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS fk
            ON rc.CONSTRAINT_CATALOG = fk.CONSTRAINT_CATALOG
           AND rc.CONSTRAINT_SCHEMA = fk.CONSTRAINT_SCHEMA
           AND rc.CONSTRAINT_NAME = fk.CONSTRAINT_NAME
        INNER JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS referenced
            ON rc.UNIQUE_CONSTRAINT_CATALOG = referenced.CONSTRAINT_CATALOG
           AND rc.UNIQUE_CONSTRAINT_SCHEMA = referenced.CONSTRAINT_SCHEMA
           AND rc.UNIQUE_CONSTRAINT_NAME = referenced.CONSTRAINT_NAME
           AND fk.ORDINAL_POSITION = referenced.ORDINAL_POSITION
        {relationship_schema_clause}
        ORDER BY fk.TABLE_SCHEMA, fk.TABLE_NAME, fk.CONSTRAINT_NAME, fk.ORDINAL_POSITION
    """)
    parameters = {"schema_name": schema} if schema else {}
    try:
        with engine.connect() as connection:
            database_name = connection.execute(text("SELECT DB_NAME()")).scalar_one()
            rows = connection.execute(query, parameters).mappings().all()
            primary_key_rows = connection.execute(
                primary_key_query, parameters
            ).mappings().all()
            foreign_key_rows = connection.execute(
                foreign_key_query, parameters
            ).mappings().all()
    finally:
        engine.dispose()

    primary_keys: dict[tuple[str, str], list[str]] = {}
    for row in primary_key_rows:
        key = (str(row["TABLE_SCHEMA"]), str(row["TABLE_NAME"]))
        primary_keys.setdefault(key, []).append(str(row["COLUMN_NAME"]))

    foreign_key_columns: set[tuple[str, str, str]] = set()
    relationships_by_constraint: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in foreign_key_rows:
        from_schema = str(row["FROM_SCHEMA"])
        from_table = str(row["FROM_TABLE"])
        from_column = str(row["FROM_COLUMN"])
        to_schema = str(row["TO_SCHEMA"])
        to_table = str(row["TO_TABLE"])
        constraint_name = str(row["CONSTRAINT_NAME"])
        foreign_key_columns.add((from_schema, from_table, from_column))
        relationship_key = (
            from_schema, from_table, constraint_name, to_schema, to_table
        )
        relationship = relationships_by_constraint.setdefault(relationship_key, {
            "constraint_name": constraint_name,
            "from_schema": from_schema,
            "from_table": from_table,
            "to_schema": to_schema,
            "to_table": to_table,
            "columns": [],
        })
        relationship["columns"].append({
            "from_column": from_column,
            "to_column": str(row["TO_COLUMN"]),
            "ordinal_position": int(row["ORDINAL_POSITION"]),
        })

    relationships = list(relationships_by_constraint.values())
    for relationship in relationships:
        relationship["columns"].sort(key=lambda item: item["ordinal_position"])

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["TABLE_SCHEMA"]), str(row["TABLE_NAME"]))
        primary_key_columns = primary_keys.get(key, [])
        column_name = str(row["COLUMN_NAME"])
        table = tables.setdefault(key, {
            "schema": key[0],
            "name": key[1],
            "type": str(row["TABLE_TYPE"]),
            "primary_key_columns": primary_key_columns,
            "columns": [],
        })
        table["columns"].append({
            "name": column_name,
            "data_type": str(row["DATA_TYPE"]),
            "nullable": str(row["IS_NULLABLE"]).upper() == "YES",
            "is_primary_key": column_name in primary_key_columns,
            "is_foreign_key": (key[0], key[1], column_name) in foreign_key_columns,
        })

    table_items = list(tables.values())
    return {
        "profile_id": profile_id,
        "database": str(database_name or ""),
        "schemas": sorted({item["schema"] for item in table_items}),
        "tables": table_items,
        "relationships": relationships,
    }

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

    # Values are always bound parameters. Only validated identifiers are
    # interpolated into SQL, so dashboard users cannot submit raw SQL.
    where_clauses: list[str] = []
    parameters: dict[str, Any] = {"row_limit": source.max_records}
    for filter_index, source_filter in enumerate(source.filters):
        column = _quote_identifier(source_filter.column)
        operator = source_filter.operator
        if operator in {"eq", "ne"}:
            parameter = f"filter_{filter_index}"
            symbol = "=" if operator == "eq" else "<>"
            where_clauses.append(f"{column} {symbol} :{parameter}")
            parameters[parameter] = source_filter.value
        elif operator == "in":
            names = []
            for value_index, value in enumerate(source_filter.values):
                parameter = f"filter_{filter_index}_{value_index}"
                names.append(f":{parameter}")
                parameters[parameter] = value
            where_clauses.append(f"{column} IN ({', '.join(names)})")
        elif operator == "is_null":
            where_clauses.append(f"{column} IS NULL")
        elif operator == "not_null":
            where_clauses.append(f"{column} IS NOT NULL")

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = text(
        f"SELECT TOP (:row_limit) {', '.join(selected)} "
        f"FROM {qualified_table}{where_sql}"
    )

    engine = _source_engine(source)
    try:
        with engine.connect() as connection:
            rows = connection.execute(query, parameters).mappings().all()
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
    return replace_documents(config.category_id, documents, updated_by=updated_by, replace_all=True)
