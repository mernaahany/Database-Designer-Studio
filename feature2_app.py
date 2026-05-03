"""
feature2_app.py — Feature 2: Modify DB  (UI layer)
implements the Streamlit UI for the database modification feature, including the chat interface, clarification flow, human review, and export options. It orchestrates user interactions and calls the core logic in __init__.py to run the modification pipeline.
"""
from __future__ import annotations
import logging
import streamlit as st
import streamlit.components.v1 as components

from shared.import_paths import bootstrap_feature_paths
bootstrap_feature_paths()

from shared.workspace import Workspace, WorkspaceState
from shared.blob_storage import save_workspace

logger = logging.getLogger(__name__)


# UI-side caches to reduce repeated DB introspection and row-count calls
@st.cache_data(ttl=120)
def _cached_get_table_row_counts(blob_name: str):
    from Features.Feature2_modify_db.utils.db_utils import get_table_row_counts
    return get_table_row_counts(blob_name)


@st.cache_data(ttl=300)
def _cached_extract_schema_f2(blob_name: str):
    from Features.Feature2_modify_db.utils.db_utils import extract_schema
    return extract_schema(blob_name)


# CSS

def _inject_css() -> None:
    st.markdown("""
    <style>
    .chat-user      { background:#E8F4F8; border-left:4px solid #2E86AB;
                      padding:.75rem 1rem; border-radius:8px; margin:.5rem 0; color:black; }
    .chat-assistant { background:#F0F7E6; border-left:4px solid #27ae60;
                      padding:.75rem 1rem; border-radius:8px; margin:.5rem 0; color:black; }
    .chat-system    { background:#FFF8E1; border-left:4px solid #F39C12;
                      padding:.75rem 1rem; border-radius:8px; margin:.5rem 0; color:black; }
    .plan-box       { background:#F8F9FA; border:1px solid #dee2e6;
                      border-radius:10px; padding:1.2rem; margin:1rem 0; color:black; }
    .pill-ok   { background:#d4edda; color:#155724; padding:3px 10px;
                 border-radius:20px; font-size:.8rem; font-weight:600; }
    .pill-warn { background:#fff3cd; color:#856404; padding:3px 10px;
                 border-radius:20px; font-size:.8rem; font-weight:600; }
    .pill-err  { background:#f8d7da; color:#721c24; padding:3px 10px;
                 border-radius:20px; font-size:.8rem; font-weight:600; }
    .val-error   { background:#fff0f0; border-left:4px solid #dc3545;
                   padding:.6rem .9rem; border-radius:6px; margin:.3rem 0;
                   color:#721c24; font-size:.88rem; }
    .val-warning { background:#fffbec; border-left:4px solid #ffc107;
                   padding:.6rem .9rem; border-radius:6px; margin:.3rem 0;
                   color:#6d5100; font-size:.88rem; }
    .section-divider { border:none; border-top:2px solid #e9ecef; margin:1.2rem 0; }
    .export-card {
        background: rgba(13,27,42,0.7);
        border: 1px solid rgba(79,195,247,0.15);
        border-radius: 12px;
        padding: 1.2rem 1.4rem;
        margin: 1rem 0;
    }
    </style>
    """, unsafe_allow_html=True)


# Internal helpers

def _save(ws: Workspace) -> None:
    st.session_state["workspace"] = ws
    save_workspace(ws)


def _add_message(ws: Workspace, role: str, content: str) -> Workspace:
    msgs = list(ws.feature2_data.get("messages", []))
    msgs.append({"role": role, "content": content})
    ws.feature2_data = {**ws.feature2_data, "messages": msgs}
    return ws


def _render_messages(ws: Workspace) -> None:
    for msg in ws.feature2_data.get("messages", []):
        role, content = msg["role"], msg["content"]
        if role == "user":
            st.markdown(
                f'<div class="chat-user">👤 <b>You:</b> {content}</div>',
                unsafe_allow_html=True,
            )
        elif role == "assistant":
            st.markdown(
                f'<div class="chat-assistant">🤖 <b>Assistant:</b> {content}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="chat-system">⚙️ {content}</div>',
                unsafe_allow_html=True,
            )


def _clear_import_state() -> None:
    for key in ["f2_import_df", "f2_import_file_name", "f2_import_table",
                "f2_import_validation", "f2_import_ready", "f2_import_on_conflict"]:
        st.session_state.pop(key, None)


