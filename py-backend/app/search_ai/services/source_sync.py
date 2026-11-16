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


_SENSITIVE_COLUMN_NAMES = {
    "password", "passwd", "pwd", "secret", "token", "accesstoken",
    "refreshtoken", "credential", "credentials", "hash", "salt",
    "aadhaar", "aadhar", "pan", "passport", "ssn", "socialsecuritynumber",
    "bankaccount", "accountnumber", "creditcard", "cvv", "otp",
    "privatekey", "email", "emailid", "phonenumber", "mobilenumber",
    "mobile", "mobileno", "phone", "dateofbirth", "dob",
}

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "title": (
        "title", "name", "fullname", "displayname", "firstname", "headline", "subject",
        "productname", "companyname", "articletitle", "businessname",
    ),
    "skill": (
        "skill", "skills", "expertise", "technology", "technologies",
        "techstack", "stack", "keywords", "tags",
    ),
    "location": (
        "location", "city", "state", "country", "region", "address",
        "address1", "pincode", "postalcode",
    ),
    "category": (
        "category", "categoryname", "type", "profession", "industry",
        "articletype", "productcategory",
    ),
    "description": (
        "description", "bio", "about", "summary", "content", "body",
        "profiledescription", "tagline", "overview",
    ),
}

_ROLE_DEFAULTS = {
    "title": (5.0, ["name", "title"]),
    "skill": (4.0, ["expertise", "technology"]),
    "location": (1.5, ["city", "place"]),
    "category": (2.0, ["type", "category"]),
    "description": (2.0, ["about", "details"]),
    "general": (1.0, []),
}

_SEARCHABLE_TYPES = {
    "char", "nchar", "varchar", "nvarchar", "text", "ntext",
    "uniqueidentifier", "int", "bigint", "smallint", "tinyint",
    "decimal", "numeric", "float", "real", "money", "smallmoney",
}


def _normalise_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _field_key_from_column(column_name: str, used_keys: set[str]) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column_name).lower()
    key = re.sub(r"[^a-z0-9_]", "_", snake).strip("_") or "field"
    if not key[0].isalpha():
        key = f"field_{key}"
    base = key[:64]
    key = base
    suffix = 2
    while key in used_keys:
        ending = f"_{suffix}"
        key = f"{base[:64 - len(ending)]}{ending}"
        suffix += 1
    used_keys.add(key)
    return key


