"""
app.py — DB Studio unified router
Single entry point. Owns page config, workspace lifecycle, and routing only.
All UI lives in feature1_app.py, feature2_app.py, feature3_app.py.

"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="DB Studio — AI Database Platform",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from shared.import_paths import bootstrap_feature_paths

bootstrap_feature_paths()

from shared.blob_storage import download_db, get_blob_url, save_workspace, upload_db
from shared.sidebar import render_sidebar
from shared.workspace import Workspace, WorkspaceState

logger = logging.getLogger(__name__)


# UI-level Streamlit caches: keep short TTLs to avoid stale data across reruns.
# These wrap shared operations (which themselves have in-memory caches) to
# eliminate repeated work across Streamlit re-executions.
@st.cache_data(ttl=300)
def _cached_download_db(blob_name: str) -> bytes:
    return download_db(blob_name)


@st.cache_data(ttl=300)
def _cached_extract_schema(blob_name: str) -> str:
    from Features.Feature2_modify_db.utils.db_utils import extract_schema
    return extract_schema(blob_name)


# Global CSS 

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif !important; }
.stApp {
    background: linear-gradient(135deg, #0a0f1e 0%, #0d1b2a 50%, #0a1628 100%) !important;
    min-height: 100vh;
}
.block-container { padding-top: 1.5rem !important; padding-bottom: 3rem !important; max-width: 1280px !important; }

section[data-testid="stSidebar"] {
    background: rgba(7, 13, 26, 0.97) !important;
    border-right: 1px solid rgba(79,195,247,0.12) !important;
}
section[data-testid="stSidebar"] .block-container { padding-top: 0 !important; }

h1, h2, h3, h4 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 600 !important; }
h1 { color: #e0f4ff !important; font-size: 2rem !important; }
h2 { color: #b3d9f5 !important; font-size: 1.4rem !important; }
h3 { color: #7ec8e3 !important; font-size: 1.1rem !important; }
p, li, span { color: #a0c4e8; }
pre, code, .stCode { font-family: 'JetBrains Mono', monospace !important; font-size: 0.82rem !important; }

.glass-card {
    background: rgba(13,27,42,0.7);
    border: 1px solid rgba(79,195,247,0.15);
    border-radius: 16px;
    padding: 1.8rem;
    backdrop-filter: blur(12px);
    transition: border-color 0.25s ease, transform 0.2s ease;
}
.glass-card:hover { border-color: rgba(79,195,247,0.38); transform: translateY(-2px); }

.feature-card {
    background: rgba(13,27,42,0.8);
    border: 1px solid rgba(79,195,247,0.18);
    border-radius: 20px;
    padding: 2.2rem 1.8rem;
    text-align: center;
    transition: all 0.25s ease;
    cursor: pointer;
    height: 100%;
}
.feature-card:hover {
    background: rgba(30,58,95,0.4);
    border-color: rgba(79,195,247,0.45);
    transform: translateY(-4px);
    box-shadow: 0 12px 40px rgba(79,195,247,0.1);
}
.feature-icon  { font-size: 3rem; margin-bottom: 1rem; display: block; }
.feature-title { font-size: 1.25rem; font-weight: 700; color: #e0f4ff; margin-bottom: 0.6rem; }
.feature-desc  { font-size: 0.88rem; color: #7a9dbf; line-height: 1.6; margin-bottom: 1.4rem; }

.topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.9rem 1.5rem;
    background: rgba(7,13,26,0.85);
    border: 1px solid rgba(79,195,247,0.12);
    border-radius: 14px;
    margin-bottom: 1.8rem;
    backdrop-filter: blur(10px);
}
.topbar-logo      { font-size: 1.15rem; font-weight: 700; color: #4fc3f7; letter-spacing: -0.02em; }
.topbar-logo span { color: #e0f4ff; }
.topbar-ws        { font-size: 0.78rem; color: #4a6a8a; font-family: 'JetBrains Mono', monospace; }
.topbar-badge     { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px;
                    border-radius: 20px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.04em; }
.badge-empty    { background: rgba(30,58,95,0.4);  color: #4fc3f7;  border: 1px solid rgba(79,195,247,0.2); }
.badge-creating { background: rgba(45,58,30,0.4);  color: #a3e635;  border: 1px solid rgba(163,230,53,0.2); }
.badge-ready    { background: rgba(30,58,38,0.4);  color: #4ade80;  border: 1px solid rgba(74,222,128,0.2); }
.badge-modified { background: rgba(58,30,45,0.4);  color: #f472b6;  border: 1px solid rgba(244,114,182,0.2); }
.badge-chat     { background: rgba(30,40,58,0.4);  color: #60a5fa;  border: 1px solid rgba(96,165,250,0.2); }

.stepper { display: flex; align-items: center; gap: 0; margin: 1.4rem 0 2rem; }
.step {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 18px; border-radius: 999px;
    font-size: 0.8rem; font-weight: 600; white-space: nowrap;
}
.step-done   { background: rgba(74,222,128,0.12); color: #4ade80; border: 1px solid rgba(74,222,128,0.25); }
.step-active { background: rgba(79,195,247,0.18); color: #4fc3f7; border: 1px solid rgba(79,195,247,0.4); }
.step-locked { background: rgba(30,40,58,0.3);   color: #2d4a6a; border: 1px solid rgba(30,40,58,0.5); }
.step-arrow  { color: #1e3a5f; font-size: 1rem; padding: 0 4px; }

.upload-zone {
    border: 2px dashed rgba(79,195,247,0.25);
    border-radius: 16px; padding: 2.5rem;
    text-align: center; background: rgba(7,13,26,0.5);
    transition: border-color 0.2s;
}
.upload-zone:hover { border-color: rgba(79,195,247,0.5); }
.upload-icon  { font-size: 3rem; margin-bottom: 0.8rem; }
.upload-title { font-size: 1.05rem; font-weight: 600; color: #e0f4ff; margin-bottom: 0.4rem; }
.upload-sub   { font-size: 0.85rem; color: #4a6a8a; }

.connect-card {
    background: rgba(13,27,42,0.7);
    border: 1px solid rgba(79,195,247,0.15);
    border-radius: 14px; padding: 1.5rem;
}
.conn-status-ok  { display:inline-flex; align-items:center; gap:6px; color:#4ade80; font-size:.85rem;
                   font-weight:600; padding:6px 14px; background:rgba(74,222,128,0.1);
                   border:1px solid rgba(74,222,128,0.25); border-radius:20px; }
.conn-status-err { display:inline-flex; align-items:center; gap:6px; color:#f87171; font-size:.85rem;
                   font-weight:600; padding:6px 14px; background:rgba(248,113,113,0.1);
                   border:1px solid rgba(248,113,113,0.25); border-radius:20px; }

[data-testid="metric-container"] {
    background: rgba(30,58,95,0.28) !important;
    border: 1px solid rgba(79,195,247,0.18) !important;
    border-radius: 12px !important; padding: 1rem !important;
}
[data-testid="metric-container"] label {
    color: #4a6a8a !important; font-size: 0.78rem !important;
    text-transform: uppercase; letter-spacing: 0.06em;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #e0f4ff !important; font-size: 1.6rem !important; font-weight: 700 !important;
}

.stButton > button {
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important; border-radius: 10px !important;
    letter-spacing: 0.02em !important; transition: all 0.2s ease !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1565c0, #0d47a1) !important;
    border: 1px solid rgba(79,195,247,0.3) !important; color: #e0f4ff !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #1976d2, #1565c0) !important;
    box-shadow: 0 4px 20px rgba(79,195,247,0.2) !important;
    transform: translateY(-1px) !important;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 4px; background: transparent !important;
    border-bottom: 1px solid rgba(79,195,247,0.12);
}
.stTabs [data-baseweb="tab"] {
    background: transparent; border-radius: 8px 8px 0 0;
    color: #4a6a8a; font-weight: 600; font-size: 0.88rem; padding: 8px 18px;
}
.stTabs [aria-selected="true"] {
    background: rgba(79,195,247,0.12) !important;
    color: #4fc3f7 !important; border-bottom: 2px solid #4fc3f7 !important;
}

.stAlert   { border-radius: 12px !important; }
.stSuccess { border-left: 4px solid #4ade80 !important; }
.stWarning { border-left: 4px solid #f0a500 !important; }
.stError   { border-left: 4px solid #f87171 !important; }
.stInfo    { border-left: 4px solid #4fc3f7 !important; }

details { border: 1px solid rgba(79,195,247,0.12) !important; border-radius: 10px !important; }
hr      { border-color: rgba(79,195,247,0.1) !important; margin: 1.5rem 0 !important; }
.stSpinner > div { border-top-color: #4fc3f7 !important; }

[data-testid="stFileUploader"] {
    background: rgba(13,27,42,0.5) !important;
    border: 2px dashed rgba(79,195,247,0.2) !important;
    border-radius: 12px !important;
}
.stTextInput > div > div, .stTextArea > div > div {
    background: rgba(13,27,42,0.8) !important;
    border: 1px solid rgba(79,195,247,0.2) !important;
    border-radius: 10px !important; color: #e0f4ff !important;
}
.stSelectbox > div > div {
    background: rgba(13,27,42,0.8) !important;
    border: 1px solid rgba(79,195,247,0.2) !important;
    border-radius: 10px !important;
}
</style>
""", unsafe_allow_html=True)


