"""
graph/router.py — Conditional routing logic for the Query Agent LangGraph graph.
"""

from __future__ import annotations

from state import DBDesignerState
from core.validation_engine import _should_retry_correction


def route_after_retrieval(state: DBDesignerState) -> str:
    if state.is_analytical and state.query_complexity == "complex":
        return "optional_decompose_query"
    return "generate_sql"


def route_after_decomposition(state: DBDesignerState) -> str:
    return "generate_sql"



def route_after_validation(state) -> str:
    # Only retry/clarify on actual hard errors, not soft warnings
    if state.error_severity == "HARD_ERROR":
        if state.correction_attempts < 3:
            return "self_correct"
        return "request_clarification"
    # SOFT_WARNING or NO_ERROR → proceed
    return "format_result"