def build_source_mapping_suggestions(
    catalog: dict[str, Any],
    *,
    schema: str,
    table: str,
) -> dict[str, Any]:
    """Build conservative field/ID mapping suggestions from discovered metadata.

    This function never reads source row values. Sensitive/contact columns are
    excluded by default, and soft-delete or active filters are never guessed.
    """
    selected = next(
        (
            item for item in catalog.get("tables", [])
            if item.get("schema") == schema and item.get("name") == table
        ),
        None,
    )
    if selected is None:
        raise ValueError("The selected table or view was not found in the discovered schema")

    columns = selected.get("columns", [])
    primary_keys = list(selected.get("primary_key_columns") or [])
    primary_key_suggestion = None
    if len(primary_keys) == 1:
        primary_key_suggestion = {
            "column": primary_keys[0],
            "confidence": "high",
            "reason": "Declared single-column primary key",
        }
    elif len(primary_keys) > 1:
        primary_key_suggestion = {
            "column": None,
            "confidence": "none",
            "reason": "Composite primary key detected; choose or construct a stable unique document ID",
        }

    if primary_key_suggestion is None:
        id_candidates = [
            str(column["name"])
            for column in columns
            if _normalise_column_name(str(column["name"])) in {
                "id", "userid", "userrowid", "useruniqueid", "articleid",
                "productid", "companyid", "documentid",
            }
        ]
        if len(id_candidates) == 1:
            primary_key_suggestion = {
                "column": id_candidates[0],
                "confidence": "medium",
                "reason": "Column name strongly resembles a record identifier; uniqueness has not been verified",
            }
        else:
            primary_key_suggestion = {
                "column": None,
                "confidence": "none",
                "reason": "No unambiguous primary-key or record-ID candidate was found",
            }

    suggestions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    used_keys: set[str] = set()
    available_columns = [column for column in columns if str(column.get("data_type", "")).lower() in _SEARCHABLE_TYPES]

    title_candidates: list[dict[str, Any]] = []
    for column in available_columns:
        name = str(column["name"])
        normalized_name = _normalise_column_name(name)
        if normalized_name in _SENSITIVE_COLUMN_NAMES or any(
            normalized_name.endswith(sensitive) for sensitive in (
                "password", "token", "secret", "hash", "salt", "aadhaar",
                "aadhar", "passport", "phonenumber", "mobilenumber",
            )
        ):
            skipped.append({"column": name, "reason": "Sensitive or contact field excluded from automatic mapping"})
            continue
        if normalized_name in {"id", "userid", "userrowid", "useruniqueid", "articleid", "productid", "companyid", "documentid"}:
            skipped.append({"column": name, "reason": "Record identifier; used for document identity, not searchable content"})
            continue
        matching_roles = [
            role for role, aliases in _ROLE_ALIASES.items()
            if normalized_name in aliases
        ]
        role = matching_roles[0] if matching_roles else "general"
        if role == "title":
            title_candidates.append(column)
        suggestions.append({
            "key": "",
            "source_column": name,
            "label": re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").strip().title(),
            "role": role,
            "weight": _ROLE_DEFAULTS[role][0],
            "searchable": True,
            "semantic": str(column.get("data_type", "")).lower() in {
                "char", "nchar", "varchar", "nvarchar", "text", "ntext"
            },
            "aliases": list(_ROLE_DEFAULTS[role][1]),
            "confidence": "high" if role != "general" else "low",
            "reason": f"Column name matched the {role} field pattern" if role != "general" else "Unclassified searchable column; verify whether it adds useful search context",
        })

    # Keep one obvious title field. Additional name-like columns are retained
    # as general searchable fields rather than creating conflicting title roles.
    chosen_title = None
    if title_candidates:
        priority = {
            "title": 0, "fullname": 1, "displayname": 2, "name": 3,
            "firstname": 4, "productname": 5, "companyname": 6, "articletitle": 7,
            "businessname": 8, "headline": 9, "subject": 10,
        }
        chosen_title = min(
            title_candidates,
            key=lambda item: priority.get(_normalise_column_name(str(item["name"])), 100),
        )
    elif suggestions:
        chosen_title = next(
            (
                column for column in available_columns
                if any(item["source_column"] == column["name"] for item in suggestions)
            ),
            None,
        )

    for item in suggestions:
        item["key"] = _field_key_from_column(item["source_column"], used_keys)
        if chosen_title is not None and item["source_column"] == chosen_title["name"]:
            item["role"] = "title"
            item["weight"] = _ROLE_DEFAULTS["title"][0]
            item["confidence"] = "medium" if item["confidence"] == "low" else item["confidence"]
            item["reason"] = (
                "Selected as the primary display field based on its column name"
                if item["confidence"] == "high"
                else "Fallback title field; verify that it provides a useful display name"
            )
        elif item["role"] == "title":
            item["role"] = "general"
            item["weight"] = _ROLE_DEFAULTS["general"][0]
            item["aliases"] = []
            item["reason"] = "Additional name-like column; retained as a general searchable field"

    if not suggestions:
        raise ValueError("No safe searchable columns were found. Add a view with suitable text or identifier fields.")

    warnings = [
        "These are suggestions only. Review fields and document ID uniqueness before creating the engine.",
        "No IsDeleted, IsActive or Status filters were enabled automatically; their valid values depend on the source data.",
    ]
    if primary_key_suggestion["column"] is None:
        warnings.append(primary_key_suggestion["reason"])
    if chosen_title is None:
        warnings.append("No suitable title column was found; at least one suggested field must be assigned the title role.")

    return {
        "profile_id": catalog.get("profile_id"),
        "database": catalog.get("database"),
        "schema": schema,
        "table": table,
        "table_type": selected.get("type"),
        "id_column_suggestion": primary_key_suggestion,
        "fields": suggestions,
        "skipped_columns": skipped,
        "potential_filter_columns": [
            str(column["name"])
            for column in columns
            if _normalise_column_name(str(column["name"])) in {"isactive", "isdeleted", "status", "enabled"}
        ],
        "warnings": warnings,
    }


