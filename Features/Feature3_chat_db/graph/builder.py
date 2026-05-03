"""
graph/builder.py — Build the LangGraph graph for the Query Agent pipeline.

"""

from __future__ import annotations

from typing import Callable

from langgraph.graph import END, StateGraph

from state import DBDesignerState
from graph.router import (
    route_after_retrieval,
    route_after_decomposition,
    route_after_validation,
)



def build_query_agent_graph(
    *,
    build_schema_context: Callable,
    retrieve_fewshots: Callable,
    optional_decompose_query: Callable,
    generate_sql: Callable,
    validate_sql: Callable,
    self_correct: Callable,
    format_result: Callable,
    request_clarification: Callable,
):
    """
    Compile the LangGraph query agent.

    Each argument is a reference to a function that implements the corresponding node's logic.  This allows the graph structure to be decoupled from the implementation of each"""
    builder = StateGraph(DBDesignerState)
    builder.add_node("build_schema_context", build_schema_context)
    builder.add_node("retrieve_fewshots", retrieve_fewshots)
    builder.add_node("optional_decompose_query", optional_decompose_query)
    builder.add_node("generate_sql", generate_sql)
    builder.add_node("validate_sql", validate_sql)
    builder.add_node("self_correct", self_correct)
    builder.add_node("format_result", format_result)
    builder.add_node("request_clarification", request_clarification)

    builder.set_entry_point("build_schema_context")
    builder.add_edge("build_schema_context", "retrieve_fewshots")
    builder.add_conditional_edges(
        "retrieve_fewshots",
        route_after_retrieval,
        {
            "optional_decompose_query": "optional_decompose_query",
            "generate_sql": "generate_sql",
        },
    )
    builder.add_conditional_edges(
        "optional_decompose_query",
        route_after_decomposition,
        {"generate_sql": "generate_sql"},
    )
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges(
        "validate_sql",
        route_after_validation,
        {
            "format_result": "format_result",
            "self_correct": "self_correct",
            "request_clarification": "request_clarification",
        },
    )
    builder.add_edge("self_correct", "validate_sql")
    builder.add_edge("format_result", END)
    builder.add_edge("request_clarification", END)
    return builder.compile()