# Internal helpers

def _save(ws: Workspace) -> None:
    st.session_state["workspace"] = ws
    save_workspace(ws)


def _set_state(
    ws: Workspace,
    *,
    state: WorkspaceState | None = None,
    entry_mode: str | None = None,
    approval_status: str | None = None,
) -> Workspace:
    if state is not None:
        ws.state = state
    if entry_mode is not None:
        ws.entry_mode = entry_mode
    ws.approval_status = approval_status
    _save(ws)
    return ws


def _write_temp_sqlite(db_bytes: bytes, file_name: str) -> str:
    suffix = Path(file_name).suffix or ".db"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(db_bytes)
        return tmp.name


def _ensure_active_blob(ws: Workspace) -> Workspace:
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        blob_name = ws.db_blob_name or f"{ws.workspace_id}/database.db"
        logger.info(
            "Ensuring active blob: workspace_id=%s local_path=%s blob_name=%s",
            ws.workspace_id,
            ws.db_local_path,
            blob_name,
        )
        with open(ws.db_local_path, "rb") as fh:
            upload_db(fh.read(), blob_name)
        ws.db_blob_name = blob_name
        ws.db_blob_url  = get_blob_url(blob_name)
        logger.info(
            "Active blob ready: workspace_id=%s blob_name=%s blob_url=%s",
            ws.workspace_id,
            ws.db_blob_name,
            ws.db_blob_url,
        )
    return ws


