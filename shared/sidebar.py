"""
shared/sidebar.py — DB Studio premium SaaS sidebar
====================================================
Exports: render_sidebar(ws: Workspace) -> None
"""
from __future__ import annotations

import os
import streamlit as st

from shared.workspace import Workspace, WorkspaceState


# CSS — sidebar-specific overrides

def _inject_sidebar_css() -> None:
    st.markdown("""
    <style>
    /* ── Sidebar shell ────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        background: rgba(7, 13, 26, 0.98) !important;
        border-right: 1px solid rgba(79,195,247,0.1) !important;
        width: 280px !important;
    }

    /* ── Logo area ────────────────────────────────────────── */
    .sb-logo {
        padding: 1.6rem 1.2rem 1rem;
        border-bottom: 1px solid rgba(79,195,247,0.08);
        margin-bottom: 0.2rem;
    }
    .sb-logo-title {
        font-size: 1.3rem;
        font-weight: 700;
        color: #4fc3f7;
        letter-spacing: -0.02em;
        line-height: 1.2;
    }
    .sb-logo-title span { color: #e0f4ff; }
    .sb-logo-sub {
        font-size: 0.72rem;
        color: #2d4a6a;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-top: 2px;
    }

    /* ── Section headers ──────────────────────────────────── */
    .sb-section-header {
        font-size: 0.68rem;
        font-weight: 700;
        color: #1e3a5f;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 1rem 1.2rem 0.35rem;
    }

    /* ── Workspace card ───────────────────────────────────── */
    .sb-ws-card {
        margin: 0 0.8rem 0.6rem;
        background: rgba(13,27,42,0.7);
        border: 1px solid rgba(79,195,247,0.1);
        border-radius: 12px;
        padding: 0.9rem 1rem;
    }
    .sb-ws-id {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.72rem;
        color: #2d4a6a;
        margin-bottom: 0.5rem;
        word-break: break-all;
    }
    .sb-state-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }
    .sp-entry    { background:rgba(30,58,95,0.4);  color:#4fc3f7;  border:1px solid rgba(79,195,247,0.2); }
    .sp-creating { background:rgba(45,58,30,0.4);  color:#a3e635;  border:1px solid rgba(163,230,53,0.2); }
    .sp-ready    { background:rgba(30,58,38,0.4);  color:#4ade80;  border:1px solid rgba(74,222,128,0.2); }
    .sp-modified { background:rgba(58,30,45,0.4);  color:#f472b6;  border:1px solid rgba(244,114,182,0.2); }
    .sp-chat     { background:rgba(30,40,58,0.4);  color:#60a5fa;  border:1px solid rgba(96,165,250,0.2); }

    /* ── Progress tracker ─────────────────────────────────── */
    .sb-progress-list {
        padding: 0 0.8rem;
        margin-bottom: 0.4rem;
    }
    .sb-progress-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 7px 10px;
        border-radius: 9px;
        margin-bottom: 3px;
        font-size: 0.82rem;
        transition: background 0.15s;
    }
    .sb-progress-item.done   { background:rgba(74,222,128,0.07); color:#4ade80; }
    .sb-progress-item.active { background:rgba(79,195,247,0.1);  color:#4fc3f7; font-weight:600; }
    .sb-progress-item.locked { color:#1e3a5f; }
    .sb-progress-dot {
        width: 8px; height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .sb-progress-item.done   .sb-progress-dot { background: #4ade80; }
    .sb-progress-item.active .sb-progress-dot { background: #4fc3f7; box-shadow: 0 0 6px #4fc3f7; }
    .sb-progress-item.locked .sb-progress-dot { background: #1e3a5f; }

    /* ── Metadata grid ────────────────────────────────────── */
    .sb-meta-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        margin: 0 0.8rem 0.6rem;
    }
    .sb-meta-cell {
        background: rgba(13,27,42,0.6);
        border: 1px solid rgba(79,195,247,0.08);
        border-radius: 9px;
        padding: 0.6rem 0.75rem;
        text-align: center;
    }
    .sb-meta-value {
        font-size: 1.1rem;
        font-weight: 700;
        color: #e0f4ff;
        line-height: 1.2;
    }
    .sb-meta-label {
        font-size: 0.65rem;
        color: #1e3a5f;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-top: 2px;
    }

    /* ── Conn status inline ───────────────────────────────── */
    .sb-conn-ok  { display:inline-flex;align-items:center;gap:5px;color:#4ade80;font-size:.75rem;font-weight:600; }
    .sb-conn-no  { display:inline-flex;align-items:center;gap:5px;color:#1e3a5f;font-size:.75rem; }

    /* ── Divider ──────────────────────────────────────────── */
    .sb-divider {
        height: 1px;
        background: rgba(79,195,247,0.07);
        margin: 0.6rem 0.8rem;
    }

    /* ── Footer ───────────────────────────────────────────── */
    .sb-footer {
        padding: 0.8rem 1.2rem;
        border-top: 1px solid rgba(79,195,247,0.07);
        margin-top: auto;
    }
    .sb-footer-text {
        font-size: 0.68rem;
        color: #1e3a5f;
        line-height: 1.6;
    }
    </style>
    """, unsafe_allow_html=True)



