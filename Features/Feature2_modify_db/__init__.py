"""
Feature 2 — Modify DB 
__init__.py — Orchestrates the end-to-end flow for the "Modify Database Schema" feature.
=====================
Entry point: run_feature_2(workspace) -> Workspace

Phases (controlled by workspace.approval_status):
  None / "done"  → run_feature_2_pipeline   (new request, start fresh)
  "clarifying"   → run_feature_2_pipeline   (clarification answered, continue)
  "approved"     → run_feature_2_execute
  "revising"     → run_feature_2_revision   (human requested changes)
"""
from __future__ import annotations
from shared.workspace import Workspace, WorkspaceState
from .agents.clarifier import run_clarifier
from .agents.modifier  import run_modifier
from .agents.validator import run_validator
from .agents.executor  import run_executor
from .utils.erd_data     import extract_erd_data
from .utils.erd_renderer import render_erd_html
from .utils.file_import  import parse_upload_file, validate_import, build_insert_statements
from .utils.db_utils     import (
    get_table_row_counts,
    execute_sql_statements,
    extract_schema,
    ingest_uploaded_db,
)
from .utils.memory import create_modification_record, format_history_for_display
from .utils.pdf_report import generate_pdf_report
__all__ = [
    # pipeline
    "run_feature_2",
    "run_feature_2_pipeline",
    "run_feature_2_execute",
    "run_feature_2_revision",
    "ingest_uploaded_db",
    "extract_schema",

    # db utils
    "ingest_uploaded_db",
    "extract_schema",
    "get_table_row_counts",
    "execute_sql_statements",
    # erd
    "extract_erd_data",
    "render_erd_html",
    # file import
    "parse_upload_file",
    "validate_import",
    "build_insert_statements",
    # memory
    "create_modification_record",
    "format_history_for_display",
    "generate_pdf_report",
]

# ─────────────────────────────────────────────────────────────
# State bridge  Workspace ↔ GraphState
# ─────────────────────────────────────────────────────────────

def _build_graph_state(workspace: Workspace) -> dict:
    """Map Workspace fields → GraphState dict for Feature 2 agents."""
    f2 = workspace.feature2_data
    return {
        "messages":               [],
        "db_path":                workspace.db_blob_name,        # blob name, not full URL
        "db_schema":              workspace.schema_ddl or "",
        "user_request":           f2.get("user_request", ""),
        "clarification_needed":   False,
        "clarification_question": f2.get("clarification_question", ""),
        "clarification_answers":  list(f2.get("clarification_answers", [])),
        "modification_plan":      f2.get("pending_plan"),
        "validation_result":      f2.get("pending_validation"),
        "validation_iterations":  f2.get("validation_iterations", 0),
        "human_approved":         None,
        "human_feedback":         "",
        "modification_history":   list(workspace.modification_history),
        "error":                  "",
        "next_action":            "",
    }


def _write_back(workspace: Workspace, state: dict) -> Workspace:
    """Write GraphState fields back into Workspace after a pipeline step."""
    f2 = dict(workspace.feature2_data)

    # Feature 2 intermediates
    f2["pending_plan"]           = state.get("modification_plan")
    f2["pending_validation"]     = state.get("validation_result")
    f2["clarification_question"] = state.get("clarification_question", "")
    f2["clarification_answers"]  = state.get("clarification_answers", [])
    f2["validation_iterations"]  = state.get("validation_iterations", 0)
    f2["error"]                  = state.get("error", "")

    workspace.feature2_data = f2

    # Shared workspace fields — updated by executor
    if state.get("db_schema"):
        workspace.schema_ddl = state["db_schema"]
    if state.get("modification_history") is not None:
        workspace.modification_history = state["modification_history"]

    return workspace


# ─────────────────────────────────────────────────────────────
# Phase 1 — Clarifier → Modifier → Validator loop
# ─────────────────────────────────────────────────────────────

def run_feature_2_pipeline(workspace: Workspace) -> Workspace:
    """
    Full pre-execution pipeline.
    Stops and returns if clarification is needed (approval_status = "clarifying")
    or when ready for human review (approval_status = "human_review").
    """
    state = _build_graph_state(workspace)

    # Step 1 — Clarifier
    upd = run_clarifier(state)
    state.update(upd)

    if state["next_action"] == "clarify":
        workspace = _write_back(workspace, state)
        workspace.approval_status = "clarifying"
        return workspace

    # Steps 2+3 — Modifier → Validator refinement loop
    # Validator sets next_action = "human_review" when done
    while True:
        upd = run_modifier(state)
        state.update(upd)

        if state.get("next_action") == "error":
            workspace = _write_back(workspace, state)
            workspace.approval_status = "error"
            return workspace

        upd = run_validator(state)
        state.update(upd)

        if state["next_action"] == "human_review":
            break

    workspace = _write_back(workspace, state)
    workspace.approval_status = "human_review"
    return workspace


# ─────────────────────────────────────────────────────────────
# Phase 2 — Executor (after human approves)
# ─────────────────────────────────────────────────────────────

def run_feature_2_execute(workspace: Workspace) -> Workspace:
    """Apply approved plan to the database."""
    state = _build_graph_state(workspace)
    state["human_approved"] = True

    upd = run_executor(state)
    state.update(upd)
    workspace = _write_back(workspace, state)

    if state.get("next_action") == "error":
        workspace.approval_status = "error"
        return workspace

    # Success — clean up intermediates
    f2 = dict(workspace.feature2_data)
    f2["pending_plan"]          = None
    f2["pending_validation"]    = None
    f2["clarification_answers"] = []
    f2["validation_iterations"] = 0
    workspace.feature2_data     = f2

    workspace.approval_status = "done"
    workspace.state           = WorkspaceState.MODIFIED
    return workspace


# ─────────────────────────────────────────────────────────────
# Phase 3 — Revision (human requested changes)
# ─────────────────────────────────────────────────────────────

def run_feature_2_revision(workspace: Workspace) -> Workspace:
    """
    Re-run modifier+validator after human feedback.
    Gets a fresh validation budget (iterations reset to 0).
    """
    state = _build_graph_state(workspace)
    state["validation_iterations"] = 0  # fresh budget for this revision

    while True:
        upd = run_modifier(state)
        state.update(upd)

        if state.get("next_action") == "error":
            workspace = _write_back(workspace, state)
            workspace.approval_status = "error"
            return workspace

        upd = run_validator(state)
        state.update(upd)

        if state["next_action"] == "human_review":
            break

    workspace = _write_back(workspace, state)
    workspace.approval_status = "human_review"
    return workspace


# ─────────────────────────────────────────────────────────────
# Main entry point — called by feature2_app.py
# ─────────────────────────────────────────────────────────────

def run_feature_2(workspace: Workspace) -> Workspace:
    status = workspace.approval_status

    if status in (None, "done"):
        # New modification request from user
        return run_feature_2_pipeline(workspace)

    if status == "clarifying":
        # User answered the clarification question — continue pipeline
        return run_feature_2_pipeline(workspace)

    if status == "approved":
        return run_feature_2_execute(workspace)

    if status == "revising":
        return run_feature_2_revision(workspace)

    # "human_review" / "error" → orchestrator/UI handles, no pipeline action
    return workspace