"""
execution/executor.py — Execution layer for the LangGraph Query Agent.
"""
 
from __future__ import annotations
 
import time

from core.runtime_db import execute_sql
from shared.cache import TTLCache
from core.sql_utils import enforce_safety_cap
from state import DBDesignerState, ChatHistoryEntry
 

 
def _record_latency(state: DBDesignerState, key: str, start_time: float) -> None:
    state.latency_breakdown_ms[key] = round((time.perf_counter() - start_time) * 1000, 2)
 

def execute_query_result(state: DBDesignerState) -> DBDesignerState:
    """Execute the validated SQL if approval and connection details are present."""
    if not state.query_result:
        state.execution_error = "No validated SQL available for execution."
        return state
    if state.needs_clarification:
        state.execution_error = "Execution blocked because clarification is required."
        return state
    if not state.db_url:
        state.execution_error = "No database URL available for execution."
        return state
    if state.approved_for_execution is not True:
        state.human_approval_required = True
        return state
 
    start_time = time.perf_counter()
    try:
        sql = state.query_result.query
        sql = enforce_safety_cap(sql)

        # NEW CACHE: simple TTL execution cache to avoid duplicate DB hits for
        # identical SELECT queries. TTL keeps results fresh while reducing load.
        if not hasattr(execute_query_result, "_exec_cache"):
            execute_query_result._exec_cache = TTLCache(default_ttl=120)
        exec_cache: TTLCache = execute_query_result._exec_cache
        cache_key = (state.db_url, sql)
        cached = exec_cache.get(cache_key)
        if cached is not None:
            state.execution_result = cached
            state.execution_error = ""
        else:
            result = execute_sql(state.db_url, sql)
            state.execution_result = result
            state.execution_error = ""
            exec_cache.set(cache_key, result)
    except Exception as exc:
        state.execution_result = []
        state.execution_error = str(exc)
    _record_latency(state, "execution", start_time)
    return state
 
# History management
 
def _append_user_assistant_history(state: DBDesignerState) -> None:
    if not state.query_result or state.needs_clarification or state.rejection_message:
        return
    user_entry = ChatHistoryEntry(role="user", content=state.user_query)
    assistant_entry = ChatHistoryEntry(role="assistant", content=state.query_result.query)
    if state.chat_history and state.chat_history[-2:] == [user_entry, assistant_entry]:
        return
    state.chat_history.extend([user_entry, assistant_entry])
