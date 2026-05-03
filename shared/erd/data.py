"""
shared/erd/data.py
─────────────────
Extracts structured data needed to render an Entity–Relationship Diagram.

Supported source types
──────────────────────
  str              → blob name  → download SQLite DB and introspect it
  SuggestionPlan   → pre-approval plan  (Feature 1, before the DB exists)
  DatabaseSchema   → post-approval model (Feature 1, after schema generation)

Returns plain dicts / lists — no rendering logic here.
The renderer (erd_renderer.py) consumes this output.

Data shape returned by extract_erd_data()
------------------------------------------
{
  "tables": [
    {
      "name": "tblCUSTOMERS",
      "columns": [
        {
          "name":      "customer_id",
          "type":      "INTEGER",
          "pk":        True,
          "pk_order":  1,
          "notnull":   True,
          "default":   None,
          "fk_ref":    None,       # None or "other_table.other_col"
          "unique":    False,
          "indexed":   False,
        },
        ...
      ],
      "indexes":    [{"name": "idx_name", "unique": True, "columns": ["col1"]}, ...],
      "checks":     ["age >= 0", ...],
      "row_count":  42,
    },
    ...
  ],
  "views": [
    {"name": "vw_summary", "sql": "SELECT ..."},
    ...
  ],
  "relationships": [
    {
      "from_table": "tblCALENDAR",
      "from_col":   "project_hr_id",
      "to_table":   "tblPROJECT_HR",
      "to_col":     "project_hr_id",
      "on_update":  "NO ACTION",
      "on_delete":  "NO ACTION",
      "from_card":  "N",
      "to_card":    "1",
      "optional":   True,
    },
    ...
  ],
}
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from shared.blob_storage import download_db
from shared.db_utils import bytes_to_connection


# SQLite / blob helpers 

def _get_tables(cursor: sqlite3.Cursor) -> List[str]:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    return [r[0] for r in cursor.fetchall()]


def _get_views(cursor: sqlite3.Cursor) -> List[Dict]:
    cursor.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name;"
    )
    return [{"name": r[0], "sql": r[1]} for r in cursor.fetchall()]


def _get_columns(cursor: sqlite3.Cursor, table: str) -> List[Dict]:
    cursor.execute(f"PRAGMA table_info('{table}');")
    raw_cols = cursor.fetchall()
    cols = []
    for row in raw_cols:
        cols.append({
            "name":     row[1],
            "type":     row[2] if row[2] else "TEXT",
            "pk":       bool(row[5]),
            "pk_order": row[5],
            "notnull":  bool(row[3]),
            "default":  row[4],
            "fk_ref":   None,
            "unique":   False,
            "indexed":  False,
        })
    return cols


def _get_foreign_keys(cursor: sqlite3.Cursor, table: str) -> List[Dict]:
    cursor.execute(f"PRAGMA foreign_key_list('{table}');")
    return [
        {
            "from_col":  r[3],
            "to_table":  r[2],
            "to_col":    r[4],
            "on_update": r[5],
            "on_delete": r[6],
        }
        for r in cursor.fetchall()
    ]


def _get_indexes(cursor: sqlite3.Cursor, table: str) -> List[Dict]:
    cursor.execute(f"PRAGMA index_list('{table}');")
    index_rows = cursor.fetchall()
    indexes = []
    for idx in index_rows:
        idx_name  = idx[1]
        is_unique = bool(idx[2])
        if idx_name.startswith("sqlite_autoindex"):
            continue
        cursor.execute(f"PRAGMA index_info('{idx_name}');")
        idx_cols = [r[2] for r in cursor.fetchall()]
        indexes.append({"name": idx_name, "unique": is_unique, "columns": idx_cols})
    return indexes


def _get_check_constraints(cursor: sqlite3.Cursor, table: str) -> List[str]:
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return []
    return re.findall(r"CHECK\s*\(([^)]+)\)", row[0], re.IGNORECASE)


def _get_row_count(cursor: sqlite3.Cursor, table: str) -> int:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM '{table}';")
        return cursor.fetchone()[0]
    except Exception:
        return 0


def _infer_cardinality(
    from_col: str,
    col_map: Dict[str, Dict],
    indexes: List[Dict],
) -> Tuple[str, bool]:
    meta = col_map.get(from_col, {})
    is_unique_col = meta.get("pk", False)
    for idx in indexes:
        if idx["unique"] and idx["columns"] == [from_col]:
            is_unique_col = True
            break
    from_card = "1" if is_unique_col else "N"
    optional  = not meta.get("notnull", False)
    return from_card, optional


#  Column dict factory (shared by plan + schema extractors) 

def _make_column(
    name: str,
    type_: str,
    *,
    pk: bool = False,
    pk_order: int = 0,
    notnull: bool = False,
    default: Any = None,
    fk_ref: Optional[str] = None,
    unique: bool = False,
    indexed: bool = False,
) -> Dict:
    return {
        "name":     name,
        "type":     type_ or "TEXT",
        "pk":       pk,
        "pk_order": pk_order,
        "notnull":  notnull,
        "default":  default,
        "fk_ref":   fk_ref,
        "unique":   unique,
        "indexed":  indexed,
    }


#  Cardinality map for plan / schema extractors 

_REL_CARDS: Dict[str, Tuple[str, str]] = {
    "one-to-one":   ("1", "1"),
    "one-to-many":  ("1", "N"),
    "many-to-one":  ("N", "1"),
    "many-to-many": ("N", "M"),
}


#  Extractor: SuggestionPlan 

def _extract_from_plan(plan: Any) -> Dict[str, Any]:
    """
    Build ERD data from a Feature 1 SuggestionPlan object.
    No database download needed — everything comes from the plan model.
    """
    tables = []
    for entity in plan.suggested_entities:
        columns = []
        pk_order = 0
        for attr in entity.attributes:
            is_pk = bool(getattr(attr, "is_primary_key", False))
            if is_pk:
                pk_order += 1

            fk_ref = None
            if getattr(attr, "is_foreign_key", False):
                # Use attr.references if the model exposes it; else None.
                raw = getattr(attr, "references", None)
                if raw:
                    fk_ref = raw.replace("(", ".").rstrip(")")

            columns.append(_make_column(
                name     = attr.name,
                type_    = getattr(attr, "data_type", "TEXT"),
                pk       = is_pk,
                pk_order = pk_order if is_pk else 0,
                notnull  = not getattr(attr, "is_nullable", True),
                fk_ref   = fk_ref,
                unique   = is_pk,
            ))

        tables.append({
            "name":      entity.name,
            "columns":   columns,
            "indexes":   [],
            "checks":    [],
            "row_count": 0,
        })

    relationships = []
    for rel in plan.suggested_relationships:
        from_card, to_card = _REL_CARDS.get(rel.relationship_type, ("N", "1"))
        relationships.append({
            "from_table": rel.from_entity,
            "from_col":   getattr(rel, "from_column", "id"),
            "to_table":   rel.to_entity,
            "to_col":     getattr(rel, "to_column", "id"),
            "on_update":  "NO ACTION",
            "on_delete":  "NO ACTION",
            "from_card":  from_card,
            "to_card":    to_card,
            "optional":   True,
            "label":      getattr(rel, "label", ""),
        })

    return {"tables": tables, "views": [], "relationships": relationships}


#  Extractor: DatabaseSchema 

def _extract_from_schema(schema: Any) -> Dict[str, Any]:
    """
    Build ERD data from a Feature 1 DatabaseSchema object.
    No database download needed — everything comes from the schema model.
    """
    tables = []
    relationships = []

    for table in schema.tables:
        columns = []
        pk_order = 0

        for col in table.columns:
            constraints = [c.upper() for c in (col.constraints or [])]
            is_pk   = "PRIMARY KEY" in constraints
            is_nn   = "NOT NULL" in constraints or is_pk
            is_uniq = "UNIQUE" in constraints or is_pk

            if is_pk:
                pk_order += 1

            # Normalise "OtherTable(col_name)" or "OtherTable.col_name"
            fk_ref = None
            raw_ref = getattr(col, "references", None)
            if raw_ref:
                fk_ref = raw_ref.replace("(", ".").rstrip(")")

            columns.append(_make_column(
                name     = col.name,
                type_    = getattr(col, "data_type", "TEXT"),
                pk       = is_pk,
                pk_order = pk_order if is_pk else 0,
                notnull  = is_nn,
                fk_ref   = fk_ref,
                unique   = is_uniq,
            ))

            if fk_ref and "." in fk_ref:
                to_table, to_col = fk_ref.split(".", 1)
                relationships.append({
                    "from_table": table.name,
                    "from_col":   col.name,
                    "to_table":   to_table,
                    "to_col":     to_col,
                    "on_update":  "NO ACTION",
                    "on_delete":  "NO ACTION",
                    "from_card":  "1" if is_uniq else "N",
                    "to_card":    "1",
                    "optional":   not is_nn,
                    "label":      "",
                })

        # Parse index DDL strings e.g. "CREATE UNIQUE INDEX idx ON t(col);"
        indexes = []
        for idx_sql in (table.indexes or []):
            if not isinstance(idx_sql, str):
                continue
            is_unique = "UNIQUE" in idx_sql.upper()
            m_cols = re.search(r"ON\s+\w+\s*\(([^)]+)\)", idx_sql, re.IGNORECASE)
            cols = [c.strip() for c in m_cols.group(1).split(",")] if m_cols else []
            m_name = re.search(r"INDEX\s+(\w+)", idx_sql, re.IGNORECASE)
            idx_name = m_name.group(1) if m_name else "idx"
            indexes.append({"name": idx_name, "unique": is_unique, "columns": cols})

        tables.append({
            "name":      table.name,
            "columns":   columns,
            "indexes":   indexes,
            "checks":    [],
            "row_count": 0,
        })

    return {"tables": tables, "views": [], "relationships": relationships}


#  Extractor: blob name (str) — original implementation 
def _extract_from_blob(blob_name: str) -> Dict[str, Any]:
    db_bytes = download_db(blob_name)
    conn     = bytes_to_connection(db_bytes)
    cursor   = conn.cursor()

    table_names = _get_tables(cursor)
    views       = _get_views(cursor)

    if not table_names:
        conn.close()
        return {"tables": [], "views": [], "relationships": []}

    tables        = []
    relationships = []

    for tbl_name in table_names:
        columns = _get_columns(cursor, tbl_name)
        col_map = {c["name"]: c for c in columns}

        fks = _get_foreign_keys(cursor, tbl_name)
        for fk in fks:
            if fk["from_col"] in col_map:
                col_map[fk["from_col"]]["fk_ref"] = f"{fk['to_table']}.{fk['to_col']}"

        indexes = _get_indexes(cursor, tbl_name)

        for idx in indexes:
            for col_name in idx["columns"]:
                if col_name in col_map:
                    if idx["unique"] and len(idx["columns"]) == 1:
                        col_map[col_name]["unique"] = True
                    elif not idx["unique"]:
                        col_map[col_name]["indexed"] = True

        checks    = _get_check_constraints(cursor, tbl_name)
        row_count = _get_row_count(cursor, tbl_name)

        tables.append({
            "name":      tbl_name,
            "columns":   list(col_map.values()),
            "indexes":   indexes,
            "checks":    checks,
            "row_count": row_count,
        })

        for fk in fks:
            from_card, optional = _infer_cardinality(fk["from_col"], col_map, indexes)
            relationships.append({
                "from_table": tbl_name,
                "from_col":   fk["from_col"],
                "to_table":   fk["to_table"],
                "to_col":     fk["to_col"],
                "on_update":  fk["on_update"],
                "on_delete":  fk["on_delete"],
                "from_card":  from_card,
                "to_card":    "1",
                "optional":   optional,
                "label":      "",
            })

    conn.close()
    return {
        "tables":        tables,
        "views":         views,
        "relationships": relationships,
    }


# Public API 

def extract_erd_data(source: Any) -> Dict[str, Any]:
    """
    Extract ERD data from any supported source type.

    Parameters
    ----------
    source :
        str            → blob name in Azure Blob Storage
        SuggestionPlan → Feature 1 pre-approval plan (no DB required)
        DatabaseSchema → Feature 1 post-approval schema (no DB required)

    Returns
    -------
    Dict with keys "tables", "views", "relationships".
    """
    # str → blob name (original path)
    if isinstance(source, str):
        return _extract_from_blob(source)

    # Dispatch by class name — avoids hard imports and circular import risk.
    type_name = type(source).__name__

    if type_name == "SuggestionPlan":
        return _extract_from_plan(source)

    if type_name == "DatabaseSchema":
        return _extract_from_schema(source)

    # Duck-type fallbacks in case models are subclassed or renamed.
    if hasattr(source, "suggested_entities") and hasattr(source, "suggested_relationships"):
        return _extract_from_plan(source)

    if hasattr(source, "tables") and hasattr(source, "normalization_level"):
        return _extract_from_schema(source)

    raise TypeError(
        f"extract_erd_data() received unsupported type '{type_name}'. "
        f"Expected: str (blob name), SuggestionPlan, or DatabaseSchema."
    )