def _sync_db_conn_from_blob(ws: Workspace) -> Workspace:
    if not ws.db_blob_name:
        if ws.db_local_path and os.path.exists(ws.db_local_path):
            ws = _ensure_active_blob(ws)
        else:
            return ws

    logger.info(
        "Syncing DB connection from blob: workspace_id=%s blob_name=%s",
        ws.workspace_id,
        ws.db_blob_name,
    )
    try:
        db_bytes = _cached_download_db(ws.db_blob_name)
    except FileNotFoundError:
        if ws.db_local_path and os.path.exists(ws.db_local_path):
            logger.warning(
                "Blob missing; re-uploading from local file: workspace_id=%s local_path=%s blob_name=%s",
                ws.workspace_id,
                ws.db_local_path,
                ws.db_blob_name,
            )
            with open(ws.db_local_path, "rb") as fh:
                upload_db(fh.read(), ws.db_blob_name)
            db_bytes = _cached_download_db(ws.db_blob_name)
        else:
            raise
    local_path = _write_temp_sqlite(db_bytes, ws.db_blob_name)
    ws.db_local_path = local_path
    ws.db_conn_url   = f"sqlite:///{local_path}"
    ws.db_blob_url   = get_blob_url(ws.db_blob_name)
    logger.info(
        "DB sync complete: workspace_id=%s local_path=%s blob_name=%s",
        ws.workspace_id,
        ws.db_local_path,
        ws.db_blob_name,
    )
    if not ws.schema_ddl:
        ws.schema_ddl = _cached_extract_schema(ws.db_blob_name)
    return ws


