"""
feature1_app.py — Feature 1: Create DB (UI layer)

"""
from __future__ import annotations

import logging
import os
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from shared.workspace import Workspace, WorkspaceState
from shared.blob_storage import save_workspace, load_workspace, upload_to_blob

from shared.erd.data import extract_erd_data
from shared.erd.renderer import render_erd_html

from shared.pdf_report import generate_validation_pdf

from Features.Feature1_create_db import run_feature_1

from Features.Feature1_create_db.models import (
    SuggestionPlan,
    DatabaseSchema,
    ValidationResult,
    QuerySet,
)
from Features.Feature1_create_db.utils import generate_sqlite_ddl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# CSS 

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@300;400;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
.stApp { background: linear-gradient(135deg, #0a0f1e 0%, #0d1b2a 50%, #0a1628 100%); }
section[data-testid="stSidebar"] { background: #070d1a !important; border-right: 1px solid #1e3a5f; }

.agent-card {
    background: rgba(30,58,95,0.25);
    border: 1px solid rgba(79,195,247,0.2);
    border-radius: 12px; padding: 1.2rem 1.5rem;
    margin-bottom: 1rem; backdrop-filter: blur(10px);
}
.agent-card h3 { color: #4fc3f7; margin: 0 0 0.5rem; font-size: 1rem; }
.agent-card p  { color: #a0c4e8; margin: 0; font-size: 0.88rem; line-height: 1.5; }

.status-pill {
    display: inline-block; padding: 3px 14px; border-radius: 20px;
    font-size: 0.78rem; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase;
}
.status-init       { background:#1e3a5f; color:#4fc3f7; }
.status-analyzing  { background:#2d3a1e; color:#a3e635; }
.status-suggesting { background:#3a2d1e; color:#f0a500; }
.status-awaiting   { background:#3a1e2d; color:#f06292; }
.status-approved   { background:#1e3a26; color:#4ade80; }
.status-rejected   { background:#3a1e1e; color:#f87171; }
.status-complete   { background:#1e2d3a; color:#60a5fa; }

.approval-banner {
    background: linear-gradient(90deg,rgba(240,165,0,.15),rgba(240,165,0,.05));
    border: 1px solid rgba(240,165,0,.4); border-left: 4px solid #f0a500;
    border-radius: 8px; padding: 1.2rem 1.5rem; margin: 1rem 0;
    color: #f0a500; font-weight: 600; font-size: 1rem;
}

.stButton > button {
    border-radius: 8px !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important; letter-spacing: 0.03em !important;
    transition: all 0.2s ease !important;
}

pre, code { font-family: 'JetBrains Mono', monospace !important; font-size: 0.82rem !important; }
h1 { color: #e0f4ff !important; font-weight: 700 !important; }
h2 { color: #b3d9f5 !important; font-weight: 600 !important; }
h3 { color: #7ec8e3 !important; }

[data-testid="metric-container"] {
    background: rgba(30,58,95,0.3) !important;
    border: 1px solid rgba(79,195,247,0.2) !important;
    border-radius: 10px !important; padding: 0.8rem !important;
}

hr { border-color: rgba(79,195,247,0.15) !important; margin: 1.5rem 0 !important; }

.stTabs [data-baseweb="tab-list"] { gap: 4px; background: transparent; }
.stTabs [data-baseweb="tab"] {
    background: rgba(30,58,95,0.3); border-radius: 8px 8px 0 0;
    color: #a0c4e8; font-weight: 600;
}
.stTabs [aria-selected="true"] { background: rgba(79,195,247,0.2); color: #4fc3f7 !important; }
</style>
""", unsafe_allow_html=True)


# Workspace helpers 

def get_or_create_workspace() -> Workspace:
    if "workspace_id" not in st.session_state:
        st.session_state["workspace_id"] = str(uuid.uuid4())

    wid = st.session_state["workspace_id"]

    if "workspace" not in st.session_state:
        st.session_state["workspace"] = load_workspace(wid)

    return st.session_state["workspace"]


def save(ws: Workspace) -> None:
    st.session_state["workspace"] = ws
    save_workspace(ws)


def clear_workspace() -> None:
    new_id = str(uuid.uuid4())
    st.session_state.clear()
    st.session_state["workspace_id"] = new_id
    st.session_state["workspace"]    = Workspace(workspace_id=new_id)



# Blob upload with local fallback and PDF report generation


def _upload_feature_1_artifacts(ws: Workspace) -> Workspace:
    """
    Upload the generated .db file, ERD HTML, and PDF report to blob storage.
    Clears the local temp fields afterwards.
    Called immediately after run_feature_1 returns with approval_status == "done".
    """
    if ws.db_local_path and os.path.exists(ws.db_local_path):
        try:
            with open(ws.db_local_path, "rb") as f:
                db_bytes = f.read()
            blob_path        = f"{ws.workspace_id}/database.db"
            ws.db_blob_url   = upload_to_blob(db_bytes, blob_path)
            ws.db_blob_name  = blob_path
            ws.db_local_path = None
        except Exception as e:
            # Blob upload failed — keep local path as fallback so Feature 2 works
            ws.db_blob_name = ws.db_local_path
            logger.warning("DB blob upload failed, using local path: %s", e)

    if ws.erd_html:
        try:
            blob_path       = f"{ws.workspace_id}/erd.html"
            ws.erd_blob_url = upload_to_blob(ws.erd_html.encode(), blob_path)
            ws.erd_html     = None          # clear temp field after upload
        except Exception as e:
            logger.warning("ERD blob upload failed: %s", e)

    #  NEW: Generate and upload PDF validation report 
    if ws.validation_result and ws.schema_json:
        try:
            validation = _validation_from_workspace(ws)
            schema = _schema_from_workspace(ws)
            
            pdf_path = f"output/{ws.workspace_id}_validation_report.pdf"
            generate_validation_pdf(
                validation_result=validation,
                schema=schema,
                output_path=pdf_path,
                workspace_id=ws.workspace_id,
            )
            
            # Upload PDF to blob storage
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as fh:
                    blob_path = f"{ws.workspace_id}/validation_report.pdf"
                    ws.validation_pdf_url = upload_to_blob(fh.read(), blob_path)
                logger.info(f"Validation PDF generated and uploaded: {ws.validation_pdf_url}")
        except Exception as e:
            logger.warning("Failed to generate/upload validation PDF: %s", e)

    return ws


# Rehydration helpers  


def _plan_from_workspace(ws: Workspace) -> SuggestionPlan | None:
    if not ws.suggestion_plan:
        return None
    try:
        return SuggestionPlan.model_validate(ws.suggestion_plan)
    except Exception:
        logger.exception("Failed to rehydrate SuggestionPlan")
        return None


def _schema_from_workspace(ws: Workspace) -> DatabaseSchema | None:
    if not ws.schema_json:
        return None
    try:
        return DatabaseSchema.model_validate(ws.schema_json)
    except Exception:
        logger.exception("Failed to rehydrate DatabaseSchema")
        return None


def _validation_from_workspace(ws: Workspace) -> ValidationResult | None:
    if not ws.validation_result:
        return None
    try:
        return ValidationResult.model_validate(ws.validation_result)
    except Exception:
        logger.exception("Failed to rehydrate ValidationResult")
        return None


def _queries_from_workspace(ws: Workspace) -> QuerySet | None:
    if not ws.query_set:
        return None
    try:
        return QuerySet.model_validate(ws.query_set)
    except Exception:
        logger.exception("Failed to rehydrate QuerySet")
        return None


# ERD helpers  

def _render_erd(source: SuggestionPlan | DatabaseSchema, height: int = 600) -> None:
    """
    Render an interactive ERD for either a SuggestionPlan (pre-approval)
    or a DatabaseSchema (post-approval) using the shared renderer.
    extract_erd_data() handles both types transparently (see shared/erd/data.py).
    """
    try:
        erd_data = extract_erd_data(source)
        erd_html = render_erd_html(erd_data, height=height)
        components.html(erd_html, height=height + 10, scrolling=False)
    except Exception as exc:
        logger.exception("ERD render failed")
        st.warning(f"ERD could not be rendered: {exc}")


# Sidebar  

def render_sidebar(ws: Workspace) -> None:
    with st.sidebar:
        st.markdown("## 🗄️ DB Studio")
        st.markdown("---")

        st.markdown("### Workspace")
        st.caption(f"`{ws.workspace_id[:12]}…`")

        if st.button("🔄 New workspace", use_container_width=True):
            clear_workspace()
            st.rerun()


# Phase 1 — Input

def render_input_phase(ws: Workspace) -> None:
    st.markdown("## 🧠 Describe Your Database Requirements")
    st.caption("Tell us what you need — your domain, entities, and key relationships.")

    col_main, col_side = st.columns([2.15, 1], gap="large")

    with col_main:
        examples = {
            "School Management": "I need a school management system with students, teachers, courses, classes, enrollments, grades, and attendance tracking.",
            "E-Commerce":        "Build a complete e-commerce platform with products, categories, customers, orders, payments, reviews, and inventory management.",
            "Hospital":          "Design a hospital system with patients, doctors, departments, appointments, medical records, prescriptions, and billing.",
            "HR & Payroll":      "Create an HR system with employees, departments, positions, salaries, leave management, performance reviews, and payroll.",
            "Custom Input":      "",
        }
        selected      = st.selectbox("Quick start", list(examples.keys()), key="f1_example")
        default_text  = st.session_state.get("f1_requirement_text", examples[selected])
        user_input    = st.text_area(
            "Describe your system",
            value=default_text,
            height=200,
            placeholder="e.g. I need an e-commerce platform with products, customers, orders, payments, and inventory rules...",
            key="f1_requirement_text",
        )

    with col_side:
        st.markdown(
            """
<div class="agent-card">
    <h3>What to include</h3>
    <p>List the main entities, how they relate, who uses the system, and any
    audit, reporting, or approval requirements.</p>
</div>
<div class="agent-card">
    <h3>What happens next</h3>
    <p>DB Studio analyses your input, proposes a schema design, and waits for
    your approval before generating the database.</p>
</div>
""",
            unsafe_allow_html=True,
        )

    if st.button("🚀 Analyse & Generate Plan", type="primary", use_container_width=True):
        if not user_input.strip():
            st.warning("Please describe your system first.")
            return

        ws.user_input    = user_input.strip()
        ws.history       = [{"role": "user", "content": user_input.strip()}]
        ws.approval_status = None

        with st.spinner("Analysing requirements and generating a design suggestion…"):
            try:
                ws = run_feature_1(ws)
                save(ws)
                st.rerun()
            except Exception as exc:
                st.error(f"Pipeline error: {exc}")
                logger.exception("Feature 1 pre-approval pipeline failed")


# Phase 2 — Suggestion review

def render_suggestion_phase(ws: Workspace) -> None:
    plan = _plan_from_workspace(ws)
    if plan is None:
        st.error("No suggestion plan found. Please start over.")
        return

    st.markdown(
        '<div class="approval-banner">'
        "⏸ <strong>HUMAN APPROVAL REQUIRED</strong> — Review the proposed schema design "
        "before DB Studio generates the database."
        "</div>",
        unsafe_allow_html=True,
    )

    col_plan, col_erd = st.columns([1.1, 0.9], gap="large")

    #  Plan detail 
    with col_plan:
        st.markdown("### Suggested Design")

        st.markdown("#### Entities")
        for entity in plan.suggested_entities:
            with st.expander(
                f"{entity.name} ({len(entity.attributes)} attributes)", expanded=False
            ):
                if entity.description:
                    st.caption(entity.description)
                for attr in entity.attributes:
                    badges = []
                    if attr.is_primary_key:  badges.append("PK")
                    if attr.is_foreign_key:  badges.append("FK")
                    if not attr.is_nullable: badges.append("NOT NULL")
                    badge_html = "".join(
                        f'<span style="background:rgba(79,195,247,0.12);'
                        f'border:1px solid rgba(79,195,247,0.22);'
                        f'padding:1px 7px;border-radius:8px;font-size:0.72rem;'
                        f'margin-left:4px;color:#d7ecff;">{b}</span>'
                        for b in badges
                    )
                    st.markdown(
                        f"**{attr.name}** &nbsp; `{attr.data_type}` {badge_html}",
                        unsafe_allow_html=True,
                    )
                    if attr.description:
                        st.caption(attr.description)

        st.markdown("#### Relationships")
        for rel in plan.suggested_relationships:
            arrow = {
                "one-to-one":   "1──1",
                "one-to-many":  "1──N",
                "many-to-many": "N──M",
                "many-to-one":  "N──1",
            }.get(rel.relationship_type, "──")
            label = f" _{rel.label}_" if rel.label else ""
            st.markdown(f"- **{rel.from_entity}** `{arrow}` **{rel.to_entity}**{label}")

        if plan.optional_features:
            st.markdown("#### Optional Features")
            for feature in plan.optional_features:
                with st.expander(feature.name, expanded=False):
                    st.write(feature.description)
                    if feature.entities_involved:
                        st.caption(f"Involves: {', '.join(feature.entities_involved)}")

        if plan.rationale:
            with st.expander("Design Rationale", expanded=False):
                st.write(plan.rationale)

    #  ERD preview — renderer 
    with col_erd:
        st.markdown("### Live ERD Preview")
        st.caption("Drag nodes to rearrange · scroll to zoom.")
        _render_erd(plan, height=560)

    #  Metrics row 
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entities",          len(plan.suggested_entities))
    m2.metric("Relationships",     len(plan.suggested_relationships))
    m3.metric("Attributes",        sum(len(e.attributes) for e in plan.suggested_entities))
    m4.metric("Optional Features", len(plan.optional_features))

    #  Modify plan 
    st.markdown("### Modify Plan")
    modify_instruction = st.text_input(
        "Tell DB Studio what to change",
        placeholder="e.g. add an Inventory table with product_id, stock_count, and last_updated",
        key=f"f1_modify_{ws.workspace_id}",
    )

    if st.button("Apply Modification", use_container_width=True):
        if not modify_instruction.strip():
            st.warning("Enter a modification request first.")
        else:
            ws.history.append({"role": "user", "content": modify_instruction.strip()})
            ws.approval_status = "modifying"
            with st.spinner("Updating the suggestion plan…"):
                try:
                    ws = run_feature_1(ws)
                    save(ws)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Modification failed: {exc}")
                    logger.exception("Feature 1 plan modification failed")

    #  Approval 
    st.markdown("### Your Decision")
    st.warning("Approve when the entities, relationships, and optional features look right.")

    col_approve, col_restart = st.columns(2)

    with col_approve:
        if st.button("✅ Approve & Generate Schema", type="primary", use_container_width=True):
            ws.approval_status = "approved"
            with st.spinner("Designing schema, validating it, and building the database…"):
                try:
                    ws = run_feature_1(ws)
                    # ── Upload to blob immediately after DB is ready ──────────
                    if ws.approval_status == "done":
                        ws = _upload_feature_1_artifacts(ws)
                    save(ws)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Post-approval error: {exc}")
                    logger.exception("Feature 1 post-approval pipeline failed")

    with col_restart:
        if st.button("Start Over", use_container_width=True):
            clear_workspace()
            st.rerun()


# Phase 3 — Results

def render_results_phase(ws: Workspace) -> None:
    schema     = _schema_from_workspace(ws)
    validation = _validation_from_workspace(ws)
    queries    = _queries_from_workspace(ws)

    st.markdown("## ✅ Database Ready")
    st.success(
        "The schema has been generated, validated, and uploaded. "
        "You can now modify it or chat with it."
    )

    #  Metrics 
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tables",           len(schema.tables) if schema else 0)
    c2.metric("Columns",          sum(len(t.columns) for t in schema.tables) if schema else 0)
    c3.metric("Normalization",    schema.normalization_level if schema else "—")
    c4.metric("Validation Issues",len(validation.issues) if validation else 0)
    c5.metric(
        "Queries Generated",
        (
            sum(len(v) for v in queries.crud_queries.values()) +
            len(queries.analytical_queries)
        ) if queries else 0,
    )

    #  Downloads ( with PDF report) 
    dl_db, dl_sql, dl_pdf = st.columns(3)

    with dl_db:

        local = ws.db_local_path
        if local and os.path.exists(local):
            with open(local, "rb") as fh:
                st.download_button(
                    "📥 Download SQLite Database",
                    data=fh.read(),
                    file_name=os.path.basename(local),
                    mime="application/x-sqlite3",
                    use_container_width=True,
                )
        elif ws.db_blob_url:
            st.link_button("☁ Open Blob Download Link", ws.db_blob_url, use_container_width=True)

    with dl_sql:
        ddl = ws.schema_ddl or (generate_sqlite_ddl(schema) if schema else "")
        if ddl:
            st.download_button(
                "📄 Download SQL Schema",
                data=ddl,
                file_name="schema.sql",
                mime="text/plain",
                use_container_width=True,
            )

    #  PDF Report Download 
    with dl_pdf:
        if validation and schema:
            # Check if PDF already exists in blob storage
            if hasattr(ws, 'validation_pdf_url') and ws.validation_pdf_url:
                st.link_button(
                    "📑 Download PDF Report",
                    ws.validation_pdf_url,
                    use_container_width=True,
                )
            else:
                # Generate on demand
                if st.button("📑 Generate PDF Report", use_container_width=True):
                    try:
                        pdf_path = f"output/{ws.workspace_id}_validation_report.pdf"
                        generate_validation_pdf(
                            validation_result=validation,
                            schema=schema,
                            output_path=pdf_path,
                            workspace_id=ws.workspace_id,
                        )
                        
                        if os.path.exists(pdf_path):
                            with open(pdf_path, "rb") as fh:
                                st.download_button(
                                    "📥 Download PDF",
                                    data=fh.read(),
                                    file_name=f"validation_report_{ws.workspace_id[:8]}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True,
                                )
                            st.success("PDF report generated!")
                    except Exception as e:
                        st.error(f"Failed to generate PDF: {e}")
                        logger.exception("PDF generation failed")

    #  Tabs 
    tab_erd, tab_schema, tab_sql, tab_validation, tab_queries = st.tabs(
        ["📊 ERD", "🗄 Schema", "📝 SQL DDL", "🔬 Validation", "🔍 Queries"]
    )

    # ERD tab — NEW renderer, always derives from schema object
    with tab_erd:
        if schema:
            st.caption("Drag tables to rearrange · scroll to zoom · pan with middle-mouse.")
            _render_erd(schema, height=650)
        else:
            st.info("No schema available.")

    with tab_schema:
        if not schema:
            st.info("No schema available.")
        else:
            for table in schema.tables:
                with st.expander(
                    f"{table.name} ({len(table.columns)} columns)", expanded=False
                ):
                    if table.description:
                        st.caption(table.description)
                    df = pd.DataFrame([
                        {
                            "Column":      col.name,
                            "Type":        col.data_type,
                            "Constraints": ", ".join(col.constraints) if col.constraints else "—",
                            "References":  col.references or "—",
                            "Description": col.description or "",
                        }
                        for col in table.columns
                    ])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    if table.indexes:
                        st.markdown("**Indexes**")
                        for idx_sql in table.indexes:
                            st.code(idx_sql, language="sql")

    with tab_sql:
        ddl = ws.schema_ddl or (generate_sqlite_ddl(schema) if schema else "")
        if ddl:
            st.code(ddl, language="sql")
        else:
            st.info("No SQL DDL available.")

    with tab_validation:
        if not validation:
            st.info("No validation results available.")
        else:
            if validation.is_valid:
                st.success("✅ Schema Validation Passed")
            else:
                st.warning("⚠️ Schema validation found issues.")

            errors   = [i for i in validation.issues if i.severity == "error"]
            warnings = [i for i in validation.issues if i.severity == "warning"]
            suggestions = list(getattr(validation, "llm_suggestions", []) or [])

            if suggestions:
                st.markdown("#### 💡 Suggestions")
                for s in suggestions:
                    loc = f"[{getattr(s,'table','')}]" if getattr(s,"table",None) else "Schema"
                    st.info(f"{loc} — {getattr(s,'message',str(s))}")
            if warnings:
                st.markdown("#### 🟡 Warnings")
                for i in warnings:
                    loc = f"[{i.table}]" if i.table else "Schema"
                    if i.column: loc += f".{i.column}"
                    st.warning(f"{loc} — {i.message}")
            if errors:
                st.markdown("#### 🔴 Errors")
                for i in errors:
                    loc = f"[{i.table}]" if i.table else "Schema"
                    if i.column: loc += f".{i.column}"
                    st.error(f"{loc} — {i.message}")

    with tab_queries:
        if not queries:
            st.info("No generated queries available.")
        else:
            crud_tab, analytical_tab = st.tabs(["CRUD", "Analytical"])
            with crud_tab:
                for table_name, ops in queries.crud_queries.items():
                    with st.expander(table_name, expanded=False):
                        if isinstance(ops, dict):
                            for name, sql in ops.items():
                                st.markdown(f"**{name.replace('_',' ').title()}**")
                                st.code(sql, language="sql")
                        elif isinstance(ops, list):
                            for sql in ops:
                                st.code(sql, language="sql")
            with analytical_tab:
                if queries.analytical_queries:
                    for q in queries.analytical_queries:
                        with st.expander(q.get("name", "Analytical Query"), expanded=False):
                            if q.get("description"):
                                st.caption(q["description"])
                            st.code(q.get("sql", ""), language="sql")
                else:
                    st.info("No analytical queries were generated for this schema.")

    #  Navigation 
    st.markdown("---")
    col_modify, col_chat, col_new = st.columns(3)

    with col_modify:
        if st.button("✏️ Modify this database", type="primary", use_container_width=True):
            ws.state = WorkspaceState.MODIFIED
            ws.approval_status = None
            save(ws)
            st.rerun()

    with col_chat:
        if st.button("💬 Chat with this database", use_container_width=True):
            ws.state = WorkspaceState.QUERY_READY
            save(ws)
            st.rerun()

    with col_new:
        if st.button("🔄 New workspace", use_container_width=True):
            clear_workspace()
            st.rerun()



# Router for workspace states

def main() -> None:
    ws = get_or_create_workspace()
    render_sidebar(ws)

    state    = ws.state
    approval = ws.approval_status

    if state == WorkspaceState.EMPTY:
        render_input_phase(ws)
    elif state == WorkspaceState.SCHEMA_CREATED and approval == "awaiting":
        render_suggestion_phase(ws)
    elif state == WorkspaceState.DB_READY:
        render_results_phase(ws)