# State helpers

_STATE_PILL = {
    WorkspaceState.ENTRY:          ("sp-entry",    "⬜  Entry"),
    WorkspaceState.EMPTY:          ("sp-creating", "⏳  Working"),
    WorkspaceState.SCHEMA_CREATED: ("sp-creating", "👁  Review"),
    WorkspaceState.DB_READY:       ("sp-ready",    "✅  DB Ready"),
    WorkspaceState.MODIFIED:       ("sp-modified", "✏️  Modified"),
    WorkspaceState.QUERY_READY:    ("sp-chat",     "💬  Chat"),
}

_PROGRESS_STEPS = [
    (WorkspaceState.EMPTY,          WorkspaceState.SCHEMA_CREATED,             "🆕", "Create DB"),
    (WorkspaceState.SCHEMA_CREATED, WorkspaceState.DB_READY,                   "👁", "Schema Review"),
    (WorkspaceState.DB_READY,       WorkspaceState.MODIFIED,                   "✅", "DB Ready"),
    (WorkspaceState.MODIFIED,       WorkspaceState.QUERY_READY,                "✏️", "Modify DB"),
    (WorkspaceState.QUERY_READY,    None,                                       "💬", "Chat With DB"),
]

_STATE_ORDER = [
    WorkspaceState.ENTRY,
    WorkspaceState.EMPTY,
    WorkspaceState.SCHEMA_CREATED,
    WorkspaceState.DB_READY,
    WorkspaceState.MODIFIED,
    WorkspaceState.QUERY_READY,
]

def _state_index(state: WorkspaceState) -> int:
    try:
        return _STATE_ORDER.index(state)
    except ValueError:
        return 0



# Section renderers

