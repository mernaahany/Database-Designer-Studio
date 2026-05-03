from __future__ import annotations

import logging
import sqlite3

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContainerClient

from shared.config import (AZURE_STORAGE_CONNECTION_STRING,BLOB_CONTAINER_ACTIVE,WORKSPACE_CONTAINER,)
from shared.workspace import Workspace
from shared.cache import TTLCache

logger = logging.getLogger(__name__)

# Caches: short TTL for blobs and slightly shorter for workspace state
_BLOB_DOWNLOAD_CACHE = TTLCache(default_ttl=300)  # seconds
_WORKSPACE_CACHE = TTLCache(default_ttl=60)


def _get_connection_string() -> str:
    return AZURE_STORAGE_CONNECTION_STRING or ""


def _get_client() -> BlobServiceClient | None:
    conn_str = _get_connection_string()
    if not conn_str:
        return None

    try:
        return BlobServiceClient.from_connection_string(conn_str)
    except Exception:
        logger.exception("Failed to initialize Azure Blob client.")
        return None


def _ensure_container(client: BlobServiceClient, name: str) -> ContainerClient:
    container = client.get_container_client(name)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    return container


def save_workspace(ws: Workspace) -> None:
    client = _get_client()
    if client is None:
        return

    try:
        workspace_id = ws.workspace_id
        if not isinstance(workspace_id, str):
            workspace_id = str(workspace_id)
        if not workspace_id or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        logger.info(
            "Saving workspace: workspace_id=%s container=%s",
            workspace_id,
            WORKSPACE_CONTAINER,
        )
        container = _ensure_container(client, WORKSPACE_CONTAINER)
        blob_name = f"{workspace_id}/state.json"
        if not isinstance(blob_name, str):
            blob_name = str(blob_name)
        if not blob_name or not blob_name.strip():
            raise ValueError("blob_name must be a non-empty string")
        
        container.upload_blob(
            blob_name,
            ws.model_dump_json(),
            overwrite=True,
        )
        try:
            _WORKSPACE_CACHE.invalidate(workspace_id)
        except Exception:
            # Non-fatal: cache invalidation should not break saving
            pass
    except Exception:
        logger.exception("Failed to save workspace %s.", ws.workspace_id)


def load_workspace(workspace_id: str) -> Workspace:
    client = _get_client()
    if client is None:
        return Workspace(workspace_id=workspace_id)

    try:
        if not isinstance(workspace_id, str):
            workspace_id = str(workspace_id)
        if not workspace_id or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        logger.info(
            "Loading workspace: workspace_id=%s container=%s",
            workspace_id,
            WORKSPACE_CONTAINER,
        )
        blob_name = f"{workspace_id}/state.json"
        # Use a short-lived cache to avoid repeated blob downloads from UI
        cached = _WORKSPACE_CACHE.get(workspace_id)
        if cached is not None:
            return cached
        if not isinstance(blob_name, str):
            blob_name = str(blob_name)
        if not blob_name or not blob_name.strip():
            raise ValueError("blob_name must be a non-empty string")
        
        blob = client.get_blob_client(
            WORKSPACE_CONTAINER,
            blob_name,
        )
        stream = blob.download_blob(timeout=5)
        data = stream.readall()
        ws = Workspace.model_validate_json(data)
        _WORKSPACE_CACHE.set(workspace_id, ws)
        return ws
    except Exception:
        logger.exception("Workspace load failed.")
        return Workspace(workspace_id=workspace_id)


def upload_to_blob(file_bytes: bytes, path: str) -> str:
    client = _get_client()
    if client is None:
        return path

    try:
        blob_name = path
        if not isinstance(blob_name, str):
            blob_name = str(blob_name)
        if not blob_name or not blob_name.strip():
            raise ValueError("blob_name must be a non-empty string")
        logger.info("Uploading blob: container=%s path=%s", BLOB_CONTAINER_ACTIVE, blob_name)
        
        blob = client.get_blob_client(BLOB_CONTAINER_ACTIVE, blob_name)
        blob.upload_blob(file_bytes, overwrite=True)
        return blob.url
    except Exception:
        logger.exception("Failed to upload blob %s.", path)
        return path

def upload_db(db_bytes: bytes, blob_name: str) -> str:
    client = _get_client()
    if client is None:
        raise RuntimeError("Azure Blob Storage is not configured.")

    try:
        if not isinstance(blob_name, str):
            blob_name = str(blob_name)
        if not blob_name or not blob_name.strip():
            raise ValueError("blob_name must be a non-empty string")
        logger.info("Uploading database blob: container=%s blob_name=%s", BLOB_CONTAINER_ACTIVE, blob_name)
        container = _ensure_container(client, BLOB_CONTAINER_ACTIVE)
        
        blob = container.get_blob_client(blob_name)
        blob.upload_blob(db_bytes, overwrite=True)
        return blob_name
    except Exception:
        logger.exception("Failed to upload database blob %s.", blob_name)
        raise


