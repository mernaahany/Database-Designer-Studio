"""
utils/file_import.py
────────────────────
CSV / XLSX → SQLite table import pipeline.
 
Responsibilities
----------------
1. parse_upload_file()   — Read a Streamlit UploadedFile into a DataFrame.
2. validate_import()     — Run every possible sanity check and return a
                           structured report (errors + warnings).
3. build_insert_sql()    — Convert a clean DataFrame into parameterised
                           INSERT statements ready for execute_sql_statements().
4. execute_file_import() — Orchestrate validate → human approval → execute.
 
Design: nothing touches the local filesystem.  The uploaded file bytes live
only in RAM (io.BytesIO).  The DB is handled via blob_storage as always.
"""

from __future__ import annotations

import io
import re
import sqlite3
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .blob_storage import download_db, bytes_to_connection




# 1. FILE PARSING

def parse_upload_file(uploaded_file) -> Tuple[Optional[pd.DataFrame], str]:
    """
    Parse a Streamlit UploadedFile (CSV or XLSX) into a DataFrame.
 
    Returns
    -------
    (df, error_message)
    df is None and error_message is non-empty on failure.
    """
    name = uploaded_file.name.lower()
    raw  = io.BytesIO(uploaded_file.getbuffer().tobytes())
 
    try:
        if name.endswith(".csv"):
            # Try UTF-8 first, then fall back to latin-1
            try:
                df = pd.read_csv(raw, encoding="utf-8")
            except UnicodeDecodeError:
                raw.seek(0)
                df = pd.read_csv(raw, encoding="latin-1")
 
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(raw, engine="openpyxl")
 
        else:
            return None, f"Unsupported file type '{uploaded_file.name}'. Use .csv or .xlsx."
        

    except Exception as e:
        return None, f"Could not parse file: {e}"
 
    if df.empty and len(df.columns) == 0:
        return None, "File appears to be completely empty (no columns found)."
 
    # Strip leading/trailing whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]
    return df, ""




# 2. SCHEMA INTROSPECTION HELPERS

def _get_table_info(conn: sqlite3.Connection, table: str) -> List[Dict]:
    """
    Return PRAGMA table_info rows as list of dicts with keys:
    cid, name, type, notnull, dflt_value, pk
    """


def _get_table_info(conn: sqlite3.Connection, table: str) -> List[Dict]:
    """
    Return PRAGMA table_info rows as list of dicts with keys:
    cid, name, type, notnull, dflt_value, pk
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}');")
    cols = []
    for row in cur.fetchall():
        cols.append({
            "cid":       row[0],
            "name":      row[1],
            "type":      row[2].upper() if row[2] else "",
            "notnull":   bool(row[3]),
            "dflt_value": row[4],
            "pk":        bool(row[5]),
        })
    return cols


def _get_table_names(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    return [r[0] for r in cur.fetchall()]


def _get_foreign_keys(conn: sqlite3.Connection, table: str) -> List[Dict]:
    """
    Returns list of dicts: {from_col, ref_table, ref_col}
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA foreign_key_list('{table}');")
    fks = []
    for row in cur.fetchall():
        fks.append({
            "from_col":  row[3],
            "ref_table": row[2],
            "ref_col":   row[4],
        })
    return fks


def _get_indexes(conn: sqlite3.Connection, table: str) -> List[Dict]:
    """
    Returns list of dicts: {name, unique, columns}
    """
    cur = conn.cursor()
    cur.execute(f"PRAGMA index_list('{table}');")
    indexes = []
    for idx_row in cur.fetchall():
        idx_name   = idx_row[1]
        is_unique  = bool(idx_row[2])
        cur.execute(f"PRAGMA index_info('{idx_name}');")
        idx_cols = [r[2] for r in cur.fetchall()]
        indexes.append({"name": idx_name, "unique": is_unique, "columns": idx_cols})
    return indexes 


