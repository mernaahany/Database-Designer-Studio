"""
observability/tracing.py — Observability Layer

Langfuse-style tracing for every node in the Query Agent graph.

Every node wraps its execution in:
    with NodeTracer(state, node_name="classify_intent") as tracer:
        result = do_work()
        tracer.set_output(result)

This records: node name, input snapshot, output, latency_ms, status, errors.
All traces are appended to state["trace"] for the orchestrator to pick up.

In production: replace _emit() with Langfuse or OTEL SDK calls.
"""

from __future__ import annotations
import time
import traceback
from contextlib import contextmanager
from typing import Any, Generator


class NodeTracer:
    """
    Context manager that wraps a single LangGraph node execution.

    Records:
        - node: str
        - status: "success" | "error"
        - latency_ms: float
        - input_snapshot: dict (shallow copy of relevant state fields)
        - output_snapshot: dict
        - error: str (if exception)
    """

    def __init__(self, state: dict, node_name: str, input_fields: list[str] = None):
        self.node_name = node_name
        self.start_time = None
        self._trace_entry: dict = {}
        self._state = state
        self._input_fields = input_fields or []
        self._output: dict = {}

    def __enter__(self) -> "NodeTracer":
        self.start_time = time.perf_counter()
        self._trace_entry = {
            "node": self.node_name,
            "status": "running",
            "input": {k: self._state.get(k) for k in self._input_fields},
            "output": {},
            "latency_ms": 0.0,
            "error": None,
        }
        return self

    def set_output(self, output: dict) -> None:
        self._output = output

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed = (time.perf_counter() - self.start_time) * 1000
        self._trace_entry["latency_ms"] = round(elapsed, 2)

        if exc_type is not None:
            self._trace_entry["status"] = "error"
            self._trace_entry["error"] = traceback.format_exception_only(exc_type, exc_val)[0].strip()
        else:
            self._trace_entry["status"] = "success"
            self._trace_entry["output"] = self._output

        self._emit(self._trace_entry)

        # Append to state trace list
        if "trace" not in self._state:
            self._state["trace"] = []
        self._state["trace"].append(self._trace_entry)

        return False  # Don't suppress exceptions

    def _emit(self, entry: dict) -> None:
        """
        In production, replace with:
            langfuse.trace(name=entry["node"], metadata=entry)
        or:
            otel_tracer.start_span(entry["node"]).set_attributes(entry)
        """
        import json
        # For now: structured log (replace with proper logging sink)
        print(f"[TRACE] {entry['node']} | {entry['status']} | {entry['latency_ms']}ms")


def node_trace(node_name: str, input_fields: list[str] = None):
    """
    Decorator version of NodeTracer for LangGraph nodes.

    Usage:
        @node_trace("classify_intent", input_fields=["user_query"])
        def classify_intent(state: DBDesignerState) -> dict:
            ...
            return {"query_intent": intent}
    """
    def decorator(fn):
        def wrapper(state):
            with NodeTracer(state if isinstance(state, dict) else state.__dict__,
                            node_name=node_name,
                            input_fields=input_fields or []) as tracer:
                result = fn(state)
                tracer.set_output(result if isinstance(result, dict) else {})
                return result
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator