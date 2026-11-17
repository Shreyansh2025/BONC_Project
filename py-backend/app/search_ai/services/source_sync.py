"""Safe SQL Server source synchronization for configured search categories."""
from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.search_ai.platform.models import (
    CategoryConfig,
    SQLServerSourceConfig,
    SQLSourceFilter,
    SearchDocument,
)
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
        "WHERE parent_schema.name = :schema_name OR referenced_schema.name = :schema_name"
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
            fk.name AS CONSTRAINT_NAME,
            parent_schema.name AS FROM_SCHEMA,
            parent_table.name AS FROM_TABLE,
            parent_column.name AS FROM_COLUMN,
            referenced_schema.name AS TO_SCHEMA,
            referenced_table.name AS TO_TABLE,
            referenced_column.name AS TO_COLUMN,
            fkc.constraint_column_id AS ORDINAL_POSITION
        FROM sys.foreign_keys AS fk
        INNER JOIN sys.foreign_key_columns AS fkc
            ON fk.object_id = fkc.constraint_object_id
        INNER JOIN sys.tables AS parent_table
            ON fkc.parent_object_id = parent_table.object_id
        INNER JOIN sys.schemas AS parent_schema
            ON parent_table.schema_id = parent_schema.schema_id
        INNER JOIN sys.columns AS parent_column
            ON parent_column.object_id = fkc.parent_object_id
           AND parent_column.column_id = fkc.parent_column_id
        INNER JOIN sys.tables AS referenced_table
            ON fkc.referenced_object_id = referenced_table.object_id
        INNER JOIN sys.schemas AS referenced_schema
            ON referenced_table.schema_id = referenced_schema.schema_id
        INNER JOIN sys.columns AS referenced_column
            ON referenced_column.object_id = fkc.referenced_object_id
           AND referenced_column.column_id = fkc.referenced_column_id
        {relationship_schema_clause}
        ORDER BY parent_schema.name, parent_table.name, fk.name, fkc.constraint_column_id
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
    "mobile", "mobileno", "phone", "dateofbirth", "dob", "birthdate",
    "gender", "religion", "caste", "ethnicity", "maritalstatus",
    "salary", "income", "disability", "address", "address1", "address2",
    "addressline1", "addressline2", "homeaddress", "residentialaddress",
    "mailingaddress", "streetaddress",
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

_ID_COMPARABLE_TYPES = {
    "char", "nchar", "varchar", "nvarchar",
    "uniqueidentifier", "int", "bigint", "smallint", "tinyint", "bit",
    "decimal", "numeric", "float", "real", "money", "smallmoney",
    "date", "datetime", "datetime2", "smalldatetime", "datetimeoffset",
    "time",
}