# Chat tab — sub-phases

def _render_chat_input(ws: Workspace) -> None:
    from Features.Feature2_modify_db.utils.memory import format_history_for_display
    from Features.Feature2_modify_db import run_feature_2

    if ws.modification_history:
        with st.expander(f"📝 Modification history ({len(ws.modification_history)})", expanded=False):
            st.markdown(
                format_history_for_display(ws.modification_history),
                unsafe_allow_html=True,
            )

    with st.form("f2_chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Describe the modification you want to make:",
            placeholder="e.g. Add an 'email' column to the users table, unique and not null",
            height=100,
        )
        submitted = st.form_submit_button("Send →", type="primary")

    if submitted and user_input.strip():
        ws = _add_message(ws, "user", user_input.strip())
        ws.feature2_data = {
            **ws.feature2_data,
            "user_request":          user_input.strip(),
            "clarification_answers": [],
        }
        with st.spinner("🔍 Analysing your request…"):
            try:
                ws = run_feature_2(ws)
                _save(ws)
                st.rerun()
            except Exception as exc:
                st.error(f"Pipeline error: {exc}")
                logger.exception("Feature 2 pipeline failed")
        return

    st.markdown("**💡 Quick suggestions:**")
    suggestions = [
        "Create a new table", "Add a column", "Create a foreign key",
        "Insert sample data", "Add an index", "Create a view",
    ]
    cols = st.columns(len(suggestions))
    for i, sug in enumerate(suggestions):
        with cols[i]:
            if st.button(sug, key=f"f2_sug_{i}", use_container_width=True):
                from Features.Feature2_modify_db import run_feature_2 as _rf2
                ws = _add_message(ws, "user", sug)
                ws.feature2_data = {
                    **ws.feature2_data,
                    "user_request":          sug,
                    "clarification_answers": [],
                }
                ws = _rf2(ws)
                _save(ws)
                st.rerun()


def _render_clarify_phase(ws: Workspace) -> None:
    from Features.Feature2_modify_db import run_feature_2

    q = ws.feature2_data.get("clarification_question", "")

    with st.form("f2_clarify_form", clear_on_submit=True):
        st.markdown(f"**❓ {q}**")
        answer = st.text_area("Your answer:", height=80)
        submitted = st.form_submit_button("Submit Answer →", type="primary")

    if submitted and answer.strip():
        ws = _add_message(ws, "user", answer.strip())
        answers = list(ws.feature2_data.get("clarification_answers", []))
        answers.append({"q": q, "a": answer.strip()})
        ws.feature2_data = {
            **ws.feature2_data,
            "clarification_answers":  answers,
            "clarification_question": "",
        }
        ws.approval_status = "clarifying"
        with st.spinner("Continuing pipeline…"):
            ws = run_feature_2(ws)
            _save(ws)
            st.rerun()


def _render_human_review_phase(ws: Workspace) -> None:
    from Features.Feature2_modify_db import run_feature_2

    plan = ws.feature2_data.get("pending_plan") or {}
    val  = ws.feature2_data.get("pending_validation") or {}

    st.markdown("### 📋 Review Proposed Changes")

    if val.get("approved"):
        st.markdown('<span class="pill-ok">✅ Validated</span>', unsafe_allow_html=True)
    else:
        issues = val.get("issues", [])
        badge  = "pill-warn" if issues else "pill-err"
        st.markdown(f'<span class="{badge}">⚠️ Validation concerns</span>', unsafe_allow_html=True)
        for issue in issues:
            st.warning(issue)

    st.markdown(
        f'<div class="plan-box"><b>📝 Description:</b><br>'
        f'{plan.get("description", "—")}</div>',
        unsafe_allow_html=True,
    )

    for w in plan.get("warnings", []):
        st.warning(f"⚠️ {w}")

    st.markdown("**🔧 SQL to be executed:**")
    for i, sql in enumerate(plan.get("sql_statements", []), 1):
        st.code(f"-- Statement {i}\n{sql}", language="sql")

    st.markdown("---")
    col_approve, col_edit = st.columns(2)

    with col_approve:
        if st.button("✅ Approve & Apply", type="primary", use_container_width=True):
            ws.approval_status = "approved"
            with st.spinner("⚡ Applying changes to database…"):
                ws = run_feature_2(ws)
            if ws.approval_status != "error":
                desc = plan.get("description", "Modification applied.")
                ws = _add_message(ws, "assistant", f"✅ Done! {desc}")
            _save(ws)
            st.rerun()

    with col_edit:
        with st.expander("✏️ Request Changes"):
            with st.form("f2_edit_form", clear_on_submit=True):
                feedback = st.text_area("What would you like changed?", height=100)
                if st.form_submit_button("Send Revision Request"):
                    if feedback.strip():
                        ws = _add_message(ws, "user", f"🔄 Edit request: {feedback.strip()}")
                        answers = list(ws.feature2_data.get("clarification_answers", []))
                        answers.append({"q": "Human review feedback", "a": feedback.strip()})
                        ws.feature2_data = {**ws.feature2_data, "clarification_answers": answers}
                        ws.approval_status = "revising"
                        with st.spinner("Revising plan…"):
                            ws = run_feature_2(ws)
                        _save(ws)
                        st.rerun()


