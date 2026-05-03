"""
Feature 3 — Chat With DB

Entry point: run_feature_3(workspace) -> Workspace

Phases:
  run_feature_3_pipeline  → generate SQL / clarification / approval gate
  run_feature_3_execute   → execute approved query and produce NL response
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from shared.blob_storage import download_db
from shared.workspace import Workspace, WorkspaceState

_PACKAGE_DIR = Path(__file__).resolve().parent
_PACKAGE_DIR_STR = str(_PACKAGE_DIR)
if _PACKAGE_DIR_STR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR_STR)

from .Queryagent import generate_nl_response, run_query_agent
from .execution.executor import execute_query_result
from .state import ChatHistoryEntry, DBDesignerState


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _last_user_message(workspace: Workspace) -> str:
    for entry in reversed(workspace.history):
        if entry.get("role") == "user":
            return str(entry.get("content", "")).strip()
    return (workspace.user_input or "").strip()


def _to_chat_history(workspace: Workspace) -> list[ChatHistoryEntry]:
    chat_history: list[ChatHistoryEntry] = []
    for entry in workspace.history:
        role = entry.get("role")
        content = str(entry.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            chat_history.append(ChatHistoryEntry(role=role, content=content))
    return chat_history


def _resolve_db_url(workspace: Workspace) -> str:
    if workspace.db_conn_url:
        return workspace.db_conn_url

    if workspace.db_blob_name:
        db_bytes = download_db(workspace.db_blob_name)
        suffix = Path(workspace.db_blob_name).suffix or ".db"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(db_bytes)
            workspace.db_local_path = tmp.name
        workspace.db_conn_url = f"sqlite:///{workspace.db_local_path}"
        return workspace.db_conn_url

    if workspace.db_local_path:
        workspace.db_conn_url = f"sqlite:///{workspace.db_local_path}"
        return workspace.db_conn_url

    return ""


def _build_query_state(
    workspace: Workspace,
    *,
    execution_requested: bool = False,
    approved_for_execution: bool | None = None,
) -> DBDesignerState:
    return DBDesignerState(
        user_query=_last_user_message(workspace),
        session_id=workspace.workspace_id,
        db_url=_resolve_db_url(workspace),
        db_schema=workspace.schema_ddl,
        chat_history=_to_chat_history(workspace),
        execution_requested=execution_requested,
        approved_for_execution=approved_for_execution,
    )


def _append_message(workspace: Workspace, role: str, content: str) -> Workspace:
    content = content.strip()
    if not content:
        return workspace

    history = list(workspace.history)
    candidate = {"role": role, "content": content}
    if history and history[-1] == candidate:
        return workspace

    history.append(candidate)
    workspace.history = history
    return workspace


def _write_back(workspace: Workspace, state: DBDesignerState) -> Workspace:
    sql = ""
    if state.query_result is not None:
        sql = (
            getattr(state.query_result, "query", None)
            or getattr(state.query_result, "sql", None)
            or getattr(state.query_result, "final_sql", None)
            or str(state.query_result)
        )

    raw = (
        getattr(state, "execution_result", None)
        or getattr(state, "rows", None)
        or getattr(state, "results", None)
    )

    rows: list[Any] = []
    columns: list[str] = []
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            rows = raw
            columns = list(first.keys())
        elif isinstance(first, (list, tuple)):
            columns = getattr(state, "columns", []) or []
            rows = [dict(zip(columns, row)) if columns else list(row) for row in raw]
        else:
            rows = raw

    nl = (
        getattr(state, "nl_response", None)
        or getattr(state, "response", None)
        or getattr(state, "answer", None)
        or ""
    )
    error = (
        getattr(state, "execution_error", None)
        or getattr(state, "error", None)
    )
    trace = (
        getattr(state, "trace", None)
        or getattr(state, "trace_log", None)
        or getattr(state, "latency_breakdown_ms", None)
        or []
    )

    workspace.feature3_data = {
        "sql": sql,
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
        "nl_response": nl,
        "error": error,
        "trace": trace if isinstance(trace, list) else [trace],
        "needs_clarification": bool(getattr(state, "needs_clarification", False)),
        "clarification_message": getattr(state, "clarification_message", "") or "",
    }
    workspace.last_query_result = dict(workspace.feature3_data)
    workspace.query_result = {
        "sql": sql,
        "row_count": len(rows),
        "error": error,
    }
    workspace.query_history = list(getattr(workspace, "query_history", []) or [])
    if state.user_query:
        workspace.query_history.append({"role": "user", "content": state.user_query})
    if nl:
        workspace.history = list(workspace.history) + [{"role": "assistant", "content": nl}]
        workspace.query_history.append({"role": "assistant", "content": nl})

    print("DEBUG feature3_data:", workspace.feature3_data)
    return workspace


# ─────────────────────────────────────────────
# Phase 1 — Generate / clarify / approval gate
# ─────────────────────────────────────────────

def run_feature_3_pipeline(workspace: Workspace) -> Workspace:
    state = _build_query_state(workspace)
    state = run_query_agent(state)
    print("DEBUG query_result:", state.query_result)
    print("DEBUG query_result type:", type(state.query_result))
    workspace.state = WorkspaceState.QUERY_READY

    if state.rejection_message:
        workspace = _write_back(workspace, state)
        workspace.approval_status = "error"
        return _append_message(workspace, "assistant", state.rejection_message)

    if state.needs_clarification:
        workspace = _write_back(workspace, state)
        workspace.approval_status = "clarifying"
        return workspace

    if state.human_approval_required and state.query_result:
        workspace = _write_back(workspace, state)
        workspace.approval_status = "awaiting"
        return workspace

    state.approved_for_execution = True
    execute_query_result(state)
    print("DEBUG execution_result:", state.execution_result)
    print("DEBUG execution_result type:", type(state.execution_result))
    if getattr(state, "execution_result", None):
        print("DEBUG first row:", state.execution_result[0])
        print("DEBUG first row type:", type(state.execution_result[0]))

    nl_updates = generate_nl_response(state)
    print("DEBUG nl_updates keys:", list(nl_updates.keys()))
    print("DEBUG nl_updates values:", nl_updates)
    for key, value in nl_updates.items():
        setattr(state, key, value)

    workspace = _write_back(workspace, state)
    workspace.approval_status = "error" if getattr(state, "execution_error", None) else None
    return workspace


# ─────────────────────────────────────────────
# Phase 2 — Execute approved query
# ─────────────────────────────────────────────

def run_feature_3_execute(workspace: Workspace) -> Workspace:
    state = _build_query_state(
        workspace,
        execution_requested=True,
        approved_for_execution=True,
    )
    state = run_query_agent(state)
    print("DEBUG query_result:", state.query_result)
    print("DEBUG query_result type:", type(state.query_result))
    workspace.state = WorkspaceState.QUERY_READY

    execute_query_result(state)
    print("DEBUG execution_result:", state.execution_result)
    print("DEBUG execution_result type:", type(state.execution_result))
    if getattr(state, "execution_result", None):
        print("DEBUG first row:", state.execution_result[0])
        print("DEBUG first row type:", type(state.execution_result[0]))

    nl_updates = generate_nl_response(state)
    print("DEBUG nl_updates keys:", list(nl_updates.keys()))
    print("DEBUG nl_updates values:", nl_updates)
    for key, value in nl_updates.items():
        setattr(state, key, value)

    workspace = _write_back(workspace, state)
    workspace.approval_status = "error" if getattr(state, "execution_error", None) else None
    return workspace


# ─────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────

def run_feature_3(workspace: Workspace) -> Workspace:
    # Guard — Feature 3 cannot run without a DB connection
    if not workspace.db_conn_url:
        if not _resolve_db_url(workspace):
            workspace.approval_status = "error"
            workspace.feature3_data = {
                "sql": "",
                "rows": [],
                "columns": [],
                "row_count": 0,
                "nl_response": "",
                "error": (
                    "No database connection URL found in workspace. "
                    "Ensure db_conn_url is set before calling Feature 3."
                ),
                "trace": [],
                "needs_clarification": False,
                "clarification_message": "",
            }
            print("DEBUG feature3_data:", workspace.feature3_data)
            return workspace

    status = workspace.approval_status

    if status in (None, "done", "clarifying"):
        return run_feature_3_pipeline(workspace)

    if status == "approved":
        return run_feature_3_execute(workspace)

    return workspace


__all__ = [
    "DBDesignerState",
    "execute_query_result",
    "generate_nl_response",
    "run_feature_3",
    "run_feature_3_execute",
    "run_feature_3_pipeline",
    "run_query_agent",
]
