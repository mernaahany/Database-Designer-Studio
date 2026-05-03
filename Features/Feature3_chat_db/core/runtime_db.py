"""
core/runtime_db.py — Cached runtime schema extraction and optional query execution.

"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from state import ColumnDef, ForeignKeyDef, SchemaSnapshot, TableDef


@dataclass(frozen=True, slots=True)
class SchemaCacheResult: 
    """Cached schema lookup result returned to the query path."""

    snapshot: SchemaSnapshot
    cache_key: str
    cache_hit: bool
    load_time_ms: float


@dataclass(slots=True)
class _SchemaCacheEntry:
    """Internal compressed schema cache entry.

    Includes creation time to support TTL-based expiration.
    """

    snapshot: SchemaSnapshot
    cache_key: str
    created_at: float


_ENGINE_CACHE: dict[str, Engine] = {}
_SCHEMA_CACHE: dict[str, _SchemaCacheEntry] = {}

# TTL for schema cache entries (seconds). Moderately short to avoid repeated
# introspection while allowing manual refresh via refresh_schema_cache().
SCHEMA_CACHE_TTL_SECONDS = 300


#  Public quoting helper (imported by query_agent / sql_utils)

def quote_identifier(name: str, dialect: str) -> str:
    """
    Return *name* wrapped in the correct quote character for *dialect*.

    PostgreSQL, SQLite  →  "name"
    MySQL / MariaDB     →  `name`
    MSSQL               →  [name]

    This is intentionally simple: it covers the common path where the
    stored name must be passed verbatim to the server (e.g. mixed-case
    names created by pandas.to_sql with SQLAlchemy).
    """
    d = dialect.lower()
    if d in {"mysql", "mariadb"}:
        escaped = name.replace("`", "``")
        return f"`{escaped}`"
    if d in {"mssql", "tsql"}:
        escaped = name.replace("]", "]]")
        return f"[{escaped}]"
    # postgresql, sqlite, and everything else
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def needs_quoting(name: str, dialect: str) -> bool:
    """
    Return True when *name* requires quoting to survive identifier folding.

    PostgreSQL folds unquoted identifiers to lowercase.  A name that is
    already all-lowercase is safe without quotes; any mixed-case or
    uppercase name requires quoting.

    MySQL is case-insensitive on most platforms (depends on
    lower_case_table_names setting) — we quote unconditionally there.
    SQLite is case-insensitive — never needs quoting.
    """
    d = dialect.lower()
    if d == "sqlite":
        return False
    if d in {"mysql", "mariadb", "mssql", "tsql"}:
        return True          # quote unconditionally for safety
    # postgresql and others: quote if the name is not already lowercase
    return name != name.lower()


def safe_identifier(name: str, dialect: str) -> str:
    """
    Return the identifier as it should appear inside a SQL statement.

    If quoting is needed, the name is quoted; otherwise it is returned
    as-is (unquoted lowercase is always safe for PostgreSQL).
    """
    if needs_quoting(name, dialect):
        return quote_identifier(name, dialect)
    return name


#  Engine cache 

def get_engine(db_url: str) -> Engine:
    """Return a cached SQLAlchemy engine for the target database URL."""
    if db_url not in _ENGINE_CACHE:
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        _ENGINE_CACHE[db_url] = create_engine(
            db_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    return _ENGINE_CACHE[db_url]


#  Schema cache 

def preload_schema_cache(db_url: str, schema_name: str | None = None) -> SchemaCacheResult:
    """Load schema into the in-memory cache once for a database connection."""
    return get_cached_schema_snapshot(db_url, schema_name=schema_name, refresh=False)


def refresh_schema_cache(db_url: str, schema_name: str | None = None) -> SchemaCacheResult:
    """Force-refresh the compressed schema cache for a database connection."""
    return get_cached_schema_snapshot(db_url, schema_name=schema_name, refresh=True)


def get_cached_schema_snapshot(
    db_url: str,
    schema_name: str | None = None,
    *,
    refresh: bool = False,
) -> SchemaCacheResult:
    """Return a compressed schema snapshot from the in-memory cache."""
    connection_hash = _connection_hash(db_url, schema_name)
    start_time = time.perf_counter()

    if not refresh and connection_hash in _SCHEMA_CACHE:
        entry = _SCHEMA_CACHE[connection_hash]
        # expire entries older than TTL
        if (time.time() - getattr(entry, "created_at", 0.0)) < SCHEMA_CACHE_TTL_SECONDS:
            return SchemaCacheResult(
                snapshot=entry.snapshot,
                cache_key=entry.cache_key,
                cache_hit=True,
                load_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
        # expired; fall through to refresh
        del _SCHEMA_CACHE[connection_hash]

    snapshot = _introspect_schema_snapshot(db_url, schema_name=schema_name)
    cache_key = f"{connection_hash}:{snapshot.version}"
    _SCHEMA_CACHE[connection_hash] = _SchemaCacheEntry(snapshot=snapshot, cache_key=cache_key, created_at=time.time())
    return SchemaCacheResult(
        snapshot=snapshot,
        cache_key=cache_key,
        cache_hit=False,
        load_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
    )


#  SQL execution 

import re as _re

_PARAM_PATTERN = _re.compile(r":[a-zA-Z_][a-zA-Z0-9_]*")

def execute_sql(db_url: str, sql: str) -> list[dict[str, Any]]:
    """Execute a validated read-only SQL statement and return JSON-safe rows.

    Safety guard: rejects SQL that still contains SQLAlchemy-style named
    parameter placeholders (:param_name).  These are generated when the LLM
    follows the old prompt instruction to parameterize user values.  Executing
    them via text() with no params dict causes a psycopg2 SyntaxError on the
    resulting %(param)s token.  We catch it here with a clear message instead.
    """
    params = _PARAM_PATTERN.findall(sql)
    if params:
        raise ValueError(
            f"SQL contains unbound parameter placeholders {params}. "
            "The agent must generate literal values, not :param_name placeholders. "
            "Check SQL_GENERATION_SYSTEM prompt — remove the parameterize instruction."
        )
    engine = get_engine(db_url)
    with engine.connect() as connection:
        result = connection.execute(text(sql))
        return [dict(row._mapping) for row in result]


#  Schema introspection 

def _introspect_schema_snapshot(db_url: str, schema_name: str | None = None) -> SchemaSnapshot:
    """
    Inspect the database and return a compressed schema snapshot.

    The exact table and column names returned by SQLAlchemy's inspector
    are preserved in TableDef.name and ColumnDef.name.  Callers that
    build SQL from these names should pass them through safe_identifier()
    so that mixed-case names (e.g. 'Employee') are quoted correctly for
    the target dialect.
    """
    engine = get_engine(db_url)
    inspector = inspect(engine)
    dialect = engine.dialect.name.lower()
    effective_schema = _effective_schema_name(inspector, dialect, schema_name)
    schema_arg = None if dialect == "sqlite" else effective_schema
    table_names = inspector.get_table_names(schema=schema_arg)

    tables: list[TableDef] = []
    for table_name in table_names:
        columns_info          = inspector.get_columns(table_name, schema=schema_arg)
        pk_columns            = set(
            inspector.get_pk_constraint(table_name, schema=schema_arg).get(
                "constrained_columns", []
            ) or []
        )
        foreign_keys_info     = inspector.get_foreign_keys(table_name, schema=schema_arg) or []
        indexes_info          = inspector.get_indexes(table_name, schema=schema_arg) or []
        unique_constraints    = inspector.get_unique_constraints(table_name, schema=schema_arg) or []
        try:
            table_comment = inspector.get_table_comment(table_name, schema=schema_arg).get("text")
        except NotImplementedError:
            table_comment = None

        indexed_columns                  = _build_index_lookup(indexes_info, unique_constraints)
        foreign_key_lookup, fk_defs      = _build_foreign_key_lookup(foreign_keys_info)

        columns = [
            ColumnDef(
                name        = str(col["name"]),
                type        = str(col.get("type", "UNKNOWN")),
                nullable    = bool(col.get("nullable", True)),
                primary_key = str(col["name"]) in pk_columns,
                foreign_key = foreign_key_lookup.get(str(col["name"])),
                default     = None if col.get("default") is None else str(col.get("default")),
                unique      = str(col["name"]) in indexed_columns["unique"],
                indexes     = sorted(indexed_columns["all"].get(str(col["name"]), set())),
                comment     = col.get("comment"),
            )
            for col in columns_info
        ]

        tables.append(
            TableDef(
                name         = table_name,   # exact name as stored in the DB
                columns      = columns,
                indexes      = sorted(_flatten_index_columns(indexed_columns["all"])),
                primary_keys = sorted(pk_columns),
                foreign_keys = fk_defs,
                comment      = table_comment,
            )
        )

    version = _schema_version_signature(dialect, effective_schema, tables)
    return SchemaSnapshot(
        schema_id = _schema_id(db_url, effective_schema),
        version   = version,
        tables    = tables,
        dialect   = dialect,
        source    = db_url,
    )


#  Internal helpers 

def _effective_schema_name(inspector: Any, dialect: str, schema_name: str | None) -> str:
    if schema_name:
        return schema_name
    if dialect == "sqlite":
        return "main"
    return inspector.default_schema_name or "public"


def _build_foreign_key_lookup(
    foreign_keys_info: list[dict[str, Any]],
) -> tuple[dict[str, str], list[ForeignKeyDef]]:
    lookup: dict[str, str] = {}
    fk_defs: list[ForeignKeyDef] = []
    for fk in foreign_keys_info:
        constrained     = fk.get("constrained_columns") or []
        referred_table  = fk.get("referred_table")
        referred_cols   = fk.get("referred_columns") or []
        for idx, col_name in enumerate(constrained):
            if not referred_table or idx >= len(referred_cols):
                continue
            ref_col   = str(referred_cols[idx])
            reference = f"{referred_table}.{ref_col}"
            lookup[str(col_name)] = reference
            fk_defs.append(
                ForeignKeyDef(
                    column            = str(col_name),
                    references_table  = str(referred_table),
                    references_column = ref_col,
                )
            )
    return lookup, fk_defs


def _build_index_lookup(
    indexes_info:      list[dict[str, Any]],
    unique_constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    all_columns: dict[str, set[str]] = {}
    unique_columns: set[str] = set()

    for index in indexes_info:
        idx_name = str(index.get("name") or "idx")
        for col_name in index.get("column_names") or []:
            all_columns.setdefault(str(col_name), set()).add(idx_name)
        if index.get("unique"):
            unique_columns.update(str(c) for c in index.get("column_names") or [])

    for uc in unique_constraints:
        cols = uc.get("column_names") or []
        unique_columns.update(str(c) for c in cols)
        uc_name = str(uc.get("name") or "uq")
        for col_name in cols:
            all_columns.setdefault(str(col_name), set()).add(uc_name)

    return {"all": all_columns, "unique": unique_columns}


def _flatten_index_columns(index_lookup: dict[str, set[str]]) -> set[str]:
    return set(index_lookup.keys())


def _connection_hash(db_url: str, schema_name: str | None) -> str:
    normalized = (schema_name or "").strip().lower()
    digest = hashlib.sha1(f"{db_url}|{normalized}".encode()).hexdigest()
    return digest[:16]


def _schema_id(db_url: str, schema_name: str) -> str:
    digest = hashlib.sha1(f"{db_url}|{schema_name}".encode()).hexdigest()
    return digest[:16]


def _schema_version_signature(
    dialect:     str,
    schema_name: str,
    tables:      list[TableDef],
) -> int:
    payload = {
        "dialect": dialect,
        "schema":  schema_name,
        "tables": [
            {
                "name": t.name,
                "columns": [
                    {
                        "name":        c.name,
                        "type":        c.type,
                        "nullable":    c.nullable,
                        "primary_key": c.primary_key,
                        "foreign_key": c.foreign_key,
                        "default":     c.default,
                        "unique":      c.unique,
                    }
                    for c in t.columns
                ],
                "indexes":      t.indexes,
                "primary_keys": t.primary_keys,
                "foreign_keys": [
                    {
                        "column":            fk.column,
                        "references_table":  fk.references_table,
                        "references_column": fk.references_column,
                    }
                    for fk in t.foreign_keys
                ],
            }
            for t in tables
        ],
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return int(digest[:8], 16)