def download_db(blob_name: str) -> bytes:
    client = _get_client()
    if client is None:
        raise RuntimeError("Azure Blob Storage is not configured.")
    if blob_name.startswith("http"):
        raise ValueError("Expected blob_name, got URL instead")

    try:
        if not isinstance(blob_name, str):
            blob_name = str(blob_name)
        if not blob_name or not blob_name.strip():
            raise ValueError("blob_name must be a non-empty string")
        logger.info("Downloading database blob: container=%s blob_name=%s", BLOB_CONTAINER_ACTIVE, blob_name)
        container = _ensure_container(client, BLOB_CONTAINER_ACTIVE)
        logger.error(
            "blob_name debug -> type=%s len=%s value=%s",
            type(blob_name).__name__,
            len(str(blob_name)) if blob_name is not None else None,
            repr(blob_name),
        )
        # Cache downloaded DB bytes to reduce repeated network IO for the same
        # blob during short-lived sessions. Caller should ensure cache TTL is
        # acceptable for their workflow.
        cached = _BLOB_DOWNLOAD_CACHE.get(blob_name)
        if cached is not None:
            return cached

        blob = container.get_blob_client(blob_name)
        data = blob.download_blob().readall()
        _BLOB_DOWNLOAD_CACHE.set(blob_name, data)
        return data
    except ResourceNotFoundError as exc:
        raise FileNotFoundError(
            f"Database blob '{blob_name}' not found in container '{BLOB_CONTAINER_ACTIVE}'."
        ) from exc


def get_blob_url(blob_name: str) -> str:
    client = _get_client()
    if client is None:
        raise RuntimeError("Azure Blob Storage is not configured.")

    if not isinstance(blob_name, str):
        blob_name = str(blob_name)
    if not blob_name or not blob_name.strip():
        raise ValueError("blob_name must be a non-empty string")
    logger.info("Resolving blob URL: container=%s blob_name=%s", BLOB_CONTAINER_ACTIVE, blob_name)
    container = _ensure_container(client, BLOB_CONTAINER_ACTIVE)
    
    return container.get_blob_client(blob_name).url


def ingest_uploaded_db(uploaded_file) -> str:
    """Upload a Streamlit SQLite upload into the active DB container."""
    raw_bytes = uploaded_file.getbuffer().tobytes()
    blob_name = uploaded_file.name
    if not isinstance(blob_name, str):
        blob_name = str(blob_name)
    if not blob_name or not blob_name.strip():
        raise ValueError("uploaded_file.name must be a non-empty string")
    upload_db(raw_bytes, blob_name)
    return blob_name


def extract_schema(blob_name: str) -> str:
    """Extract a readable SQLite schema summary from a blob-backed database."""
    # Schema extraction can be expensive; cache the textual summary per-blob.
    cached = _BLOB_DOWNLOAD_CACHE.get(f"schema:{blob_name}")
    if cached is not None:
        return cached

    db_bytes = download_db(blob_name)
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.deserialize(db_bytes)
        except AttributeError:
            raise RuntimeError("SQLite deserialize() is not available in this Python runtime.")
        schema = _schema_from_connection(conn)
        # cache the schema summary separately from raw blob bytes
        _BLOB_DOWNLOAD_CACHE.set(f"schema:{blob_name}", schema)
        return schema
    finally:
        conn.close()


def _schema_from_connection(conn: sqlite3.Connection) -> str:
    cursor = conn.cursor()
    lines: list[str] = []

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        return "Database is empty (no tables found)."

    for table in tables:
        lines.append(f"TABLE: {table}")

        cursor.execute(f"PRAGMA table_info('{table}');")
        for col in cursor.fetchall():
            pk_marker = " [PK]" if col[5] else ""
            nn_marker = " NOT NULL" if col[3] else ""
            dflt = f" DEFAULT {col[4]}" if col[4] is not None else ""
            lines.append(f"  - {col[1]}: {col[2]}{pk_marker}{nn_marker}{dflt}")

        cursor.execute(f"PRAGMA foreign_key_list('{table}');")
        for fk in cursor.fetchall():
            lines.append(f"  FK: {fk[3]} -> {fk[2]}({fk[4]})")

        cursor.execute(f"PRAGMA index_list('{table}');")
        for idx in cursor.fetchall():
            if not idx[1].startswith("sqlite_autoindex"):
                cursor.execute(f"PRAGMA index_info('{idx[1]}');")
                idx_cols = [row[2] for row in cursor.fetchall()]
                unique = " UNIQUE" if idx[2] else ""
                lines.append(f"  INDEX{unique}: {idx[1]} on ({', '.join(idx_cols)})")

        lines.append("")

    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='view' ORDER BY name;")
    for view_name, view_sql in cursor.fetchall():
        lines.append(f"VIEW: {view_name}")
        lines.append(f"  SQL: {view_sql}")
        lines.append("")

    return "\n".join(lines)