def _normalise_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_sensitive_column_name(value: str) -> bool:
    logical_name = value.split("__")[-1]
    normalized_name = _normalise_column_name(logical_name)
    return normalized_name in _SENSITIVE_COLUMN_NAMES or any(
        normalized_name.endswith(sensitive) for sensitive in (
            "password", "token", "secret", "hash", "salt", "aadhaar",
            "aadhar", "passport", "passportnumber", "phonenumber", "mobilenumber",
            "email", "emailid", "mobile", "mobileno", "phone",
            "pannumber", "panid", "ssn", "socialsecuritynumber", "voterid",
            "aadharnumber", "aadhaarnumber", "birthdate", "homeaddress",
            "residentialaddress", "mailingaddress", "streetaddress",
            "address1", "address2", "addressline1", "addressline2",
        )
    )


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
    if len(primary_keys) == 1 and _IDENTIFIER.fullmatch(primary_keys[0]):
        primary_key_suggestion = {
            "column": primary_keys[0],
            "confidence": "high",
            "reason": "Declared single-column primary key",
        }
    elif len(primary_keys) == 1:
        primary_key_suggestion = {
            "column": None,
            "confidence": "none",
            "reason": "The primary-key column name is outside the supported safe-identifier format; create a view with a simple alias",
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
            if _IDENTIFIER.fullmatch(str(column["name"]))
            and _normalise_column_name(str(column["name"])) in {
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
        if not _IDENTIFIER.fullmatch(name):
            skipped.append({
                "column": name,
                "reason": "Column name is outside the supported safe-identifier format; create a view with a simple alias",
            })
            continue
        # Generated join views prefix columns with their source table. Infer
        # the logical name from the original leaf column.
        normalized_name = _normalise_column_name(name.split("__")[-1])
        if _is_sensitive_column_name(name):
            skipped.append({"column": name, "reason": "Sensitive or contact field excluded from automatic mapping"})
            continue
        if (
            column.get("is_primary_key")
            or column.get("is_foreign_key")
            or normalized_name in {
                "id", "userid", "userrowid", "useruniqueid", "articleid",
                "productid", "companyid", "documentid",
            }
            or (
                normalized_name.endswith(("id", "uuid", "guid"))
                and str(column.get("data_type", "")).lower() in {
                    "uniqueidentifier", "int", "bigint", "smallint", "tinyint"
                }
            )
        ):
            skipped.append({"column": name, "reason": "Record or relationship identifier; not mapped as searchable content"})
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
            "label": re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.split("__")[-1]).replace("_", " ").strip().title(),
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
            key=lambda item: priority.get(_normalise_column_name(str(item["name"]).split("__")[-1]), 100),
        )
    elif suggestions:
        text_types = {"char", "nchar", "varchar", "nvarchar", "text", "ntext"}
        suggested_columns = {item["source_column"] for item in suggestions}
        chosen_title = next(
            (
                column for column in available_columns
                if str(column.get("data_type", "")).lower() in text_types
                and str(column["name"]) in suggested_columns
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
    if _is_sensitive_column_name(base_id_column):
        raise ValueError("A sensitive or contact field cannot be used as the generated search document ID")
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

    used_output_names = {"documentid"}

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
            if str(column.get("data_type", "")).lower() not in _SEARCHABLE_TYPES:
                continue
            if _is_sensitive_column_name(column_name):
                continue
            alias_name = output_name(table_key, column_name)
            select_items.append(
                f"{_quote_identifier(aliases[table_key])}.{_quote_identifier(column_name)} "
                f"AS {_quote_identifier(alias_name)}"
            )

    generated_display_names = 0
    for table_key in included_in_order:
        table_info = tables[table_key]
        columns_by_name = {
            _normalise_column_name(str(column["name"])): str(column["name"])
            for column in table_info["columns"]
        }
        first_column = next(
            (columns_by_name[name] for name in ("firstname", "givenname") if name in columns_by_name),
            None,
        )
        last_column = next(
            (columns_by_name[name] for name in ("lastname", "surname", "familyname") if name in columns_by_name),
            None,
        )
        if not first_column or not last_column:
            continue
        first_ref = (
            f"{_quote_identifier(aliases[table_key])}.{_quote_identifier(first_column)}"
        )
        last_ref = (
            f"{_quote_identifier(aliases[table_key])}.{_quote_identifier(last_column)}"
        )
        first_value = f"NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {first_ref}))), N'')"
        last_value = f"NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {last_ref}))), N'')"
        display_expression = (
            f"CONCAT({first_value}, "
            f"CASE WHEN {first_value} IS NOT NULL AND {last_value} IS NOT NULL "
            f"THEN N' ' ELSE N'' END, {last_value})"
        )
        display_alias = output_name(table_key, "DisplayName")
        select_items.append(
            f"{display_expression} AS {_quote_identifier(display_alias)}"
        )
        generated_display_names += 1

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
            "Sensitive/contact columns and unsupported data types are excluded from the default projection.",
            *(
                ["A DisplayName column was generated from first/last-name fields found on the same table."]
                if generated_display_names else []
            ),
            "Review the selected columns and permissions, then execute the script manually in the development database.",
        ],
    }

def _build_filter_clause(
    filters: list[SQLSourceFilter],
) -> tuple[str, dict[str, Any]]:
    """Build parameterized source filters shared by validation and sync."""
    where_clauses: list[str] = []
    parameters: dict[str, Any] = {}
    for filter_index, source_filter in enumerate(filters):
        column = _quote_identifier(source_filter.column)
        operator = source_filter.operator
        if operator in {"eq", "ne"}:
            parameter = f"filter_{filter_index}"
            symbol = "=" if operator == "eq" else "<>"
            where_clauses.append(f"{column} {symbol} :{parameter}")
            parameters[parameter] = source_filter.value
        elif operator == "in":
            names: list[str] = []
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
    return where_sql, parameters


