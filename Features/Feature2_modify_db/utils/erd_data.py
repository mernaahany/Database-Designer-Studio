"""
utils/erd_data.py
─────────────────
Extracts structured data needed to render an Entity–Relationship Diagram
from a SQLite database living in Azure Blob Storage.
 
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
          "pk":        True,       # part of PRIMARY KEY
          "pk_order":  1,          # position in composite PK (1-based)
          "notnull":   True,
          "default":   None,
          "fk_ref":    None,       # None or "other_table.other_col"
          "unique":    False,      # covered by a single-col UNIQUE index
          "indexed":   False,      # covered by any non-unique index
        },
        ...
      ],
      "indexes": [
        {"name": "idx_name", "unique": True, "columns": ["col1", "col2"]},
        ...
      ],
      "checks":    ["age >= 0", ...],   # CHECK constraint expressions
      "row_count": 42,                  # live row count (0 if empty)
    },
    ...
  ],
  "views": [
    {"name": "vw_summary", "sql": "SELECT ..."},
    ...
  ],
  "relationships": [
    {
      "from_table":  "tblCALENDAR",
      "from_col":    "project_hr_id",
      "to_table":    "tblPROJECT_HR",
      "to_col":      "project_hr_id",
      "on_update":   "NO ACTION",
      "on_delete":   "NO ACTION",
      # Cardinality: derived from whether from_col is PK/UNIQUE and nullable
      "from_card":   "N",   # "1" or "N"
      "to_card":     "1",   # always "1" (FK targets PK/UNIQUE)
      "optional":    True,  # True if from_col is nullable (allows NULL FK)
    },
    ...
  ],
}
"""
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
 
from .blob_storage import download_db, bytes_to_connection




# ── Low-level PRAGMA helpers ──────────────────────────────────────────────────
 