def suggest_source_mapping(
    profile_id: str,
    *,
    schema: str,
    table: str,
) -> dict[str, Any]:
    """Discover the selected table and return administrator-reviewable mappings."""
    catalog = discover_source_schema(profile_id, schema=schema)
    return build_source_mapping_suggestions(catalog, schema=schema, table=table)


def generate_join_view_preview(
    profile_id: str,
    *,
    base_schema: str,
    base_table: str,
    base_id_column: str,
    view_schema: str,
    view_name: str,
    selected_relationships: list[dict[str, str]],
) -> dict[str, Any]:
    """Generate, but do not execute, a safe many-to-one SQL Server view script."""
    for value, label in (
        (base_schema, "base schema"),
        (base_table, "base table"),
        (base_id_column, "base ID column"),
        (view_schema, "view schema"),
        (view_name, "view name"),
    ):
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Invalid {label} identifier")
    if len(selected_relationships) > 10:
        raise ValueError("A maximum of 10 relationships can be included in one view preview")
    if not selected_relationships:
        raise ValueError("Select at least one foreign-key relationship to generate a join view")

    catalog = discover_source_schema(profile_id)
    tables = {
        (item["schema"], item["name"]): item
        for item in catalog["tables"]
    }
    base_key = (base_schema, base_table)
    base = tables.get(base_key)
    if base is None:
        raise ValueError("The selected base table was not found in the discovered source")
    if base_id_column not in {column["name"] for column in base["columns"]}:
        raise ValueError("The selected document ID column does not exist on the base table")
    if base["primary_key_columns"] != [base_id_column]:
        raise ValueError(
            "The base table must have this exact single-column primary key to preserve one document per base record"
        )
    if (view_schema, view_name) in tables:
        raise ValueError("The proposed view name already exists as a table or view")

    relationship_lookup = {
        (
            item["constraint_name"],
            item["from_schema"],
            item["from_table"],
            item["to_schema"],
            item["to_table"],
        ): item
        for item in catalog["relationships"]
    }
    selected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()
    for supplied in selected_relationships:
        key = (
            supplied.get("constraint_name", ""),
            supplied.get("from_schema", ""),
            supplied.get("from_table", ""),
            supplied.get("to_schema", ""),
            supplied.get("to_table", ""),
        )
        if key in seen_keys:
            raise ValueError("The same foreign-key relationship was selected more than once")
        seen_keys.add(key)
        relationship = relationship_lookup.get(key)
        if relationship is None:
            raise ValueError("A selected relationship is not present in the discovered database metadata")
        selected.append(relationship)

    included = {base_key}
    ordered_joins: list[tuple[dict[str, Any], tuple[str, str]]] = []
    pending = list(selected)
    while pending:
        progress = False
        for relationship in list(pending):
            from_key = (relationship["from_schema"], relationship["from_table"])
            to_key = (relationship["to_schema"], relationship["to_table"])
            if from_key in included and to_key in included:
                raise ValueError("The selected relationships contain a redundant or cyclic join")
            if to_key in included and from_key not in included:
                raise ValueError(
                    "This join would add child rows to a parent base table and could duplicate search documents. "
                    "Use a many-to-one relationship or create an aggregated source view manually."
                )
            if from_key in included and to_key not in included:
                related = tables.get(to_key)
                if related is None:
                    raise ValueError("A related table is not visible to the configured SQL connection")
                if related["type"] != "BASE TABLE":
                    raise ValueError("Only declared relationships to base tables are supported in generated joins")
                ordered_joins.append((relationship, to_key))
                included.add(to_key)
                pending.remove(relationship)
                progress = True
        if not progress:
            raise ValueError("Selected relationships do not form a connected path from the base table")

    aliases = {base_key: "t0"}
    for index, (_, table_key) in enumerate(ordered_joins, start=1):
        aliases[table_key] = f"t{index}"

    used_output_names = {"DocumentId"}

    def output_name(table_key: tuple[str, str], column_name: str) -> str:
        base_name = re.sub(
            r"[^A-Za-z0-9_]",
            "_",
            f"{table_key[0]}_{table_key[1]}__{column_name}",
        ).strip("_") or "field"
        if not base_name[0].isalpha():
            base_name = f"field_{base_name}"
        base_name = base_name[:128]
        candidate = base_name
        suffix = 2
        while candidate.lower() in used_output_names:
            ending = f"_{suffix}"
            candidate = f"{base_name[:128 - len(ending)]}{ending}"
            suffix += 1
        used_output_names.add(candidate.lower())
        return candidate

    select_items = [
        f"{_quote_identifier(aliases[base_key])}.{_quote_identifier(base_id_column)} "
        "AS [DocumentId]"
    ]
    included_in_order = [base_key, *[key for _, key in ordered_joins]]
    for table_key in included_in_order:
        table_info = tables[table_key]
        for column in table_info["columns"]:
            column_name = str(column["name"])
            if table_key == base_key and column_name == base_id_column:
                continue
            alias_name = output_name(table_key, column_name)
            select_items.append(
                f"{_quote_identifier(aliases[table_key])}.{_quote_identifier(column_name)} "
                f"AS {_quote_identifier(alias_name)}"
            )

    from_clause = (
        f"FROM {_quote_identifier(base_schema)}.{_quote_identifier(base_table)} "
        f"AS {_quote_identifier(aliases[base_key])}"
    )
    join_clauses: list[str] = []
    for relationship, related_key in ordered_joins:
        from_key = (relationship["from_schema"], relationship["from_table"])
        from_alias = aliases[from_key]
        to_alias = aliases[related_key]
        predicates = [
            f"{_quote_identifier(from_alias)}.{_quote_identifier(column['from_column'])} = "
            f"{_quote_identifier(to_alias)}.{_quote_identifier(column['to_column'])}"
            for column in relationship["columns"]
        ]
        join_clauses.append(
            f"LEFT JOIN {_quote_identifier(related_key[0])}.{_quote_identifier(related_key[1])} "
            f"AS {_quote_identifier(to_alias)} ON " + " AND ".join(predicates)
        )

    sql = (
        f"CREATE OR ALTER VIEW {_quote_identifier(view_schema)}.{_quote_identifier(view_name)} AS\n"
        "SELECT\n    " + ",\n    ".join(select_items) + "\n"
        + from_clause + "\n"
        + "\n".join(join_clauses) + ";\n"
    )
    return {
        "profile_id": profile_id,
        "database": catalog["database"],
        "view_schema": view_schema,
        "view_name": view_name,
        "document_id_column": "DocumentId",
        "joined_tables": [
            {"schema": key[0], "table": key[1]}
            for key in included_in_order
        ],
        "relationship_count": len(ordered_joins),
        "view_sql": sql,
        "execution_performed": False,
        "warnings": [
            "This endpoint only generates SQL; it does not execute or create the view.",
            "The generated joins follow child-to-parent foreign keys so the base record remains the document grain.",
            "Review the selected columns and permissions, then execute the script manually in the development database.",
        ],
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