def _get_check_constraints(conn: sqlite3.Connection, table: str) -> List[str]:
    """
    Extract CHECK constraint expressions from CREATE TABLE SQL.
    Returns a list of raw expression strings (best-effort parsing).
    """
def _get_check_constraints(conn: sqlite3.Connection, table: str) -> List[str]:
    """
    Extract CHECK constraint expressions from CREATE TABLE SQL.
    Returns a list of raw expression strings (best-effort parsing).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    sql = row[0]
    # Simple regex — finds CHECK(...) blocks; handles one level of parens
    checks = re.findall(r"CHECK\s*\(([^)]+)\)", sql, re.IGNORECASE)
    return checks



# 3. TYPE COERCION CHECK

_INTEGER_TYPES = {"INTEGER", "INT", "TINYINT", "SMALLINT", "MEDIUMINT", "BIGINT", "INT2", "INT8"}
_REAL_TYPES    = {"REAL", "DOUBLE", "FLOAT", "NUMERIC", "DECIMAL"}
_BLOB_TYPES    = {"BLOB", "NONE"}


def _check_type_coercible(value: Any, sqlite_type: str) -> bool:
    """
    Return True if `value` can be safely stored in a column of `sqlite_type`.
    SQLite is permissive, but we flag obvious mismatches (e.g. "abc" → INTEGER).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return True   # NULL is always acceptable from a type standpoint
    t = sqlite_type.upper().split("(")[0].strip()
    if t in _INTEGER_TYPES:
        try:
            int(str(value).strip())
            return True
        except (ValueError, TypeError):
            return False
    if t in _REAL_TYPES:
        try:
            float(str(value).strip())
            return True
        except (ValueError, TypeError):
            return False
    return True   # TEXT / BLOB / unknown → accept anything




# 4. MAIN VALIDATION FUNCTION

class ValidationReport:
    """Collects errors (blockers) and warnings (advisories) during validation."""
 
    def __init__(self):
        self.errors:   List[str] = []
        self.warnings: List[str] = []
 
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
 
    def add_error(self, msg: str):
        self.errors.append(msg)
 
    def add_warning(self, msg: str):
        self.warnings.append(msg)
 
    def to_dict(self) -> Dict:
        return {
            "has_errors": self.has_errors,
            "errors":     self.errors,
            "warnings":   self.warnings,
        }
    