def _get_tables(cursor: sqlite3.Cursor) -> List[str]:
    """All user-created table names, alphabetically sorted."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    return [r[0] for r in cursor.fetchall()]


def _get_views(cursor: sqlite3.Cursor) -> List[Dict]:
    """All view names and their defining SQL."""
    cursor.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name;"
    )
    return [{"name": r[0], "sql": r[1]} for r in cursor.fetchall()]


def _get_columns(cursor: sqlite3.Cursor, table: str) -> List[Dict]:
    """
    PRAGMA table_info rows enriched with fk_ref / unique / indexed flags.
    Returns list of column dicts (see module docstring for schema).
    """
    cursor.execute(f"PRAGMA table_info('{table}');")
    raw_cols = cursor.fetchall()
    # raw: (cid, name, type, notnull, dflt_value, pk)
    cols = []
    for row in raw_cols:
        cols.append({
            "name":     row[1],
            "type":     row[2] if row[2] else "TEXT",
            "pk":       bool(row[5]),
            "pk_order": row[5],          # 0 = not PK; 1,2,... = PK position
            "notnull":  bool(row[3]),
            "default":  row[4],
            "fk_ref":   None,            # filled in below
            "unique":   False,           # filled in below
            "indexed":  False,           # filled in below
        })
    return cols


def _get_foreign_keys(cursor: sqlite3.Cursor, table: str) -> List[Dict]:
    """
    PRAGMA foreign_key_list rows as dicts.
    Returns [{"from_col", "to_table", "to_col", "on_update", "on_delete"}, ...]
    """
    cursor.execute(f"PRAGMA foreign_key_list('{table}');")
    # raw: (id, seq, table, from, to, on_update, on_delete, match)
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
    """
    PRAGMA index_list / index_info rows, excluding sqlite auto-indexes.
    Returns [{"name", "unique", "columns": [...]}, ...]
    """
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
    """
    Best-effort extraction of CHECK() expressions from the CREATE TABLE SQL.
    Only captures single-level parens; nested parens are truncated.
    """
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return []
    sql = row[0]
    return re.findall(r"CHECK\s*\(([^)]+)\)", sql, re.IGNORECASE)


def _get_row_count(cursor: sqlite3.Cursor, table: str) -> int:
    """SELECT COUNT(*) for a table.  Returns 0 on any error."""
    try:
        cursor.execute(f"SELECT COUNT(*) FROM '{table}';")
        return cursor.fetchone()[0]
    except Exception:
        return 0
    



# ── Cardinality inference ─────────────────────────────────────────────────────
 
def _infer_cardinality(
    from_col: str,
    col_map: Dict[str, Dict],
    indexes: List[Dict],
) -> Tuple[str, bool]:
    """
    Determine the 'from' side cardinality and optionality of an FK.
 
    Returns
    -------
    (from_card, optional)
    from_card : "1" if the FK column is unique (one-to-one), else "N" (many-to-one)
    optional  : True if the FK column is nullable (i.e. the FK is optional)
    """
    meta = col_map.get(from_col, {})
 
    # Is the column covered by a single-col UNIQUE index or is itself PK?
    is_unique_col = meta.get("pk", False)
    for idx in indexes:
        if idx["unique"] and idx["columns"] == [from_col]:
            is_unique_col = True
            break
 
    from_card = "1" if is_unique_col else "N"
    optional  = not meta.get("notnull", False)
    return from_card, optional




# ── Public API ────────────────────────────────────────────────────────────────
 
def extract_erd_data(blob_name: str) -> Dict[str, Any]:
    """
    Download the database from Blob Storage and extract all ERD-relevant
    metadata.  Returns the structured dict described in the module docstring.
 
    Parameters
    ----------
    blob_name : The blob name (cloud "path") of the active database.
 
    Returns
    -------
    A dict with keys: "tables", "views", "relationships".
    Returns {"tables": [], "views": [], "relationships": []} for an empty DB.
    """
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
        # ── Columns ──────────────────────────────────────────────────────────
        columns = _get_columns(cursor, tbl_name)
        col_map = {c["name"]: c for c in columns}
 
        # ── Foreign keys ─────────────────────────────────────────────────────
        fks = _get_foreign_keys(cursor, tbl_name)
        for fk in fks:
            # Annotate column with FK reference string
            if fk["from_col"] in col_map:
                col_map[fk["from_col"]]["fk_ref"] = f"{fk['to_table']}.{fk['to_col']}"
 
        # ── Indexes ──────────────────────────────────────────────────────────
        indexes = _get_indexes(cursor, tbl_name)
 
        # Annotate columns with unique / indexed flags from indexes
        for idx in indexes:
            for col_name in idx["columns"]:
                if col_name in col_map:
                    if idx["unique"] and len(idx["columns"]) == 1:
                        col_map[col_name]["unique"] = True
                    elif not idx["unique"]:
                        col_map[col_name]["indexed"] = True
 
        # ── CHECK constraints ─────────────────────────────────────────────────
        checks = _get_check_constraints(cursor, tbl_name)
 
        # ── Row count ─────────────────────────────────────────────────────────
        row_count = _get_row_count(cursor, tbl_name)
 
        tables.append({
            "name":      tbl_name,
            "columns":   list(col_map.values()),  # preserve column order
            "indexes":   indexes,
            "checks":    checks,
            "row_count": row_count,
        })
 
        # ── Build relationship records ────────────────────────────────────────
        for fk in fks:
            from_card, optional = _infer_cardinality(
                fk["from_col"], col_map, indexes
            )
            relationships.append({
                "from_table": tbl_name,
                "from_col":   fk["from_col"],
                "to_table":   fk["to_table"],
                "to_col":     fk["to_col"],
                "on_update":  fk["on_update"],
                "on_delete":  fk["on_delete"],
                "from_card":  from_card,   # "1" or "N"
                "to_card":    "1",         # FK always targets PK/UNIQUE → "1" side
                "optional":   optional,    # nullable FK = optional participation
            })
 
    conn.close()
    return {
        "tables":        tables,
        "views":         views,
        "relationships": relationships,
    }
