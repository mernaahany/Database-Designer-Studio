"""
feature3_app.py — Feature 3: Chat DB (UI layer)
Implements the Streamlit UI for the chat-with-database feature, including the chat interface, clarification flow, human review, and export options. It orchestrates user interactions and calls the core logic in run_feature_3() to process queries and update the workspace.
"""
from __future__ import annotations

import logging
import uuid

import pandas as pd
import streamlit as st

from shared.blob_storage import save_workspace
from shared.workspace import Workspace, WorkspaceState

from Features.Feature1_create_db.models import DatabaseSchema
from Features.Feature3_chat_db import run_feature_3
from Features.Feature3_chat_db.observability.tracing import NodeTracer

logger = logging.getLogger(__name__)


def _inject_css() -> None:
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
.stApp {
    background: linear-gradient(135deg, #0a0f1e 0%, #0d1b2a 50%, #0a1628 100%);
}
.glass-card {
    background: rgba(30,58,95,0.25);
    border: 1px solid rgba(79,195,247,0.18);
    border-radius: 14px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
    backdrop-filter: blur(10px);
}
.glass-card h3, .glass-card h4 {
    color: #e0f4ff;
    margin: 0 0 0.45rem 0;
}
.glass-card p, .glass-card li {
    color: #a0c4e8;
}
.step-chip {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(79,195,247,0.12);
    border: 1px solid rgba(79,195,247,0.22);
    margin-right: 0.45rem;
    margin-bottom: 0.45rem;
    font-size: 0.8rem;
    color: #d7ecff;
}
.chat-row {
    display: flex;
    margin: 0.75rem 0;
}
.chat-row.user {
    justify-content: flex-end;
}
.chat-row.assistant {
    justify-content: flex-start;
}
.chat-bubble {
    max-width: 82%;
    border-radius: 18px;
    padding: 0.9rem 1rem;
    line-height: 1.55;
    border: 1px solid rgba(79,195,247,0.15);
    box-shadow: 0 10px 30px rgba(0,0,0,0.18);
}
.chat-row.user .chat-bubble {
    background: linear-gradient(135deg, rgba(49, 103, 206, 0.95), rgba(37, 82, 171, 0.92));
    color: #f7fbff;
    border-bottom-right-radius: 6px;
}
.chat-row.assistant .chat-bubble {
    background: rgba(18, 31, 52, 0.9);
    color: #d7ecff;
    border-bottom-left-radius: 6px;
}
.chat-meta {
    display: block;
    margin-bottom: 0.4rem;
    font-size: 0.75rem;
    opacity: 0.75;
    letter-spacing: 0.02em;
}
.sql-block {
    background: rgba(8, 14, 26, 0.85);
    border: 1px solid rgba(79,195,247,0.15);
    border-radius: 12px;
    padding: 0.9rem 1rem;
}
.approval-banner {
    background: linear-gradient(90deg, rgba(240,165,0,0.15), rgba(240,165,0,0.05));
    border: 1px solid rgba(240,165,0,0.4);
    border-left: 4px solid #f0a500;
    border-radius: 10px;
    padding: 1.2rem 1.5rem;
    margin: 1rem 0;
    color: #f0a500;
    font-weight: 600;
}

/* ── Latency bars (matches app.py style) ── */
.lat-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem;
    color: #7a8aaa;
}
.lat-bg   { flex: 1; background: #1c2333; border-radius: 2px; height: 4px; }
.lat-fill { height: 4px; border-radius: 2px; background: #4f8eff; }

pre, code { font-family: 'JetBrains Mono', monospace !important; }
h1, h2, h3 { color: #e0f4ff !important; }
[data-testid="metric-container"] {
    background: rgba(30,58,95,0.3) !important;
    border: 1px solid rgba(79,195,247,0.2) !important;
    border-radius: 10px !important;
    padding: 0.8rem !important;
}
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] {
    background: rgba(30,58,95,0.3);
    border-radius: 8px 8px 0 0;
    color: #a0c4e8;
    font-weight: 600;
}
.stTabs [aria-selected="true"] {
    background: rgba(79,195,247,0.2);
    color: #4fc3f7 !important;
}
</style>
""",
        unsafe_allow_html=True,
    )


def _save(ws: Workspace) -> None:
    st.session_state["workspace"] = ws
    save_workspace(ws)


def _reset_workspace() -> None:
    new_ws = Workspace(workspace_id=str(uuid.uuid4()))
    st.session_state.clear()
    st.session_state["workspace_id"] = new_ws.workspace_id
    st.session_state["workspace"] = new_ws
    save_workspace(new_ws)


def _schema_from_workspace(ws: Workspace) -> DatabaseSchema | None:
    schema_payload = getattr(ws, "schema_json", None) or getattr(ws, "schema_json_data", None)
    if not schema_payload:
        return None
    try:
        return DatabaseSchema.model_validate(schema_payload)
    except Exception:
        logger.exception("Failed to rehydrate schema data for Feature 3 UI.")
        return None


def _render_step_chips(*labels: str) -> None:
    chips = "".join(f"<div class='step-chip'>{label}</div>" for label in labels)
    st.markdown(chips, unsafe_allow_html=True)


def _database_source_label(ws: Workspace) -> str:
    if ws.db_blob_name:
        return f"Blob database: {ws.db_blob_name}"
    if ws.db_blob_url:
        return "Blob URL connected"
    if ws.db_local_path:
        return "Local SQLite database"
    if ws.db_conn_url:
        return ws.db_conn_url
    return "No connected database"


def _previous_query_count(ws: Workspace) -> int:
    explicit = getattr(ws, "query_history", None)
    if isinstance(explicit, list):
        return len(explicit)
    return len([msg for msg in ws.history if msg.get("role") == "user"])


def _first_present(mapping: dict | None, keys: list[str], default=None):
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _normalize_rows(rows, columns) -> pd.DataFrame | None:
    if rows is None:
        return None
    if isinstance(rows, pd.DataFrame):
        return rows
    if not isinstance(rows, list) or not rows:
        return None
    if all(isinstance(row, dict) for row in rows):
        return pd.DataFrame(rows)
    if all(isinstance(row, (list, tuple)) for row in rows):
        if isinstance(columns, list) and columns:
            return pd.DataFrame(rows, columns=columns)
        return pd.DataFrame(rows)
    try:
        return pd.DataFrame(rows)
    except Exception:
        logger.exception("Failed to normalize query rows into a dataframe.")
        return None


def _get_result(ws: Workspace) -> dict:
    result = ws.feature3_data or {}
    sql     = result.get("sql", "")
    rows    = result.get("rows") or []
    columns = result.get("columns") or []
    summary = result.get("nl_response", "")
    error   = result.get("error")
    trace   = result.get("trace") or []

    df = _normalize_rows(rows, columns)
    normalized_columns = list(df.columns) if df is not None else (columns if isinstance(columns, list) else [])
    normalized_rows    = df.to_dict(orient="records") if df is not None else rows
    row_count = result.get("row_count", 0)
    if not isinstance(row_count, int):
        row_count = len(df.index) if df is not None else (len(normalized_rows) if isinstance(normalized_rows, list) else 0)

    return {
        "sql":     sql,
        "rows":    normalized_rows,
        "columns": normalized_columns,
        "row_count": row_count,
        "summary": summary,
        "error":   error,
        "trace":   trace,
        "df":      df,
    }


def _render_status_card(ws: Workspace, schema: DatabaseSchema | None) -> None:
    tables_count = len(schema.tables) if schema else 0
    query_count  = _previous_query_count(ws)
    st.markdown(
        f"""
<div class="glass-card">
    <h4>Database Status</h4>
    <p><strong>Source:</strong> {_database_source_label(ws)}</p>
    <p><strong>Tables:</strong> {tables_count}</p>
    <p><strong>Previous queries:</strong> {query_count}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_history(ws: Workspace) -> None:
    if not ws.history:
        st.info("No conversation yet. Ask your database a question to get started.")
        return
    for message in ws.history:
        role    = message.get("role", "")
        content = str(message.get("content", "")).strip()
        if not content or role not in {"user", "assistant"}:
            continue
        role_label = "You" if role == "user" else "Assistant"
        st.markdown(
            f"""
<div class="chat-row {role}">
    <div class="chat-bubble">
        <span class="chat-meta">{role_label}</span>
        {content}
    </div>
</div>
""",
            unsafe_allow_html=True,
        )


def _render_schema_panel(ws: Workspace, schema: DatabaseSchema | None) -> None:
    with st.expander("Connected Database Schema", expanded=False):
        if ws.schema_ddl:
            st.code(ws.schema_ddl, language="sql")
        elif schema:
            for table in schema.tables:
                st.markdown(f"- **{table.name}** ({len(table.columns)} columns)")
        else:
            st.caption("Schema not available.")


def _submit_query(ws: Workspace, user_query: str) -> None:
    history = list(ws.history)
    history.append({"role": "user", "content": user_query})
    ws.history         = history
    ws.approval_status = None
    updated = _run_pipeline(ws, "Thinking...")
    if updated is not None:
        _save(updated)
        st.rerun()


def _run_pipeline(ws: Workspace, spinner_msg: str = "Running...") -> Workspace | None:
    try:
        with st.spinner(spinner_msg):
            # Record a high-level trace for the Feature 3 pipeline execution
            with NodeTracer(ws.__dict__, node_name="feature3_pipeline", input_fields=["db_conn_url", "db_blob_name", "history"]) as tracer:
                updated_ws = run_feature_3(ws)
                tracer.set_output({"has_result": bool(getattr(updated_ws, "feature3_data", None))})
        return updated_ws
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        logger.exception("Feature 3 pipeline failed")
        return None


def _render_chat_input(ws: Workspace) -> None:
    with st.form("f3_chat_form", clear_on_submit=True):
        user_query = st.text_area(
            "Ask your database anything...",
            placeholder=(
                "Examples:\n"
                "- Show all customers from Cairo\n"
                "- What are the top selling products?\n"
                "- Find employees hired after 2023\n"
                "- Show monthly revenue"
            ),
            height=150,
        )
        submitted = st.form_submit_button("Ask Database", type="primary")

    if submitted:
        if not user_query.strip():
            st.warning("Please enter a question first.")
            return
        _submit_query(ws, user_query.strip())


def _render_clarify_phase(ws: Workspace) -> None:
    clarification = (ws.feature3_data or {}).get("clarification_message") or "Could you clarify your question?"
    st.markdown(
        f'<div class="approval-banner">{clarification}</div>',
        unsafe_allow_html=True,
    )
    with st.form("f3_clarify_form", clear_on_submit=True):
        answer    = st.text_area("Provide the missing detail", height=110)
        submitted = st.form_submit_button("Continue", type="primary")

    if submitted:
        if not answer.strip():
            st.warning("Please provide a clarification before continuing.")
            return
        history = list(ws.history)
        history.append({"role": "user", "content": answer.strip()})
        ws.history         = history
        ws.approval_status = "clarifying"
        updated = _run_pipeline(ws, "Continuing query...")
        if updated is not None:
            _save(updated)
            st.rerun()


def _render_approval_phase(ws: Workspace) -> None:
    sql = (ws.feature3_data or {}).get("sql", "")
    st.markdown(
        """
<div class="approval-banner">
Approval required before executing this query because it may modify data.
</div>
""",
        unsafe_allow_html=True,
    )
    if sql:
        st.code(sql, language="sql")

    approve_col, cancel_col = st.columns(2)
    with approve_col:
        if st.button("Approve & Execute", type="primary", use_container_width=True):
            ws.approval_status = "approved"
            # Reuse _run_pipeline which includes tracing and error handling
            updated_ws = _run_pipeline(ws, "Executing query...")
            if updated_ws is not None:
                _save(updated_ws)
                st.rerun()
            else:
                st.error("Execution failed, see logs for details.")
    with cancel_col:
        if st.button("Cancel Query", use_container_width=True):
            history = list(ws.history)
            history.append({"role": "assistant", "content": "Query cancelled. Ask another question when you're ready."})
            ws.history         = history
            ws.approval_status = None
            _save(ws)
            st.rerun()


def _render_error_panel(ws: Workspace) -> None:
    result = _get_result(ws)
    error  = result.get("error") or "The query could not be completed."
    st.error(error)


def _render_nl_response(ws: Workspace) -> None:
    result   = _get_result(ws)
    response = result.get("summary") or ""
    if not response:
        return
    st.markdown(
        f"""
<div class="glass-card">
    <h4>Assistant Response</h4>
    <p>{response}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_sql_output(ws: Workspace) -> None:
    result = _get_result(ws)
    sql    = result.get("sql") or ""
    if sql:
        st.code(sql, language="sql")
    else:
        st.info("No SQL generated yet.")


def _render_query_results(ws: Workspace) -> None:
    result    = _get_result(ws)
    rows      = result.get("rows")
    df        = result.get("df")
    summary   = result.get("summary") or ""
    row_count = result.get("row_count")
    error     = result.get("error")

    if error:
        st.error(error)
        return

    if summary:
        st.markdown(
            f"<div class='glass-card'><h4>Execution Summary</h4><p>{summary}</p></div>",
            unsafe_allow_html=True,
        )

    if isinstance(df, pd.DataFrame) and not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        count = row_count if isinstance(row_count, int) else len(rows)
        st.caption(f"{count:,} row(s) returned")
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Results as CSV",
            data=csv_bytes,
            file_name="query_results.csv",
            mime="text/csv",
        )
    else:
        st.info("No query results are available yet.")


# ─────────────────────────────────────────────────────────────
# Latency — same bar chart as app.py diagnostics panel
# ─────────────────────────────────────────────────────────────

def _fmt_ms(ms: float | None) -> str:
    """Format a millisecond value into a human-readable string."""
    if ms is None:
        return "—"
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.2f} s"


def _render_latency(lat: dict) -> None:
    """
    Horizontal proportional bar chart for all numeric latency keys.
    Skips internal bookkeeping entries. Mirrors app.py's _render_latency exactly:
      - bars scaled to the slowest node
      - ms values right-aligned
      - values shown as seconds when ≥ 1 000 ms
    """
    skip = {
        "schema_cache_hit", "schema_cache_miss",
        "correction_loop_count", "correction_total_time", "top_3_nodes",
    }
    rows = {
        k: float(v)
        for k, v in lat.items()
        if isinstance(v, (int, float)) and float(v) > 0 and k not in skip
    }
    if not rows:
        st.caption("No latency data available for this query.")
        return

    mx = max(rows.values()) or 1.0
    for k, v in sorted(rows.items(), key=lambda x: -x[1]):
        pct = int(v / mx * 100)
        st.markdown(
            f'<div class="lat-row">'
            f'<span style="width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{k}</span>'
            f'<div class="lat-bg"><div class="lat-fill" style="width:{pct}%"></div></div>'
            f'<span style="width:72px;text-align:right">{_fmt_ms(v)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_agent_trace(ws: Workspace) -> None:
    """
    Agent Trace tab — latency breakdown only, no raw trace log.
    Matches the Diagnostics > Latency breakdown expander in app.py.
    """
    f3  = ws.feature3_data or {}
    lat = f3.get("latency_breakdown_ms") or {}

    _render_latency(lat)

    # Surface the top-3 slowest nodes callout when available
    top3 = lat.get("top_3_nodes")
    if top3:
        st.caption(f"Slowest nodes: {top3}")


def _render_query_history(ws: Workspace) -> None:
    explicit = ws.query_history
    if isinstance(explicit, list) and explicit:
        for index, item in enumerate(explicit, start=1):
            if isinstance(item, dict):
                role    = item.get("role", "assistant")
                content = str(item.get("content", item.get("query", item.get("response", "")))).strip()
                if not content:
                    continue
                role_label = "You" if role == "user" else "Assistant"
                st.markdown(
                    f"""
<div class="chat-row {role if role in {'user', 'assistant'} else 'assistant'}">
    <div class="chat-bubble">
        <span class="chat-meta">{role_label}</span>
        {content}
    </div>
</div>
""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"{index}. {item}")
        return

    chat_messages = [
        msg for msg in ws.history
        if msg.get("role") in {"user", "assistant"} and msg.get("content")
    ]
    if not chat_messages:
        st.info("No previous queries yet.")
        return

    for message in chat_messages:
        role       = message.get("role", "assistant")
        role_label = "You" if role == "user" else "Assistant"
        content    = str(message.get("content", "")).strip()
        st.markdown(
            f"""
<div class="chat-row {role}">
    <div class="chat-bubble">
        <span class="chat-meta">{role_label}</span>
        {content}
    </div>
</div>
""",
            unsafe_allow_html=True,
        )


def render_chat_phase(ws: Workspace) -> None:
    _inject_css()

    schema = _schema_from_workspace(ws)

    st.markdown("## 💬 Chat With Database")
    st.caption("Ask questions in natural language and get SQL-powered answers instantly")
    _render_step_chips(
        "Connect database",
        "Ask a natural-language question",
        "Review SQL-powered results",
    )

    if not any([ws.db_blob_url, ws.db_local_path, ws.db_blob_name, ws.db_conn_url]):
        st.error("No database source is connected yet. Create or upload a database first.")
        return

    if not ws.db_conn_url:
        st.warning("A database source is available, but the live connection is not ready yet.")
        return

    stats_col, metrics_col = st.columns([1.2, 2], gap="large")
    with stats_col:
        _render_status_card(ws, schema)
    with metrics_col:
        c1, c2, c3 = st.columns(3)
        c1.metric("Tables", len(schema.tables) if schema else 0)
        c2.metric("Previous Queries", _previous_query_count(ws))
        last_result = _get_result(ws)
        rows        = last_result.get("rows")
        row_count   = last_result.get("row_count")
        if isinstance(row_count, int):
            result_count = row_count
        elif isinstance(rows, list):
            result_count = len(rows)
        else:
            result_count = 0
        c3.metric("Last Result Rows", result_count)
        _render_schema_panel(ws, schema)

    tab_chat, tab_sql, tab_results, tab_trace, tab_history = st.tabs(
        ["Chat", "SQL Output", "Query Results", "Agent Trace", "Query History"]
    )

    with tab_chat:
        _render_history(ws)
        st.divider()
        status = ws.approval_status
        if status == "clarifying":
            _render_nl_response(ws)
            _render_clarify_phase(ws)
        elif status == "awaiting":
            _render_nl_response(ws)
            _render_approval_phase(ws)
        else:
            if status == "error":
                _render_error_panel(ws)
            _render_nl_response(ws)
            _render_chat_input(ws)

    with tab_sql:
        _render_sql_output(ws)

    with tab_results:
        _render_query_results(ws)

    with tab_trace:
        _render_agent_trace(ws)

    with tab_history:
        _render_query_history(ws)

    st.divider()
    nav_modify, nav_new = st.columns(2)
    with nav_modify:
        if st.button("Back to Modify DB", use_container_width=True):
            ws.state = WorkspaceState.MODIFIED
            _save(ws)
            st.rerun()
    with nav_new:
        if st.button("Start New Workspace", use_container_width=True):
            _reset_workspace()
            st.rerun()