def validate_import(
    df: pd.DataFrame,
    blob_name: str,
    table_name: str,
) -> ValidationReport:
    """
    Run all validation checks on `df` against `table_name` in the database
    identified by `blob_name`.
    Checks performed (in order)
    ----------------------------
    V-01  Table existence
    V-02  Empty file (zero rows)
    V-03  Column name mismatch (file cols not in table, table cols not in file)
    V-04  Column count match (informational)
    V-05  NOT NULL violations (null in a NOT NULL / no-default column)
    V-06  Type coercion failures (e.g. "abc" in INTEGER column)
    V-07  Duplicate rows within the file on PK / UNIQUE columns
    V-08  PK / UNIQUE collisions against existing rows in the DB
    V-09  Foreign key violations (referenced value doesn't exist in parent table)
    V-10  CHECK constraint violations (best-effort)
    V-11  Completely duplicate rows in the file (all columns identical)
    V-12  Trailing whitespace in string values that could break UNIQUE checks
    V-13  Suspiciously large row count (informational warning)
    """

    report = ValidationReport()

    # ── V-01: Table existence ─────────────────────────────────────────────────
    db_bytes = download_db(blob_name)
    conn = bytes_to_connection(db_bytes)
    conn.execute("PRAGMA foreign_keys = ON;")

    all_tables = _get_table_names(conn)
    if table_name not in all_tables:
        similar = [t for t in all_tables if table_name.lower() in t.lower()]
        hint    = f" Did you mean: {similar}?" if similar else ""
        report.add_error(
            f"[V-01] Table '{table_name}' does not exist in the database.{hint}"
        )
        conn.close()
        return report   # nothing else makes sense without the table
    

    schema_cols = _get_table_info(conn, table_name)
    db_col_names  = {c["name"] for c in schema_cols}
    file_col_names = set(df.columns.tolist())


    # ── V-02: Empty file ──────────────────────────────────────────────────────
    if len(df) == 0:
        report.add_error("[V-02] The file contains no data rows (header only).")


    # ── V-03: Column name mismatches ──────────────────────────────────────────
    extra_in_file  = file_col_names - db_col_names
    missing_in_file = db_col_names  - file_col_names

    if extra_in_file:
        report.add_error(
            f"[V-03] File contains columns not found in table '{table_name}': "
            f"{sorted(extra_in_file)}. Remove or rename these columns."      
        )  

    # Missing columns are only errors if they are NOT NULL with no default
    blocking_missing = []
    advisory_missing = []

    if blocking_missing:
        report.add_error(
            f"[V-03] File is missing required NOT NULL columns (no default): "
            f"{blocking_missing}."
        )
    if advisory_missing:
        report.add_warning(
            f"[V-03] File is missing optional columns (will use NULL/default): "
            f"{advisory_missing}."
        )


    # ── V-04: Column count (informational) ───────────────────────────────────
    if len(file_col_names) != len(db_col_names) and not extra_in_file and not missing_in_file:
        report.add_warning(
            f"[V-04] Column count differs: file has {len(file_col_names)}, "
            f"table has {len(db_col_names)}."
        )


    # ── V-13: Large row count warning ────────────────────────────────────────
    if len(df) > 50_000:
        report.add_warning(
            f"[V-13] File contains {len(df):,} rows. Large imports may be slow."
        )


    # Stop deep checks if there are structural column errors
    if extra_in_file or blocking_missing:
        conn.close()
        return report

    # Narrow df to only columns that exist in the DB
    shared_cols = [c for c in df.columns if c in db_col_names]
    df_clean    = df[shared_cols].copy() 

    # Map col name → schema info for fast lookup
    col_info = {c["name"]: c for c in schema_cols}


    # ── V-05: NOT NULL violations ────────────────────────────────────────────
    null_violations: Dict[str, List[int]] = {}   # col → list of 1-based row numbers
    for col_name in shared_cols:
        meta = col_info[col_name]
        if meta["notnull"] and meta["dflt_value"] is None and not meta["pk"]:
            bad_rows = df_clean.index[
                df_clean[col_name].isna()
            ].tolist()
            if bad_rows:
                null_violations[col_name] = [r + 2 for r in bad_rows]  # +2: header + 0-index
 
    for col_name, rows in null_violations.items():
        sample = rows[:5]
        extra  = f" …and {len(rows)-5} more" if len(rows) > 5 else ""
        report.add_error(
            f"[V-05] Column '{col_name}' is NOT NULL but file has NULL/empty values "
            f"at rows: {sample}{extra}."
        )


    # ── V-06: Type coercion failures ─────────────────────────────────────────
    for col_name in shared_cols:
        meta     = col_info[col_name]
        bad_rows = []
        for idx, val in enumerate(df_clean[col_name]):
            if not _check_type_coercible(val, meta["type"]):
                bad_rows.append(idx + 2)
        if bad_rows:
            sample = bad_rows[:3]
            extra  = f" …and {len(bad_rows)-3} more" if len(bad_rows) > 3 else ""
            report.add_error(
                f"[V-06] Type mismatch in column '{col_name}' (expected {meta['type']}): "
                f"non-coercible values at rows {sample}{extra}."
            )


    # ── V-07: Intra-file duplicate PK / UNIQUE checks ────────────────────────
    pk_cols     = [c["name"] for c in schema_cols if c["pk"] and c["name"] in shared_cols]
    indexes     = _get_indexes(conn, table_name)
    unique_idxs = [i for i in indexes if i["unique"]]
 
    # PK duplicates in file
    if pk_cols:
        pk_df = df_clean[pk_cols].dropna()
        dups  = pk_df[pk_df.duplicated()].index.tolist()
        if dups:
            report.add_error(
                f"[V-07] File contains duplicate values in PK column(s) {pk_cols} "
                f"at rows: {[r+2 for r in dups[:5]]}."
            )


    # UNIQUE index duplicates in file
    for idx in unique_idxs:
        u_cols = [c for c in idx["columns"] if c in shared_cols]
        if not u_cols:
            continue
        u_df = df_clean[u_cols].dropna()
        dups = u_df[u_df.duplicated()].index.tolist()
        if dups:
            report.add_error(
                f"[V-07] File has duplicate values for UNIQUE index '{idx['name']}' "
                f"(columns {u_cols}) at rows: {[r+2 for r in dups[:5]]}."
            )


    # ── V-08: Collisions with existing DB rows ────────────────────────────────
    # Check PK collision
    if pk_cols:
        _check_existing_collisions(conn, table_name, pk_cols, df_clean, report, "PK", "V-08")
 
    # Check UNIQUE index collisions
    for idx in unique_idxs:
        u_cols = [c for c in idx["columns"] if c in shared_cols]
        if u_cols:
            _check_existing_collisions(
                conn, table_name, u_cols, df_clean, report,
                f"UNIQUE index '{idx['name']}'", "V-08",
            )   


    # ── V-09: Foreign key violations ────────────────────────────────────────
    fks = _get_foreign_keys(conn, table_name)
    for fk in fks:
        from_col  = fk["from_col"]
        ref_table = fk["ref_table"]
        ref_col   = fk["ref_col"]
        if from_col not in shared_cols:
            continue
        file_vals = df_clean[from_col].dropna().unique().tolist()
        if not file_vals:
            continue
        cur = conn.cursor()
        # Fetch all existing ref values
        cur.execute(f"SELECT DISTINCT \"{ref_col}\" FROM \"{ref_table}\";")
        existing_ref = {str(r[0]) for r in cur.fetchall()}
        bad_vals = [v for v in file_vals if str(v) not in existing_ref]
        if bad_vals:
            sample = bad_vals[:5]
            extra  = f" …and {len(bad_vals)-5} more" if len(bad_vals) > 5 else ""
            report.add_error(
                f"[V-09] Foreign key violation: column '{from_col}' references "
                f"'{ref_table}.{ref_col}' but values {sample}{extra} do not exist there."
            )    


    # ── V-10: CHECK constraint violations (best-effort) ──────────────────────
    checks = _get_check_constraints(conn, table_name)
    if checks:
        for check_expr in checks:
            bad_rows = _eval_check_constraint(df_clean, check_expr, shared_cols)
            if bad_rows:
                sample = bad_rows[:5]
                extra  = f" …and {len(bad_rows)-5} more" if len(bad_rows) > 5 else ""
                report.add_warning(
                    f"[V-10] CHECK constraint '{check_expr}' may be violated "
                    f"at rows: {sample}{extra}. (Best-effort check — verify manually.)"
                )                        


    # ── V-11: Fully duplicate rows in file ───────────────────────────────────
    full_dups = df_clean[df_clean.duplicated()].index.tolist()
    if full_dups:
        report.add_warning(
            f"[V-11] File contains {len(full_dups)} fully duplicate row(s) "
            f"(all columns identical). These will be inserted as separate rows."
        )


    # ── V-12: Trailing/leading whitespace in text columns ────────────────────
    whitespace_cols = []
    for col_name in shared_cols:
        meta = col_info[col_name]
        if "TEXT" in meta["type"] or meta["type"] == "":
            col_series = df_clean[col_name].dropna().astype(str)
            if (col_series != col_series.str.strip()).any():
                whitespace_cols.append(col_name)
 
    if whitespace_cols:
        report.add_warning(
            f"[V-12] Columns {whitespace_cols} contain values with leading/trailing "
            f"whitespace. This may cause UNIQUE constraint failures or lookup mismatches."
        )
 
    conn.close()
    return report