def validate_sqlserver_source(
    profile_id: str,
    *,
    schema: str,
    table: str,
    id_column: str,
    filters: list[SQLSourceFilter],
    max_records: int = 50000,
) -> dict[str, Any]:
    """Check the selected source and verify document-ID completeness and uniqueness."""
    catalog = discover_source_schema(profile_id, schema=schema)
    selected = next(
        (
            item for item in catalog["tables"]
            if item["schema"] == schema and item["name"] == table
        ),
        None,
    )
    if selected is None:
        raise ValueError("The selected source table or view was not found")
    column_types = {
        str(column["name"]): str(column["data_type"]).lower()
        for column in selected["columns"]
    }
    if id_column not in column_types:
        raise ValueError("The selected document ID column does not exist on this source")
    if _is_sensitive_column_name(id_column):
        raise ValueError("A sensitive or contact field cannot be used as the search document ID")
    if column_types[id_column] not in _ID_COMPARABLE_TYPES:
        raise ValueError(
            "The selected document ID data type cannot be checked safely for uniqueness; choose a supported scalar ID column"
        )

    for source_filter in filters:
        if source_filter.column not in column_types:
            raise ValueError(
                f"Filter column '{source_filter.column}' does not exist on the selected source"
            )

    qualified_table = (
        f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    )
    id_identifier = _quote_identifier(id_column)
    where_sql, parameters = _build_filter_clause(filters)
    trimmed_id_expression = (
        f"LEN(LTRIM(RTRIM(CONVERT(NVARCHAR(MAX), {id_identifier}))))"
    )
    query = text(
        "SELECT COUNT_BIG(*) AS total_rows, "
        f"COUNT_BIG({id_identifier}) AS non_null_ids, "
        f"COUNT_BIG(CASE WHEN {id_identifier} IS NOT NULL "
        f"AND {trimmed_id_expression} = 0 THEN 1 END) AS blank_ids, "
        f"COUNT_BIG(DISTINCT CASE WHEN {id_identifier} IS NOT NULL "
        f"AND {trimmed_id_expression} > 0 THEN {id_identifier} END) AS distinct_ids "
        f"FROM {qualified_table}{where_sql}"
    )

    engine = _engine_from_environment_name(f"SEARCH_SOURCE_{profile_id}")
    try:
        with engine.connect() as connection:
            row = connection.execute(query, parameters).mappings().one()
    finally:
        engine.dispose()

    total_rows = int(row["total_rows"] or 0)
    non_null_ids = int(row["non_null_ids"] or 0)
    blank_ids = int(row["blank_ids"] or 0)
    distinct_ids = int(row["distinct_ids"] or 0)
    null_ids = total_rows - non_null_ids
    usable_ids = non_null_ids - blank_ids
    duplicate_ids = usable_ids - distinct_ids
    warnings: list[str] = []
    if total_rows == 0:
        warnings.append("The configured filters returned no rows.")
    if null_ids:
        warnings.append(f"{null_ids} source rows have a NULL document ID.")
    if blank_ids:
        warnings.append(f"{blank_ids} document IDs are empty or contain only whitespace.")
    if duplicate_ids:
        warnings.append(f"{duplicate_ids} duplicate document IDs were found.")
    if total_rows > max_records:
        warnings.append(
            f"The source has {total_rows} matching rows; sync is capped at {max_records} records."
        )
    if selected["type"] == "VIEW":
        warnings.append(
            "The source is a view; ensure its document ID remains unique after all joins."
        )

    return {
        "profile_id": profile_id,
        "database": catalog["database"],
        "schema": schema,
        "table": table,
        "id_column": id_column,
        "total_rows": total_rows,
        "non_null_ids": non_null_ids,
        "distinct_ids": distinct_ids,
        "null_id_count": null_ids,
        "blank_id_count": blank_ids,
        "duplicate_id_count": duplicate_ids,
        "max_records": max_records,
        "is_valid": total_rows > 0 and total_rows <= max_records and null_ids == 0 and blank_ids == 0 and duplicate_ids == 0,
        "warnings": warnings,
    }


def sync_sqlserver_category(config: CategoryConfig, *, updated_by: str) -> int:
    if not isinstance(config.source, SQLServerSourceConfig):
        raise ValueError("This category does not use a SQL Server source")

    source = config.source

    # Enforce the same ID/filter preflight on the server immediately before
    # reading source records. The admin button is useful feedback, but it must
    # not be the only protection: direct API callers must not be able to sync
    # duplicate, NULL, blank, or empty source identities.
    profile_id = source.connection_env.removeprefix("SEARCH_SOURCE_")
    preflight = validate_sqlserver_source(
        profile_id,
        schema=source.schema,
        table=source.table,
        id_column=source.id_column,
        filters=source.filters,
        max_records=source.max_records,
    )
    if not preflight["is_valid"]:
        issues = "; ".join(preflight["warnings"]) or "The source did not pass identity validation."
        raise ValueError(
            "Source preflight failed; refusing to replace the existing index. " + issues
        )

    columns = [field.source_column for field in config.fields]
    if len(columns) != len(set(columns)):
        raise ValueError("A source column can only be mapped once")

    selected = [_quote_identifier(source.id_column) + " AS [_document_id]"]
    selected.extend(
        f"{_quote_identifier(field.source_column)} AS [{field.key}]"
        for field in config.fields
    )
    qualified_table = f"{_quote_identifier(source.schema)}.{_quote_identifier(source.table)}"

    # Values are bound parameters; only validated identifiers are interpolated.
    where_sql, parameters = _build_filter_clause(source.filters)
    parameters["row_limit"] = source.max_records
    # Source row order has no meaning for the search index. Avoid a full
    # sort here: on wide SQL Server views it can request a large memory grant
    # and stall an otherwise small snapshot sync.
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
        if raw_id is None or not str(raw_id).strip():
            raise ValueError(
                "Source contains a NULL or blank document ID; refusing to replace the existing index"
            )
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
