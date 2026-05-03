"""
utils/db_utils.py
─────────────────
Database helpers that work entirely in memory.
 
IMPORTANT: There is no local filesystem involvement here.
All functions that need to read or write a database:
  1. Download raw bytes from Azure Blob Storage via blob_storage.py
  2. Open an in-memory sqlite3 connection with bytes_to_connection()
  3. Do their work
  4. Serialise back to bytes with connection_to_bytes() if modified
  5. Upload the result back to Blob Storage
 
The `blob_name` parameter (e.g. "customers.db") is the only
"path" the rest of the codebase ever passes around.
"""
import io
import sqlite3
from typing import List, Tuple, Dict
 
from ..config import NO_OF_SAMPLES
from .blob_storage import (
    download_db,
    upload_db,
    backup_db_to_blob,
    restore_from_backup,
    bytes_to_connection,
    connection_to_bytes
)
from shared.cache import TTLCache

# Module-level caches for read-only operations
_F2_SCHEMA_CACHE = TTLCache(default_ttl=300)
_F2_TABLE_COUNTS_CACHE = TTLCache(default_ttl=120)
_F2_SAMPLE_ROWS_CACHE = TTLCache(default_ttl=60)
_F2_VALIDATE_CACHE = TTLCache(default_ttl=60)


# Upload / ingest

def ingest_uploaded_db(uploaded_file) -> str:
    """
    Accept a Streamlit UploadedFile, push its bytes straight to the
    ACTIVE Blob container, and return the blob_name.
    """
    raw_bytes = uploaded_file.getbuffer().tobytes()
    blob_name = uploaded_file.name
    upload_db(raw_bytes, blob_name)
    return blob_name


def create_empty_db(db_name: str) -> str:
    """
    Create a brand-new empty SQLite database in memory, upload it to the
    ACTIVE container, and return the blob_name.
    """
    conn      = sqlite3.connect(":memory:")
    db_bytes  = connection_to_bytes(conn)
    conn.close()
    blob_name = db_name if db_name.endswith(".db") else db_name + ".db"
    upload_db(db_bytes, blob_name)
    return blob_name




# Schema extraction


def extract_schema(blob_name: str) -> str:
    """
    Download the database, extract a human-readable schema string, close.
    Returns the schema string — does NOT upload anything.
    """
    cached = _F2_SCHEMA_CACHE.get(blob_name)
    if cached is not None:
        return cached
    db_bytes = download_db(blob_name)
    conn     = bytes_to_connection(db_bytes)
    schema   = _schema_from_connection(conn)
    conn.close()
    _F2_SCHEMA_CACHE.set(blob_name, schema)
    return schema


def _schema_from_connection(conn: sqlite3.Connection) -> str:
    """
    Return a human-readable schema string describing all tables,
    columns (with types), primary keys, foreign keys, and indexes.
    """  
    cursor = conn.cursor()

    lines: List[str] = []

    # Tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        conn.close()
        return "Database is empty (no tables found)."
    
    for table in tables:
        lines.append(f"TABLE: {table}")

        # Columns
        cursor.execute(f"PRAGMA table_info('{table}');")
        cols = cursor.fetchall()
        for col in cols:
            # col: (cid, name, type, notnull, dflt_value, pk)
            pk_marker  = " [PK]"  if col[5] else ""
            nn_marker  = " NOT NULL" if col[3] else ""
            dflt       = f" DEFAULT {col[4]}" if col[4] is not None else ""
            lines.append(f"  - {col[1]}: {col[2]}{pk_marker}{nn_marker}{dflt}")


        # Foreign keys
        cursor.execute(f"PRAGMA foreign_key_list('{table}');")
        fks = cursor.fetchall()
        for fk in fks:
            # fk: (id, seq, table, from, to, on_update, on_delete, match)
            lines.append(f"  FK: {fk[3]} -> {fk[2]}({fk[4]})")


        # Indexes
        cursor.execute(f"PRAGMA index_list('{table}');")
        indexes = cursor.fetchall()
        for idx in indexes:
            if not idx[1].startswith("sqlite_autoindex"):
                cursor.execute(f"PRAGMA index_info('{idx[1]}');")
                idx_cols = [r[2] for r in cursor.fetchall()]
                unique = " UNIQUE" if idx[2] else ""
                lines.append(f"  INDEX{unique}: {idx[1]} on ({', '.join(idx_cols)})")        


        lines.append("")  # blank line between tables
      

    # Views
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name;")
    views = cursor.fetchall()
    for view in views:
        lines.append(f"VIEW: {view[0]}")
        lines.append(f"  SQL: {view[1]}")
        lines.append("")

    
    conn.close()
    return "\n".join(lines)