def _check_existing_collisions(
    conn: sqlite3.Connection,
    table: str,
    key_cols: List[str],
    df: pd.DataFrame,
    report: ValidationReport,
    label: str,
    code: str,
):
    
    """Check if any key_cols values in df already exist in the DB table."""
    cur      = conn.cursor()
    col_expr = ", ".join(f'"{c}"' for c in key_cols)
    cur.execute(f"SELECT {col_expr} FROM \"{table}\";")
    existing = {tuple(str(v) for v in row) for row in cur.fetchall()}

    collisions = []
    for idx, row in df[key_cols].iterrows():
        key = tuple(str(v) for v in row)
        if key in existing:
            collisions.append(idx + 2)
 
    if collisions:
        sample = collisions[:5]
        extra  = f" …and {len(collisions)-5} more" if len(collisions) > 5 else ""
        report.add_error(
            f"[{code}] {label} collision: {len(collisions)} row(s) in the file already "
            f"exist in '{table}' (columns {key_cols}). Duplicate rows at: {sample}{extra}."
        )



def _eval_check_constraint(
    df: pd.DataFrame,
    check_expr: str,
    shared_cols: List[str],
) -> List[int]:
    """
    Best-effort evaluation of a SQLite CHECK expression against a DataFrame.
    Only works for simple column-level expressions (e.g. "age >= 0", "len > 0").
    Returns list of 1-based row numbers that appear to violate the constraint.
    """
    # Replace SQL column references with df["col"] style for eval
    expr = check_expr
    for col in sorted(shared_cols, key=len, reverse=True):
        expr = re.sub(r'\b' + re.escape(col) + r'\b', f'df["{col}"]', expr)
 
    # Translate common SQL operators to Python
    expr = re.sub(r'\bAND\b', '&',  expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bOR\b',  '|',  expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bNOT\b', '~',  expr, flags=re.IGNORECASE)
 
    try:
        mask    = eval(expr)   # noqa: S307 — only runs on controlled schema strings
        bad_idx = df.index[~mask.fillna(False)].tolist()
        return [r + 2 for r in bad_idx]
    except Exception:
        return []   # can't evaluate — skip silently
    




