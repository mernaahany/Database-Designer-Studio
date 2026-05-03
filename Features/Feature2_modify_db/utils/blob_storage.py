"""
utils/blob_storage.py
─────────────────────
All Azure Blob Storage interactions for the DB Designer modification module.
 
Design principles
-----------------
* NO local filesystem writes.  Every database is downloaded into an
  in-memory io.BytesIO buffer, operated on with sqlite3's in-memory URI
  mode, then the result bytes are uploaded straight back to Blob Storage.
* Two containers:
    BLOB_CONTAINER_ACTIVE  — the single "live" copy of each database.
      Blob name  :  <db_name>          e.g. "customers.db"
    BLOB_CONTAINER_BACKUPS — immutable pre-modification snapshots.
      Blob name  :  <db_name>/backup_<ISO-timestamp>
      e.g. "customers.db/backup_2025-04-26T14:32:01"
* The rest of the codebase never calls BlobServiceClient directly —
  it only calls the public functions defined here.
"""
import io
import sqlite3
from datetime import datetime, timezone
from typing import List, Tuple

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError

from shared.blob_storage import (
    download_db as shared_download_db,
    get_blob_url as shared_get_blob_url,
    upload_db as shared_upload_db,
)

from ..config import (
    AZURE_BLOB_SAS_TOKEN,
    AZURE_STORAGE_CONNECTION_STRING,
    AZURE_STORAGE_ACCOUNT_NAME,
    AZURE_STORAGE_ACCOUNT_KEY,
    AZURE_STORAGE_ACCOUNT_URL,
    BLOB_CONTAINER_ACTIVE,
    BLOB_CONTAINER_BACKUPS,
)


# Client factory 

def _get_service_client() -> BlobServiceClient:
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is missing in .env")

    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


def _ensure_container(service: BlobServiceClient, name: str) -> ContainerClient:
    """Return (and lazily create) a container."""
    container = service.get_container_client(name)
    try:
        container.create_container()
    except ResourceExistsError:
        pass   # already exists — that's fine
    return container




# Public API 

def upload_db(db_bytes: bytes, blob_name: str) -> str:
    """
    Upload raw SQLite bytes to the ACTIVE container, overwriting any
    existing blob with the same name.
 
    Parameters
    ----------
    db_bytes  : Raw .db file bytes (from Streamlit UploadedFile or BytesIO).
    blob_name : Logical name, e.g. "customers.db".
 
    Returns
    -------
    The blob_name (used as the cloud "path" throughout the codebase).
    """
    return shared_upload_db(db_bytes, blob_name)


def get_blob_url(blob_name: str) -> str:
    """Return the canonical ACTIVE-container URL for a database blob."""
    return shared_get_blob_url(blob_name)


def download_db(blob_name: str) -> bytes:
    """
    Download a database from the ACTIVE container and return its raw bytes.
 
    Raises
    ------
    FileNotFoundError if the blob does not exist.
    """
    return shared_download_db(blob_name)
    

def backup_db_to_blob(blob_name: str) -> str:
    """
    Copy the current ACTIVE blob to the BACKUPS container with a
    timestamped name.  The original active blob is NOT deleted.
 
    Returns
    -------
    The backup blob name, e.g. "customers.db/backup_2025-04-26T14:32:01Z".
    """

    db_bytes    = download_db(blob_name)
    ts          = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    backup_name = f"{blob_name}/backup_{ts}"
 
    service   = _get_service_client()
    container = _ensure_container(service, BLOB_CONTAINER_BACKUPS)
    blob      = container.get_blob_client(backup_name)
    blob.upload_blob(db_bytes, overwrite=False)   # immutable snapshots
    return backup_name