def get_table_row_counts(blob_name: str) -> Dict[str, int]:
    """Return {table_name: row_count} for all tables."""
    cached = _F2_TABLE_COUNTS_CACHE.get(blob_name)
    if cached is not None:
        return cached
    db_bytes = download_db(blob_name)
    conn     = bytes_to_connection(db_bytes)
    cursor   = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables   = [r[0] for r in cursor.fetchall()]
    counts   = {}
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM '{t}';")
        counts[t] = cursor.fetchone()[0]
    conn.close()
    _F2_TABLE_COUNTS_CACHE.set(blob_name, counts)
    return counts


def get_sample_rows(blob_name: str, table: str, limit: int = NO_OF_SAMPLES) -> List[dict]:
    """Return up to `limit` rows from a table as list-of-dicts."""
    cache_key = (blob_name, table, int(limit))
    cached = _F2_SAMPLE_ROWS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    db_bytes         = download_db(blob_name)
    conn             = bytes_to_connection(db_bytes)
    conn.row_factory = sqlite3.Row
    cursor           = conn.cursor()
    cursor.execute(f"SELECT * FROM '{table}' LIMIT {limit};")
    rows             = [dict(r) for r in cursor.fetchall()]
    conn.close()
    _F2_SAMPLE_ROWS_CACHE.set(cache_key, rows)
    return rows




# SQL validation (dry-run, read-only)

def validate_sql_syntax(blob_name: str, statements: List[str]) -> Tuple[bool, str]:
    """
    Dry-run all SQL statements together in a single transaction on an
    in-memory copy of the database, then roll back — leaving the live
    database completely untouched.

    This correctly handles multi-statement plans where later statements
    reference tables or columns created by earlier ones in the same batch
    (e.g. CREATE TABLE emp_new … then INSERT INTO emp_new …).

    Returns (success: bool, message: str).
    """
    cache_key = (blob_name, tuple(statements))
    cached = _F2_VALIDATE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    db_bytes = download_db(blob_name)
    conn     = bytes_to_connection(db_bytes)
    # isolation_level=None → autocommit off; we manage the transaction manually
    conn.isolation_level = None
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN;")
        cursor.execute("PRAGMA foreign_keys = ON;")
        for sql in statements:
            sql = sql.strip()
            if not sql:
                continue
            # Skip bare transaction-control statements — they would conflict
            # with our wrapping BEGIN/ROLLBACK and are fine to ignore here.
            if sql.upper() in ("BEGIN", "BEGIN TRANSACTION", "COMMIT", "ROLLBACK"):
                continue
            cursor.execute(sql)
        conn.rollback()
        conn.close()
        _F2_VALIDATE_CACHE.set(cache_key, (True, ""))
        return True, ""
    except Exception as e:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        result = (False, str(e))
        _F2_VALIDATE_CACHE.set(cache_key, result)
        return result




# SQL execution

def execute_sql_statements(
    blob_name: str,
    statements: List[str],
) -> Tuple[bool, str, str]:
    """
    Full cloud-native execute flow:
 
    1. Backup current active blob to the BACKUPS container.
    2. Download active blob to memory.
    3. Apply all statements in a single transaction.
    4. On success  → serialise and upload new bytes back to ACTIVE.
    5. On failure  → restore from the backup created in step 1.
 
    Returns
    -------
    (success: bool, message: str, backup_blob_name: str)
    The backup_blob_name is returned even on failure (for audit purposes).
    """
    # ── Step 1: Backup ────────────────────────────────────────────────────────
    try:
        backup_name = backup_db_to_blob(blob_name)
    except Exception as e:
        return False, f"Backup failed before execution: {e}", ""
    
    # ── Step 2: Download ──────────────────────────────────────────────────────
    try:
        db_bytes = download_db(blob_name)
    except Exception as e:
        return False, f"Download failed: {e}", backup_name
    

    # ── Step 3: Execute in memory ─────────────────────────────────────────────
    conn = bytes_to_connection(db_bytes)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        cursor = conn.cursor()
        for sql in statements:
            sql = sql.strip()
            if not sql:
                continue
            cursor.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()

        # ── Step 5a: Restore on failure ───────────────────────────────────────
        try:
            restore_from_backup(backup_name, blob_name)
        except Exception as re:
            return (
                False,
                f"SQL failed ({e}) AND restore also failed ({re}). "
                f"Manual restore from backup '{backup_name}' may be needed.",
                backup_name,
            )
        return False, f"SQL execution failed (rolled back from backup): {e}", backup_name
 
    # ── Step 4: Serialise & upload ────────────────────────────────────────────
    try:
        new_bytes = connection_to_bytes(conn)
        conn.close()
        upload_db(new_bytes, blob_name)
    except Exception as e:
        try:
            restore_from_backup(backup_name, blob_name)
        except Exception:
            pass
        return False, f"Upload of modified DB failed: {e}", backup_name
 
    return True, "All statements executed and uploaded successfully.", backup_name