def _prepare_query_workspace(ws: Workspace, *, force_refresh: bool = False) -> Workspace:
    if ws.db_blob_name and (
        force_refresh
        or not ws.db_local_path
        or not os.path.exists(ws.db_local_path)
        or not ws.db_conn_url
    ):
        return _sync_db_conn_from_blob(ws)
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        ws.db_conn_url = f"sqlite:///{ws.db_local_path}"
        return ws
    return ws


def _go_to_modify(ws: Workspace) -> None:
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        ws = _ensure_active_blob(ws)
    _set_state(ws, state=WorkspaceState.MODIFIED, approval_status=None)
    st.rerun()


def _go_to_chat(ws: Workspace) -> None:
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        ws = _ensure_active_blob(ws)
    ws = _prepare_query_workspace(ws, force_refresh=True)
    _set_state(ws, state=WorkspaceState.QUERY_READY, approval_status=None)
    st.rerun()


# Top bar

_STATE_BADGES = {
    WorkspaceState.ENTRY:          ("badge-empty",    "●  Entry"),
    WorkspaceState.EMPTY:          ("badge-creating", "●  Creating"),
    WorkspaceState.SCHEMA_CREATED: ("badge-creating", "●  Schema Review"),
    WorkspaceState.DB_READY:       ("badge-ready",    "●  DB Ready"),
    WorkspaceState.MODIFIED:       ("badge-modified", "●  Modified"),
    WorkspaceState.QUERY_READY:    ("badge-chat",     "●  Chat Active"),
}