def _render_done_phase(ws: Workspace) -> None:
    for msg in reversed(ws.feature2_data.get("messages", [])):
        if msg["role"] == "assistant":
            st.markdown(
                f'<div class="chat-assistant">🤖 <b>Assistant:</b> {msg["content"]}</div>',
                unsafe_allow_html=True,
            )
            break

    st.success("Database updated successfully.")
    if st.button("➕ Make another modification", type="primary"):
        ws.approval_status = None
        _save(ws)
        st.rerun()


def _render_error_phase(ws: Workspace) -> None:
    error = ws.feature2_data.get("error", "Unknown error")
    st.error(f"❌ Pipeline error: {error}")
    if st.button("🔄 Try again with a new request"):
        ws.approval_status = None
        ws.feature2_data   = {**ws.feature2_data, "error": ""}
        _save(ws)
        st.rerun()


# Export / PDF report


def _render_export_section(ws: Workspace) -> None:
    """PDF report + DB download — shown when there are modifications."""
    if not ws.modification_history:
        return

    st.markdown("---")
    st.markdown("### 📤 Export")
    st.markdown('<div class="export-card">', unsafe_allow_html=True)

    col_pdf, col_db = st.columns(2)

    with col_pdf:
        if st.button("📄 Generate PDF Report", use_container_width=True, key="f2_gen_pdf"):
            with st.spinner("Generating PDF report…"):
                try:
                    from Features.Feature2_modify_db.utils.pdf_report import generate_pdf_report
                    pdf_bytes = generate_pdf_report(
                        modification_history=ws.modification_history,
                        db_schema=ws.schema_ddl or "",
                        db_name=ws.db_blob_name or "database.db",
                    )
                    st.session_state["f2_pdf_bytes"] = pdf_bytes
                    st.success("PDF ready — click below to download.")
                except Exception as exc:
                    st.error(f"PDF generation failed: {exc}")
                    logger.exception("PDF report generation failed")

        pdf_bytes = st.session_state.get("f2_pdf_bytes")
        if pdf_bytes:
            st.download_button(
                "⬇️ Download PDF Report",
                data=pdf_bytes,
                file_name="db_modification_report.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="f2_download_pdf",
            )

    with col_db:
        # Download DB from local path if available, else from blob
        db_path = ws.db_local_path
        import os
        if db_path and os.path.exists(db_path):
            try:
                with open(db_path, "rb") as f:
                    st.download_button(
                        "⬇️ Download Database (.db)",
                        data=f.read(),
                        file_name=os.path.basename(db_path),
                        mime="application/x-sqlite3",
                        use_container_width=True,
                        key="f2_download_db_local",
                    )
            except Exception:
                pass
        elif ws.db_blob_url:
            st.markdown(
                f'<a href="{ws.db_blob_url}" target="_blank" style="color:#4fc3f7">⬇️ Download from Cloud</a>',
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)


# Import tab for uploading CSV/XLSX files into existing tables