def _render_logo() -> None:
    st.markdown(
        """
        <div class="sb-logo">
            <div class="sb-logo-title">🗄️ DB<span>Studio</span></div>
            <div class="sb-logo-sub">AI Database Platform</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_workspace_card(ws: Workspace) -> None:
    wid = getattr(ws, "workspace_id", "—")
    wid_display = wid[:20] + "…" if len(wid) > 20 else wid
    pill_cls, pill_label = _STATE_PILL.get(ws.state, ("sp-entry", "—"))

    conn_html = ""
    if ws.db_conn_url:
        conn_html = '<div style="margin-top:6px"><span class="sb-conn-ok">● Connected</span></div>'
    elif ws.db_blob_name:
        conn_html = '<div style="margin-top:6px"><span class="sb-conn-ok">● Blob linked</span></div>'
    elif ws.state not in (WorkspaceState.ENTRY, WorkspaceState.EMPTY):
        conn_html = '<div style="margin-top:6px"><span class="sb-conn-no">○ No connection</span></div>'

    st.markdown(
        f"""
        <div class="sb-ws-card">
            <div class="sb-ws-id">ws · {wid_display}</div>
            <div class="sb-state-pill {pill_cls}">{pill_label}</div>
            {conn_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_progress_tracker(ws: Workspace) -> None:
    current_idx = _state_index(ws.state)

    st.markdown('<div class="sb-section-header">Pipeline</div>', unsafe_allow_html=True)
    st.markdown('<div class="sb-progress-list">', unsafe_allow_html=True)

    items_html = ""
    for i, (trigger_state, _, icon, label) in enumerate(_PROGRESS_STEPS):
        step_idx = _state_index(trigger_state)
        if current_idx > step_idx:
            cls = "done"
            dot_icon = "✓"
        elif current_idx == step_idx:
            cls = "active"
            dot_icon = icon
        else:
            cls = "locked"
            dot_icon = icon

        items_html += (
            f'<div class="sb-progress-item {cls}">'
            f'<div class="sb-progress-dot"></div>'
            f'<span>{label}</span>'
            f'</div>'
        )

    st.markdown(
        f'<div class="sb-progress-list">{items_html}</div>',
        unsafe_allow_html=True,
    )


def _render_metadata(ws: Workspace) -> None:
    """Show DB metadata when available."""
    has_schema  = bool(ws.schema_ddl or ws.schema_json)
    has_queries = bool(ws.query_set)
    has_mods    = bool(getattr(ws, "modification_history", None))

    if not (has_schema or has_queries or has_mods):
        return

    st.markdown('<div class="sb-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sb-section-header">Database</div>', unsafe_allow_html=True)

    # Count tables from schema_json or schema_ddl
    table_count = "—"
    if ws.schema_json and isinstance(ws.schema_json, dict):
        tables = ws.schema_json.get("tables", [])
        table_count = str(len(tables))
    elif ws.schema_ddl:
        table_count = str(ws.schema_ddl.upper().count("CREATE TABLE"))

    # Count queries
    query_count = "—"
    if ws.query_set and isinstance(ws.query_set, dict):
        crud = ws.query_set.get("crud_queries", {})
        analytical = ws.query_set.get("analytical_queries", [])
        total = sum(len(v) for v in crud.values()) + len(analytical)
        query_count = str(total)

    # Count modifications
    mod_count = str(len(ws.modification_history)) if ws.modification_history else "0"

    st.markdown(
        f"""
        <div class="sb-meta-grid">
            <div class="sb-meta-cell">
                <div class="sb-meta-value">{table_count}</div>
                <div class="sb-meta-label">Tables</div>
            </div>
            <div class="sb-meta-cell">
                <div class="sb-meta-value">{query_count}</div>
                <div class="sb-meta-label">Queries</div>
            </div>
            <div class="sb-meta-cell">
                <div class="sb-meta-value">{mod_count}</div>
                <div class="sb-meta-label">Edits</div>
            </div>
            <div class="sb-meta-cell">
                <div class="sb-meta-value">{"3NF" if has_schema else "—"}</div>
                <div class="sb-meta-label">Normal form</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Blob / connection info
    if ws.db_blob_name:
        with st.expander("🔗 Connection details", expanded=False):
            st.caption(f"**Blob:** `{ws.db_blob_name}`")
            if ws.db_conn_url:
                st.caption(f"**URL:** `{ws.db_conn_url[:50]}`")
            if ws.db_blob_url:
                st.caption(f"**Public URL:** `{ws.db_blob_url[:50]}`")


def _render_quick_actions(ws: Workspace) -> None:
    st.markdown('<div class="sb-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sb-section-header">Quick Actions</div>', unsafe_allow_html=True)
    st.markdown('<div style="padding:0 0.8rem 0.4rem">', unsafe_allow_html=True)

    # Download DB
    db_path = ws.db_local_path
    if db_path and os.path.exists(db_path):
        try:
            with open(db_path, "rb") as f:
                st.download_button(
                    "⬇️ Download DB",
                    data=f.read(),
                    file_name="database.db",
                    mime="application/x-sqlite3",
                    use_container_width=True,
                    key="sb_download_db",
                )
        except Exception:
            pass

    # View schema
    if ws.schema_ddl:
        with st.expander("📋 View Schema", expanded=False):
            st.code(ws.schema_ddl[:2000] + ("…" if len(ws.schema_ddl) > 2000 else ""), language="sql")

    # New workspace
    if st.button("🔄 New Workspace", use_container_width=True, key="sb_new_ws"):
        from shared.blob_storage import save_workspace
        import uuid
        new_id = str(uuid.uuid4())
        st.session_state.clear()
        st.session_state["workspace_id"] = new_id
        from shared.workspace import Workspace as _Ws
        new_ws = _Ws(workspace_id=new_id)
        new_ws.state = WorkspaceState.ENTRY
        st.session_state["workspace"] = new_ws
        save_workspace(new_ws)
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def _render_footer() -> None:
    st.markdown('<div class="sb-divider"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="sb-footer">
            <div class="sb-footer-text">
                🔒 Human-in-the-Loop · 3NF Schemas<br>
                Azure Blob · LangChain · Streamlit<br>
                <span style="color:#0d1b2a">DB Studio v1.0</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



# Main export

def render_sidebar(ws: Workspace) -> None:
    _inject_sidebar_css()

    with st.sidebar:
        _render_logo()
        _render_workspace_card(ws)
        _render_progress_tracker(ws)
        _render_metadata(ws)
        _render_quick_actions(ws)
        _render_footer()