def _render_topbar(ws: Workspace) -> None:
    cls, label = _STATE_BADGES.get(ws.state, ("badge-empty", "●  Unknown"))
    wid       = getattr(ws, "workspace_id", "—")
    wid_short = wid[:8] + "…" if len(wid) > 8 else wid
    st.markdown(
        f"""
        <div class="topbar">
            <div class="topbar-logo">🗄️ DB<span>Studio</span></div>
            <div class="topbar-ws">workspace · {wid_short}</div>
            <div class="topbar-badge {cls}">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Progress stepper

def _render_stepper(ws: Workspace) -> None:
    state = ws.state

    def _step(label: str, icon: str, done: bool, active: bool) -> str:
        if done:
            cls, prefix = "step-done", "✓"
        elif active:
            cls, prefix = "step-active", icon
        else:
            cls, prefix = "step-locked", icon
        return f'<div class="step {cls}">{prefix} {label}</div>'

    create_done   = state not in (WorkspaceState.ENTRY, WorkspaceState.EMPTY, WorkspaceState.SCHEMA_CREATED)
    create_active = state in (WorkspaceState.EMPTY, WorkspaceState.SCHEMA_CREATED)
    modify_done   = state == WorkspaceState.QUERY_READY
    modify_active = state == WorkspaceState.MODIFIED
    chat_active   = state == WorkspaceState.QUERY_READY
    db_ready      = state == WorkspaceState.DB_READY

    st.markdown(
        f"""
        <div class="stepper">
            {_step("Create DB", "🆕", create_done, create_active)}
            <div class="step-arrow">›</div>
            {_step("DB Ready", "✅", modify_done or db_ready, db_ready)}
            <div class="step-arrow">›</div>
            {_step("Modify DB", "✏️", modify_done, modify_active)}
            <div class="step-arrow">›</div>
            {_step("Chat With DB", "💬", False, chat_active)}
        </div>
        """,
        unsafe_allow_html=True,
    )


# Entry page — choose feature 

def render_entry_page(ws: Workspace) -> None:
    _render_topbar(ws)

    st.markdown(
        """
        <div style="text-align:center;padding:1.5rem 0 2rem;">
            <h1 style="font-size:2.6rem;font-weight:700;letter-spacing:-0.03em;margin-bottom:.5rem;">
                AI-Powered Database Platform
            </h1>
            <p style="color:#4a6a8a;font-size:1.05rem;max-width:580px;margin:0 auto;">
                Design, modify, and query your databases through natural language.
                No SQL expertise required.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3, gap="large")

    with col1:
        st.markdown("""
            <div class="feature-card">
                <span class="feature-icon">🆕</span>
                <div class="feature-title">Create Database</div>
                <div class="feature-desc">
                    Describe your system in plain English. AI designs a normalized schema,
                    generates an ERD, and builds a SQLite database — ready in seconds.
                </div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("Start with Create DB", type="primary", use_container_width=True, key="entry_create"):
            _set_state(ws, state=WorkspaceState.EMPTY, entry_mode="create", approval_status=None)
            st.rerun()

    with col2:
        st.markdown("""
            <div class="feature-card">
                <span class="feature-icon">✏️</span>
                <div class="feature-title">Modify Existing DB</div>
                <div class="feature-desc">
                    Upload your SQLite database or connect via URL. Use natural language
                    to alter schema, insert data, and manage your database safely.
                </div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("Upload & Modify", use_container_width=True, key="entry_modify"):
            _set_state(ws, state=WorkspaceState.EMPTY, entry_mode="upload", approval_status=None)
            st.rerun()

    with col3:
        st.markdown("""
            <div class="feature-card">
                <span class="feature-icon">💬</span>
                <div class="feature-title">Chat With DB</div>
                <div class="feature-desc">
                    Have an existing database? Connect and ask questions in plain English.
                    AI translates your queries into SQL and returns readable results.
                </div>
            </div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("Connect & Chat", use_container_width=True, key="entry_chat"):
            _set_state(ws, state=WorkspaceState.EMPTY, entry_mode="chat_direct", approval_status=None)
            st.rerun()

    st.divider()
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Schema Design",  "AI-Powered")
    sc2.metric("Normalization",  "3NF Guaranteed")
    sc3.metric("Human-in-Loop",  "Every Step")
    sc4.metric("Storage",        "Azure Blob")


# Empty phase — routes by entry_mode

def render_empty_phase(ws: Workspace) -> None:
    _render_topbar(ws)
    _render_stepper(ws)

    if ws.entry_mode == "upload":
        render_upload_phase(ws)
    elif ws.entry_mode == "chat_direct":
        render_connect_phase(ws)
    else:
        from feature1_app import render_input_phase
        render_input_phase(ws)


# Schema created phase — routes to suggestion phase in feature1_app

def render_schema_created_phase(ws: Workspace) -> None:
    _render_topbar(ws)
    _render_stepper(ws)
    from feature1_app import render_suggestion_phase
    render_suggestion_phase(ws)


# DB ready phase — routes to modify or chat based on entry_mode
# Upload phase — INLINE, no import from feature2_app
# Called when entry_mode == "upload".
# Ingests a .db/.sqlite file → uploads blob → extracts schema →
# transitions to DB_READY so the user can pick Modify or Chat.
# ─────────────────────────────────────────────────────────────

def render_upload_phase(ws: Workspace) -> None:
    st.markdown("## 📂 Upload Your Database")
    st.caption("Upload a SQLite file to modify or query it with AI assistance.")

    st.markdown(
        """
        <div class="upload-zone">
            <div class="upload-icon">📂</div>
            <div class="upload-title">Drop your SQLite database here</div>
            <div class="upload-sub">Supports .db · .sqlite · .sqlite3 files</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "Choose file",
        type=["db", "sqlite", "sqlite3"],
        key="upload_phase_file",
        label_visibility="collapsed",
    )

    if not uploaded:
        if st.button("← Back", key="upload_back_no_file"):
            _set_state(ws, state=WorkspaceState.ENTRY, approval_status=None)
            st.rerun()
        return

    # File info card
    st.markdown(
        f'<div class="glass-card" style="padding:1rem 1.4rem;">'
        f'<span style="color:#4ade80;font-weight:700">✓ {uploaded.name}</span>'
        f'<span style="color:#4a6a8a;margin-left:1rem;font-size:.85rem">'
        f'{uploaded.size / 1024:.1f} KB</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    col_proceed, col_cancel = st.columns([1, 3])
    with col_proceed:
        if st.button("Ingest & Continue →", type="primary", use_container_width=True, key="upload_proceed_btn"):
            with st.spinner("Uploading and reading schema…"):
                try:
                    from Features.Feature2_modify_db.utils.db_utils import ingest_uploaded_db

                    blob_name  = ingest_uploaded_db(uploaded)
                    schema_ddl = _cached_extract_schema(blob_name)
                    local_path = _write_temp_sqlite(uploaded.getvalue(), uploaded.name)

                    ws.db_blob_name  = blob_name
                    ws.db_blob_url   = get_blob_url(blob_name)
                    ws.db_local_path = local_path
                    ws.db_conn_url   = f"sqlite:///{local_path}"
                    ws.schema_ddl    = schema_ddl

                    _set_state(ws, state=WorkspaceState.DB_READY, entry_mode="upload", approval_status=None)
                    st.rerun()
                except Exception as exc:
                    logger.exception("Upload phase ingestion failed.")
                    st.error(f"Upload failed: {exc}")

    with col_cancel:
        if st.button("← Back", use_container_width=True, key="upload_back_btn"):
            _set_state(ws, state=WorkspaceState.ENTRY, approval_status=None)
            st.rerun()



# Connect phase — direct-to-chat via file upload or URL

def render_connect_phase(ws: Workspace) -> None:
    st.markdown("## 🔌 Connect to Your Database")
    st.caption("Upload a SQLite file or provide a connection URL — then jump straight to chatting.")

    tab_file, tab_url = st.tabs(["📂 Upload File", "🔗 Connection URL"])

    with tab_file:
        st.markdown(
            """
            <div class="upload-zone">
                <div class="upload-icon">📂</div>
                <div class="upload-title">Upload your SQLite database</div>
                <div class="upload-sub">Supports .db · .sqlite · .sqlite3 files</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader(
            "Choose file",
            type=["db", "sqlite", "sqlite3"],
            key="connect_upload_file",
            label_visibility="collapsed",
        )
        if uploaded:
            if st.button("Connect & Start Chatting", type="primary", use_container_width=True, key="connect_file_btn"):
                with st.spinner("Uploading and connecting…"):
                    try:
                        from Features.Feature2_modify_db.utils.db_utils import ingest_uploaded_db

                        blob_name  = ingest_uploaded_db(uploaded)
                        schema_ddl = _cached_extract_schema(blob_name)
                        local_path = _write_temp_sqlite(uploaded.getvalue(), uploaded.name)

                        ws.db_blob_name  = blob_name
                        ws.db_blob_url   = get_blob_url(blob_name)
                        ws.db_local_path = local_path
                        ws.db_conn_url   = f"sqlite:///{local_path}"
                        ws.schema_ddl    = schema_ddl
                        ws.approval_status = None

                        st.markdown('<span class="conn-status-ok">✓ Connected successfully</span>', unsafe_allow_html=True)
                        _set_state(ws, state=WorkspaceState.QUERY_READY, entry_mode="chat_direct", approval_status=None)
                        st.rerun()
                    except Exception as exc:
                        logger.exception("Chat-direct file connect failed.")
                        st.error(f"Connection failed: {exc}")

    with tab_url:
        st.markdown('<div class="connect-card">', unsafe_allow_html=True)
        st.markdown("#### Connection URL")
        st.caption("Provide a SQLite connection string. Format: `sqlite:///path/to/database.db`")

        conn_url = st.text_input(
            "Connection URL",
            placeholder="sqlite:///your_database.db  or  sqlite:///:memory:",
            key="connect_url_input",
            label_visibility="collapsed",
        )

        col_test, col_connect = st.columns([1, 2])

        with col_test:
            if st.button("🔍 Test Connection", use_container_width=True, key="test_conn_btn"):
                if not conn_url.strip():
                    st.warning("Enter a connection URL first.")
                else:
                    with st.spinner("Testing…"):
                        try:
                            import sqlite3
                            path   = conn_url.replace("sqlite:///", "")
                            conn   = sqlite3.connect(path)
                            cursor = conn.cursor()
                            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                            tables = [r[0] for r in cursor.fetchall()]
                            conn.close()
                            st.session_state["conn_test_ok"]     = True
                            st.session_state["conn_test_tables"] = tables
                            st.session_state["conn_test_url"]    = conn_url.strip()
                        except Exception as exc:
                            st.session_state["conn_test_ok"]  = False
                            st.session_state["conn_test_err"] = str(exc)

        if st.session_state.get("conn_test_ok") is True:
            tables = st.session_state.get("conn_test_tables", [])
            st.markdown(
                f'<span class="conn-status-ok">✓ Connected — {len(tables)} table(s) found</span>',
                unsafe_allow_html=True,
            )
            if tables:
                st.caption("Tables: " + ", ".join(f"`{t}`" for t in tables[:8]) + ("…" if len(tables) > 8 else ""))

        elif st.session_state.get("conn_test_ok") is False:
            err = st.session_state.get("conn_test_err", "Unknown error")
            st.markdown(
                f'<span class="conn-status-err">✗ Connection failed: {err}</span>',
                unsafe_allow_html=True,
            )

        with col_connect:
            connect_disabled = not st.session_state.get("conn_test_ok", False)
            if st.button(
                "Connect & Start Chatting",
                type="primary",
                use_container_width=True,
                disabled=connect_disabled,
                key="url_connect_btn",
            ):
                url  = st.session_state.get("conn_test_url", conn_url.strip())
                path = url.replace("sqlite:///", "")

                with st.spinner("Reading schema…"):
                    try:
                        import sqlite3
                        conn   = sqlite3.connect(path)
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                        tables = [r[0] for r in cursor.fetchall()]
                        ddl_parts = []
                        for t in tables:
                            cursor.execute(f"SELECT sql FROM sqlite_master WHERE name='{t}';")
                            row = cursor.fetchone()
                            if row and row[0]:
                                ddl_parts.append(row[0])
                        conn.close()
                        schema_ddl = "\n\n".join(ddl_parts)
                    except Exception:
                        schema_ddl = ""

                ws.db_conn_url   = url
                ws.schema_ddl    = schema_ddl
                ws.approval_status = None
                _set_state(ws, state=WorkspaceState.QUERY_READY, entry_mode="chat_direct", approval_status=None)
                st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# DB ready phase — shows DB info and lets user choose Modify or Chat

def render_db_ready_phase(ws: Workspace) -> None:
    _render_topbar(ws)
    _render_stepper(ws)

    if ws.entry_mode == "create":
        from feature1_app import render_results_phase
        render_results_phase(ws)
        return

    st.markdown("## ✅ Database Connected & Ready")
    st.success("Your database is registered and schema extracted. Choose what to do next.")

    info_col1, info_col2, info_col3 = st.columns(3)
    with info_col1:
        st.markdown(
            f'<div class="glass-card"><div style="color:#4a6a8a;font-size:.75rem;text-transform:uppercase;'
            f'letter-spacing:.06em;margin-bottom:.4rem">Blob Name</div>'
            f'<code style="color:#4fc3f7;font-size:.8rem">{ws.db_blob_name or "—"}</code></div>',
            unsafe_allow_html=True,
        )
    with info_col2:
        st.markdown(
            f'<div class="glass-card"><div style="color:#4a6a8a;font-size:.75rem;text-transform:uppercase;'
            f'letter-spacing:.06em;margin-bottom:.4rem">Connection</div>'
            f'<code style="color:#4fc3f7;font-size:.8rem">{(ws.db_conn_url or "—")[:40]}</code></div>',
            unsafe_allow_html=True,
        )
    with info_col3:
        st.markdown(
            f'<div class="glass-card"><div style="color:#4a6a8a;font-size:.75rem;text-transform:uppercase;'
            f'letter-spacing:.06em;margin-bottom:.4rem">Status</div>'
            f'<span class="conn-status-ok">✓ Ready</span></div>',
            unsafe_allow_html=True,
        )

    if ws.schema_ddl:
        with st.expander("🔍 Schema Preview", expanded=False):
            st.code(ws.schema_ddl, language="sql")

    st.divider()
    choice_col1, choice_col2 = st.columns(2, gap="large")
    with choice_col1:
        st.markdown(
            '<div class="glass-card" style="text-align:center"><span style="font-size:2rem">✏️</span><br>'
            '<strong style="color:#e0f4ff">Modify Database</strong><br>'
            '<span style="color:#4a6a8a;font-size:.85rem">Edit schema, insert data, add indexes</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if st.button("Go to Modify →", type="primary", use_container_width=True, key="db_ready_modify"):
            _go_to_modify(ws)

    with choice_col2:
        st.markdown(
            '<div class="glass-card" style="text-align:center"><span style="font-size:2rem">💬</span><br>'
            '<strong style="color:#e0f4ff">Chat With Database</strong><br>'
            '<span style="color:#4a6a8a;font-size:.85rem">Ask questions, run queries, explore data</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if st.button("Go to Chat →", use_container_width=True, key="db_ready_chat"):
            _go_to_chat(ws)


# Modified phase

def render_modified_phase(ws: Workspace) -> None:
    _render_topbar(ws)
    _render_stepper(ws)

    # Only render_modify_phase is exported from feature2_app — nothing else.
    from feature2_app import render_modify_phase

    prev_blob_name = ws.db_blob_name
    prev_blob_url  = ws.db_blob_url
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        ws = _ensure_active_blob(ws)
        if ws.db_blob_name != prev_blob_name or ws.db_blob_url != prev_blob_url:
            _save(ws)

    render_modify_phase(ws)

    st.divider()
    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("← Back to DB Ready", use_container_width=True, key="mod_back"):
            _set_state(ws, state=WorkspaceState.DB_READY, approval_status=None)
            st.rerun()
    with nav2:
        if st.button("Chat with Database →", type="primary", use_container_width=True, key="mod_to_chat"):
            _go_to_chat(ws)


# Query / chat phase

def render_query_phase(ws: Workspace) -> None:
    _render_topbar(ws)
    _render_stepper(ws)

    from feature3_app import render_chat_phase

    prev_conn = ws.db_conn_url
    prev_path = ws.db_local_path
    ws = _prepare_query_workspace(ws)
    if ws.db_conn_url != prev_conn or ws.db_local_path != prev_path:
        _save(ws)

    render_chat_phase(ws)


# Workspace lifecycle

def get_or_create_workspace() -> Workspace:
    if "workspace_id" not in st.session_state:
        st.session_state["workspace_id"] = str(uuid.uuid4())

    if "workspace" not in st.session_state:
        ws = Workspace(workspace_id=st.session_state["workspace_id"])
        ws.state = WorkspaceState.ENTRY
        st.session_state["workspace"] = ws

    return st.session_state["workspace"]


def clear_workspace() -> None:
    new_id = str(uuid.uuid4())
    st.session_state.clear()
    st.session_state["workspace_id"] = new_id
    ws = Workspace(workspace_id=new_id)
    ws.state = WorkspaceState.ENTRY
    st.session_state["workspace"] = ws
    save_workspace(ws)
    st.rerun()


def render_unknown_state(ws: Workspace) -> None:
    _render_topbar(ws)
    st.error(f"Unsupported workspace state: `{ws.state}`")
    if st.button("← Return to Entry", use_container_width=True):
        _set_state(ws, state=WorkspaceState.ENTRY, approval_status=None)
        st.rerun()


# Route table

ROUTES = {
    WorkspaceState.ENTRY:          render_entry_page,
    WorkspaceState.EMPTY:          render_empty_phase,
    WorkspaceState.SCHEMA_CREATED: render_schema_created_phase,
    WorkspaceState.DB_READY:       render_db_ready_phase,
    WorkspaceState.MODIFIED:       render_modified_phase,
    WorkspaceState.QUERY_READY:    render_query_phase,
}


# Main

def main() -> None:
    ws = get_or_create_workspace()
    render_sidebar(ws)
    ROUTES.get(ws.state, render_unknown_state)(ws)


if __name__ == "__main__":
    main()