def _render_import_tab(ws: Workspace) -> None:
    from Features.Feature2_modify_db.utils.file_import import (
        parse_upload_file, validate_import, build_insert_statements,
    )
    from Features.Feature2_modify_db.utils.db_utils import (
        get_table_row_counts, execute_sql_statements, extract_schema,
    )
    from Features.Feature2_modify_db.utils.memory import create_modification_record

    st.markdown("### 📂 Import CSV or XLSX into a Table")
    st.caption("Upload a file, choose a target table, validate, then insert.")

    st.markdown("#### Step 1 — Upload your file")
    uploaded = st.file_uploader(
        "Choose a CSV or XLSX file",
        type=["csv", "xlsx", "xls"],
        key="f2_import_uploader",
    )

    if not uploaded:
        st.markdown("""
        <div style="background:#f8f9fa;border:2px dashed #dee2e6;border-radius:10px;
                    padding:2rem;text-align:center;color:#6c757d;">
          <div style="font-size:2.5rem">📄</div>
          <div style="font-size:1rem;font-weight:600">Upload a CSV or XLSX file above</div>
          <div style="font-size:.85rem;margin-top:.5rem">
            The system validates it against the selected table schema before inserting.
          </div>
        </div>
        """, unsafe_allow_html=True)
        return

    if uploaded.name != st.session_state.get("f2_import_file_name"):
        _clear_import_state()
        with st.spinner("Parsing file…"):
            df, parse_error = parse_upload_file(uploaded)
        if parse_error:
            st.error(f"❌ {parse_error}")
            return
        st.session_state["f2_import_df"]        = df
        st.session_state["f2_import_file_name"] = uploaded.name

    df = st.session_state["f2_import_df"]

    with st.expander(
        f"👀 Preview — {uploaded.name} ({len(df):,} rows × {len(df.columns)} cols)",
        expanded=True,
    ):
        st.dataframe(df.head(10), use_container_width=True)
        if len(df) > 10:
            st.caption(f"Showing first 10 of {len(df):,} rows.")

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
    st.markdown("#### Step 2 — Configure import")

    try:
        available_tables = list(_cached_get_table_row_counts(ws.db_blob_name).keys())
    except Exception as e:
        st.error(f"Could not fetch table list: {e}")
        return

    if not available_tables:
        st.warning("⚠️ No tables in database yet. Create one first via the Chat tab.")
        return

    col_cfg1, col_cfg2 = st.columns([2, 1])
    with col_cfg1:
        prev_table     = st.session_state.get("f2_import_table", available_tables[0])
        selected_table = st.selectbox(
            "Target table",
            options=available_tables,
            index=available_tables.index(prev_table) if prev_table in available_tables else 0,
        )
    with col_cfg2:
        on_conflict = st.selectbox(
            "On duplicate key",
            options=["ABORT", "IGNORE", "REPLACE"],
            index=["ABORT", "IGNORE", "REPLACE"].index(
                st.session_state.get("f2_import_on_conflict", "ABORT")
            ),
        )

    st.session_state["f2_import_on_conflict"] = on_conflict

    if selected_table != st.session_state.get("f2_import_table"):
        st.session_state["f2_import_validation"] = None
        st.session_state["f2_import_ready"]      = False
        st.session_state["f2_import_table"]      = selected_table

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
    st.markdown("#### Step 3 — Validate")

    if st.button("🔍 Run Validation", type="primary", key="f2_run_validation"):
        with st.spinner("Running all validation checks…"):
            report = validate_import(df, ws.db_blob_name, selected_table)
        st.session_state["f2_import_validation"] = report.to_dict()
        st.session_state["f2_import_ready"]      = not report.has_errors
        st.session_state["f2_import_table"]      = selected_table

    val_report = st.session_state.get("f2_import_validation")
    if val_report is None:
        return

    errors   = val_report.get("errors", [])
    warnings = val_report.get("warnings", [])

    if errors:
        st.markdown(
            f'<div style="background:#f8d7da;border-left:5px solid #dc3545;'
            f'padding:.8rem 1.1rem;border-radius:8px;color:#721c24;margin:.5rem 0;">'
            f'<b>❌ {len(errors)} error(s)</b> must be fixed before import can proceed.</div>',
            unsafe_allow_html=True,
        )
    elif warnings:
        st.markdown(
            f'<div style="background:#fff3cd;border-left:5px solid #ffc107;'
            f'padding:.8rem 1.1rem;border-radius:8px;color:#6d5100;margin:.5rem 0;">'
            f'<b>⚠️ Passed with {len(warnings)} warning(s).</b> Review before importing.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#d4edda;border-left:5px solid #28a745;'
            'padding:.8rem 1.1rem;border-radius:8px;color:#155724;margin:.5rem 0;">'
            '<b>✅ All checks passed. Ready to import.</b></div>',
            unsafe_allow_html=True,
        )

    if errors or warnings:
        with st.expander(f"📋 Details ({len(errors)} error(s), {len(warnings)} warning(s))", expanded=True):
            for e in errors:
                st.markdown(f'<div class="val-error">🚫 {e}</div>', unsafe_allow_html=True)
            for w in warnings:
                st.markdown(f'<div class="val-warning">⚠️ {w}</div>', unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
    st.markdown("#### Step 4 — Confirm & Import")

    if not st.session_state.get("f2_import_ready"):
        st.info("Fix the errors above, correct your file, and re-upload to try again.")
        return

    st.markdown(
        f'<div class="plan-box"><b>📋 Import Summary</b><br><br>'
        f'• <b>File:</b> {uploaded.name}<br>'
        f'• <b>Target table:</b> <code>{selected_table}</code><br>'
        f'• <b>Rows to insert:</b> {len(df):,}<br>'
        f'• <b>On conflict:</b> <code>{on_conflict}</code>'
        f'{"  ⚠️ " + str(len(warnings)) + " warning(s)" if warnings else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    confirm_col, _ = st.columns([1, 2])
    with confirm_col:
        if st.button(f"⬆️ Confirm & Import {len(df):,} rows", type="primary", use_container_width=True):
            with st.spinner("Building INSERT statements…"):
                try:
                    sqls = build_insert_statements(
                        df, selected_table, ws.db_blob_name, on_conflict=on_conflict,
                    )
                except Exception as e:
                    st.error(f"Failed to build INSERT statements: {e}")
                    return

            with st.spinner(f"⚡ Inserting {len(sqls):,} rows into '{selected_table}'…"):
                success, message, backup_blob = execute_sql_statements(ws.db_blob_name, sqls)

            if not success:
                st.error(f"❌ Import failed: {message}")
                if backup_blob:
                    st.caption(f"Backup: {backup_blob}")
                return

            ws.schema_ddl = _cached_extract_schema_f2(ws.db_blob_name)
            record = create_modification_record(
                user_request=f"File import: '{uploaded.name}' → table '{selected_table}'",
                sql_statements=[
                    f"-- {len(sqls)} INSERT statements (file import)",
                    sqls[0] if sqls else "",
                    f"-- … {len(sqls) - 1} more rows" if len(sqls) > 1 else "",
                ],
                description=(
                    f"Imported {len(sqls):,} rows from '{uploaded.name}' "
                    f"into table '{selected_table}' (conflict: {on_conflict})."
                ),
            )
            ws.modification_history = list(ws.modification_history) + [record]
            ws.state = WorkspaceState.MODIFIED
            _save(ws)
            _clear_import_state()
            st.success(f"✅ Imported {len(sqls):,} rows into `{selected_table}`!")
            st.rerun()


# ERD tab — with blob fallback


def _render_erd_tab(ws: Workspace) -> None:
    from Features.Feature2_modify_db.utils.erd_data     import extract_erd_data
    from Features.Feature2_modify_db.utils.erd_renderer import render_erd_html

    st.markdown("### 📊 Entity–Relationship Diagram")
    st.caption("Drag tables to rearrange · Scroll to zoom · Updates after every modification.")

    _, erd_col_right = st.columns([3, 1])
    with erd_col_right:
        erd_height = st.slider("Height (px)", 400, 1200, 650, 50, key="f2_erd_height")

    # Guard — need a blob name to extract ERD
    if not ws.db_blob_name:
        st.info("No database blob registered yet. Complete a modification first.")
        return

    with st.spinner("Loading schema for diagram…"):
        try:
            erd_data = extract_erd_data(ws.db_blob_name)

        except FileNotFoundError:
          
            st.warning(
                f"⚠️ Database blob `{ws.db_blob_name}` is not yet in cloud storage.\n\n"
                "This usually means the database was created in this session but hasn't "
                "been synced to Azure Blob Storage yet. "
                "Try applying a chat modification — the executor will upload the DB automatically."
            )

            # Attempt local fallback using db_local_path
            import os
            if ws.db_local_path and os.path.exists(ws.db_local_path):
                st.info("Attempting local fallback for ERD preview…")
                try:
                    from Features.Feature2_modify_db.utils.blob_storage import upload_db
                    with open(ws.db_local_path, "rb") as f:
                        upload_db(f.read(), ws.db_blob_name)
                    erd_data = extract_erd_data(ws.db_blob_name)
                    st.success("✅ Database synced to cloud storage. ERD loaded.")
                except Exception as sync_exc:
                    st.error(f"Could not sync local DB to blob: {sync_exc}")
                    return
            else:
                return

        except Exception as e:
            st.error(f"❌ Could not extract ERD data: {e}")
            logger.exception("ERD extraction failed for blob: %s", ws.db_blob_name)
            return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tables",        len(erd_data["tables"]))
    c2.metric("Views",         len(erd_data["views"]))
    c3.metric("Relationships", len(erd_data["relationships"]))
    c4.metric("Total Columns", sum(len(t["columns"]) for t in erd_data["tables"]))

    st.divider()
    components.html(render_erd_html(erd_data, height=erd_height), height=erd_height + 10, scrolling=False)

    if erd_data["relationships"]:
        with st.expander("🔗 Relationship Details", expanded=False):
            for rel in erd_data["relationships"]:
                card_label = (
                    f'**{rel["from_card"]}:{rel["to_card"]}** '
                    f'({"optional" if rel["optional"] else "mandatory"})'
                )
                on_del = rel["on_delete"] if rel["on_delete"] != "NO ACTION" else "—"
                on_upd = rel["on_update"] if rel["on_update"] != "NO ACTION" else "—"
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;padding:5px 8px;'
                    f'border-bottom:1px solid #EEF0F3;font-size:12px;">'
                    f'<code style="background:#E8F4F8;padding:2px 6px;border-radius:4px;">'
                    f'{rel["from_table"]}.{rel["from_col"]}</code>'
                    f'<span style="color:#2E86AB;font-weight:700;">──{card_label}──▶</span>'
                    f'<code style="background:#F0F7E6;padding:2px 6px;border-radius:4px;">'
                    f'{rel["to_table"]}.{rel["to_col"]}</code>'
                    f'<span style="color:#888;font-size:10px;margin-left:auto;">'
                    f'ON DELETE {on_del} | ON UPDATE {on_upd}</span></div>',
                    unsafe_allow_html=True,
                )

    if erd_data["views"]:
        with st.expander(f"👁 View Definitions ({len(erd_data['views'])})", expanded=False):
            for vw in erd_data["views"]:
                st.markdown(f"**{vw['name']}**")
                st.code(vw["sql"], language="sql")


# Upload existing DB — called by app.py

def render_upload_phase(ws: Workspace) -> None:
    from Features.Feature2_modify_db.utils.db_utils import ingest_uploaded_db, extract_schema
    from shared.blob_storage import get_blob_url

    st.markdown("## 📤 Upload Existing Database")
    st.caption("Upload a SQLite file or connect via URL to start modifying or chatting.")

    tab_file, tab_url = st.tabs(["📂 Upload File", "🔗 Connection URL"])

    with tab_file:
        uploaded = st.file_uploader(
            "Upload your SQLite database",
            type=["db", "sqlite", "sqlite3"],
            key="f2_router_upload_db",
        )
        if not uploaded:
            st.markdown(
                '<div style="border:2px dashed rgba(79,195,247,0.25);border-radius:12px;'
                'padding:2rem;text-align:center;color:#4a6a8a;">'
                '<div style="font-size:2.5rem;margin-bottom:.5rem">📂</div>'
                '<div style="font-weight:600;color:#e0f4ff">Drop your SQLite file here</div>'
                '<div style="font-size:.85rem;margin-top:.4rem">Supports .db · .sqlite · .sqlite3</div>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("DB Studio will register the database, extract its schema, and let you choose what to do next.")
            if st.button("Upload & Register Database", type="primary", use_container_width=True, key="f2_upload_btn"):
                with st.spinner("Uploading and preparing database…"):
                    blob_name  = ingest_uploaded_db(uploaded)
                    schema_ddl = _cached_extract_schema_f2(blob_name)

                    ws.db_blob_name    = blob_name
                    ws.db_blob_url     = get_blob_url(blob_name)
                    ws.schema_ddl      = schema_ddl
                    ws.approval_status = None

                st.markdown(
                    '<span style="color:#4ade80;font-weight:600">✓ Database registered successfully</span>',
                    unsafe_allow_html=True,
                )
                ws.state = WorkspaceState.DB_READY
                ws.entry_mode = "upload"
                st.session_state["workspace"] = ws
                save_workspace(ws)
                st.rerun()

    with tab_url:
        st.markdown("#### SQLite Connection URL")
        st.caption("Format: `sqlite:///absolute/path/to/database.db`")

        conn_url = st.text_input(
            "Connection URL",
            placeholder="sqlite:///path/to/your/database.db",
            key="f2_upload_url_input",
            label_visibility="collapsed",
        )

        col_test, col_connect = st.columns([1, 2])

        with col_test:
            if st.button("🔍 Test", use_container_width=True, key="f2_url_test_btn"):
                if not conn_url.strip():
                    st.warning("Enter a URL first.")
                else:
                    with st.spinner("Testing connection…"):
                        try:
                            import sqlite3
                            path = conn_url.strip().replace("sqlite:///", "")
                            conn = sqlite3.connect(path)
                            cursor = conn.cursor()
                            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                            tables = [r[0] for r in cursor.fetchall()]
                            conn.close()
                            st.session_state["f2_url_conn_ok"]     = True
                            st.session_state["f2_url_conn_tables"] = tables
                            st.session_state["f2_url_conn_url"]    = conn_url.strip()
                        except Exception as e:
                            st.session_state["f2_url_conn_ok"]  = False
                            st.session_state["f2_url_conn_err"] = str(e)

        if st.session_state.get("f2_url_conn_ok") is True:
            tables = st.session_state.get("f2_url_conn_tables", [])
            st.markdown(
                f'<span style="color:#4ade80;font-weight:600">✓ Connected — {len(tables)} table(s)</span>',
                unsafe_allow_html=True,
            )
            if tables:
                st.caption("Tables: " + ", ".join(f"`{t}`" for t in tables[:8]))
        elif st.session_state.get("f2_url_conn_ok") is False:
            err = st.session_state.get("f2_url_conn_err", "Unknown error")
            st.markdown(
                f'<span style="color:#f87171;font-weight:600">✗ {err}</span>',
                unsafe_allow_html=True,
            )

        with col_connect:
            conn_ready = st.session_state.get("f2_url_conn_ok", False)
            if st.button(
                "Connect & Continue",
                type="primary",
                use_container_width=True,
                disabled=not conn_ready,
                key="f2_url_connect_btn",
            ):
                url  = st.session_state.get("f2_url_conn_url", conn_url.strip())
                path = url.replace("sqlite:///", "")
                try:
                    import sqlite3
                    conn   = sqlite3.connect(path)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL;"
                    )
                    ddl_parts = [r[0] for r in cursor.fetchall()]
                    conn.close()
                    schema_ddl = "\n\n".join(ddl_parts)
                except Exception:
                    schema_ddl = ""

                ws.db_conn_url     = url
                ws.schema_ddl      = schema_ddl
                ws.approval_status = None
                ws.state           = WorkspaceState.DB_READY
                ws.entry_mode      = "upload"
                st.session_state["workspace"] = ws
                save_workspace(ws)
                st.rerun()


# Main export ← called by app.py

def render_modify_phase(ws: Workspace) -> None:
    _inject_css()

    tab_chat, tab_import, tab_erd = st.tabs([
        "💬 Chat Modifications",
        "📂 Import CSV / XLSX",
        "📊 ER Diagram",
    ])

    with tab_chat:
        with st.expander("🔍 Current Schema", expanded=False):
            st.code(ws.schema_ddl or "Schema not available.", language="sql")
        st.divider()
        _render_messages(ws)
        st.divider()

        status = ws.approval_status

        if status in (None, "done"):
            if status == "done":
                _render_done_phase(ws)
                st.divider()
            _render_chat_input(ws)

        elif status == "clarifying":
            _render_clarify_phase(ws)

        elif status == "human_review":
            _render_human_review_phase(ws)

        elif status == "error":
            _render_error_phase(ws)

        # PDF + download export — shown after at least one modification
        _render_export_section(ws)

    with tab_import:
        _render_import_tab(ws)

    with tab_erd:
        _render_erd_tab(ws)