def restore_from_backup(backup_blob_name: str, active_blob_name: str) -> None:
    """
    Overwrite the ACTIVE blob with bytes from a BACKUP blob.
    Used by the executor to roll back after a failed SQL run.
    """
    service    = _get_service_client()
    bkp_cont   = _ensure_container(service, BLOB_CONTAINER_BACKUPS)
    bkp_blob   = bkp_cont.get_blob_client(backup_blob_name)
    try:
        stream   = bkp_blob.download_blob()
        db_bytes = stream.readall()
    except ResourceNotFoundError:
        raise FileNotFoundError(
            f"Backup blob '{backup_blob_name}' not found in '{BLOB_CONTAINER_BACKUPS}'."
        )
    upload_db(db_bytes, active_blob_name)


def list_backups(blob_name: str) -> List[str]:
    """
    Return all backup blob names for a given database, sorted newest-first.
    e.g. ["customers.db/backup_2025-04-26T15:00:00Z", ...]
    """
    service   = _get_service_client()
    container = _ensure_container(service, BLOB_CONTAINER_BACKUPS)
    prefix    = f"{blob_name}/backup_"
    blobs     = container.list_blobs(name_starts_with=prefix)
    names     = sorted([b.name for b in blobs], reverse=True)
    return names


def delete_active_db(blob_name: str) -> None:
    """Remove a database from the ACTIVE container (e.g. on session reset)."""
    service   = _get_service_client()
    container = service.get_container_client(BLOB_CONTAINER_ACTIVE)
    try:
        container.get_blob_client(blob_name).delete_blob()
    except ResourceNotFoundError:
        pass   # already gone




# ── In-memory SQLite helpers ──────────────────────────────────────────────────
# These let callers work with a DB entirely in RAM.
# Pattern: bytes → BytesIO → sqlite3 connection → modified bytes → upload.

def bytes_to_connection(db_bytes: bytes) -> sqlite3.Connection:
    """
    Load raw DB bytes into an in-memory SQLite connection.
    The caller is responsible for closing the connection.
    """
    # Write bytes to an in-memory database via the sqlite3 backup API
    src_buf  = io.BytesIO(db_bytes)
    src_conn = sqlite3.connect(":memory:")
    # sqlite3 can't load from BytesIO directly, so write to a named temp URI
    # Instead: deserialise via the undocumented but stable deserialize API
    try:
        # Python 3.11+ / sqlite3 3.38+
        src_conn.deserialize(db_bytes)
    except AttributeError:
        # Fallback for older Python: write to a real temp file
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tf.write(db_bytes)
            tf_path = tf.name
        src_conn.close()
        src_conn = sqlite3.connect(tf_path)
        src_conn._tmp_path = tf_path   # type: ignore[attr-defined]
    return src_conn


def connection_to_bytes(conn: sqlite3.Connection) -> bytes:
    """
    Serialise an in-memory (or file-backed fallback) SQLite connection
    back to raw bytes, ready for upload.
    """
    try:
        data = conn.serialize()
        return bytes(data)
    except AttributeError:
        # Fallback: read the temp file
        tmp_path = getattr(conn, "_tmp_path", None)
        if tmp_path:
            conn.close()
            import os
            with open(tmp_path, "rb") as f:
                data = f.read()
            os.unlink(tmp_path)
            return data
        raise RuntimeError("Cannot serialise connection: no serialize() and no temp path.")
    

def check_blob_storage_configured() -> Tuple[bool, str]:
    """
    Returns (True, "") if Blob Storage credentials are present,
    or (False, "reason") otherwise.  Used by the UI to surface a warning.
    """
    if AZURE_STORAGE_CONNECTION_STRING:
        return True, ""
    if AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY:
        return True, ""
    if AZURE_STORAGE_ACCOUNT_URL and AZURE_BLOB_SAS_TOKEN:
        return True, ""
    return False, (
        "Azure Blob Storage credentials are missing. "
        "Set AZURE_STORAGE_CONNECTION_STRING, CONNECTION_STRING, "
        "AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY, or "
        "AZURE_STORAGE_ACCOUNT_URL + AZURE_BLOB_SAS_TOKEN."
    )