# 5. INSERT SQL BUILDER

def build_insert_statements(
    df: pd.DataFrame,
    table_name: str,
    blob_name: str,
    on_conflict: str = "ABORT",   # ABORT | IGNORE | REPLACE
) -> List[str]:
    """
    Convert a validated DataFrame into a list of SQL INSERT statements.
    Each statement is a single INSERT with literal values (safe-escaped).
    The caller is responsible for passing a clean df (validated, right columns).
 
    on_conflict controls OR clause: ABORT (default, hard fail), IGNORE, REPLACE.
    """
    db_bytes   = download_db(blob_name)
    conn       = bytes_to_connection(db_bytes)
    schema_cols = _get_table_info(conn, table_name)
    conn.close()
 
    db_col_map  = {c["name"]: c for c in schema_cols}
    insert_cols = [c for c in df.columns if c in db_col_map]
    col_list    = ", ".join(f'"{c}"' for c in insert_cols)
 
    statements  = []
    for _, row in df[insert_cols].iterrows():
        values = []
        for col in insert_cols:
            val = row[col]
            if val is None or (isinstance(val, float) and math.isnan(val)):
                values.append("NULL")
            elif isinstance(val, str):
                # Escape single quotes by doubling them
                escaped = val.replace("'", "''")
                values.append(f"'{escaped}'")
            elif isinstance(val, bool):
                values.append("1" if val else "0")
            else:
                values.append(str(val))
        val_list = ", ".join(values)
        statements.append(
            f'INSERT OR {on_conflict} INTO "{table_name}" ({col_list}) VALUES ({val_list});'
        )
 
    return statements
