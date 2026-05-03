"""
query_agent.py — Production Query Agent using LangChain and LangGraph.

"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Literal
from state import ChatHistoryEntry
import sqlglot
import sqlglot.expressions as exp
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(__file__))

from shared.config import settings
from core.runtime_db import (execute_sql,get_cached_schema_snapshot,preload_schema_cache,)
from core.sql_validation import validate_joins, validate_sql_query, classify_error_severity
from core.sql_utils import  enforce_safety_cap
from core.schema_builder import SchemaContextBuilder, SchemaGraph
from observability.tracing import node_trace
from utils.validation_utils import ( _validation_error_types, _validation_error_signature, _hash_text, _sqlglot_dialect,)
from prompts.templates import (build_table_selection_prompt, build_correction_prompt,)
from retrieval.hybrid_retriever import HybridFewShotRetriever, RetrieverConfig
from retrieval.pattern_library import get_seed_library
from state import DBDesignerState, ProposedArtifact, SQLGenerationResult, SchemaSnapshot
from nodes.intent_router import intent_router, _router_cache, IntentRouterOutput
from nodes.generate_sql import generate_sql, SQLGenerationOutput

from core.validation_engine import (_validation_result_updates, _should_retry_correction,_contains_retryable_error,_contains_schema_or_syntax_violation,)
from graph.router import (route_after_retrieval,route_after_decomposition,route_after_validation,)
from graph.builder import build_query_agent_graph as _build_query_agent_graph
from core.identifier_normalizer import normalize_sql_for_snapshot
from prompts.templates import build_nl_response_prompt
from execution.executor import (execute_query_result,_append_user_assistant_history,)


# Pydantic output schemas
class NLResponseOutput(BaseModel):
    response: str
    result_count: int = 0
    is_empty: bool = False
    
class SQLCorrectionOutput(BaseModel):
    """Structured output for the correction node — matches SQL_CORRECTION_SYSTEM schema."""
    query: str
    tables_used: list[str] = Field(default_factory=list)
    confidence: float
    fix_summary: str = ""


class ErrorSeverityOutput(BaseModel):
    error_severity: Literal["HARD_ERROR", "SOFT_WARNING", "NO_ERROR"]
    message: str = ""


# Module-level singletons

_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
    azure_deployment=settings.azure_chat_deployment,
    temperature=0,
)
_schema_builder = SchemaContextBuilder()
_retriever = HybridFewShotRetriever(
    config=RetrieverConfig(
        top_k=settings.retriever_top_k,
        dense_top_k=settings.retriever_dense_top_k,
        bm25_top_k=settings.retriever_bm25_top_k,
        rrf_k=settings.retriever_rrf_k,
        dense_weight=settings.retriever_dense_weight,
        bm25_weight=settings.retriever_bm25_weight,
        complexity_filter=settings.retriever_complexity_filter,
        max_tables_filter=settings.retriever_max_tables_filter,
    )
)
_retriever.load(get_seed_library())
_graph = None

_retrieval_cache: dict[tuple, list[dict[str, Any]]] = {}
_correction_cache: dict[tuple[str, str, str], str] = {}
_schema_summary_cache: dict[str, tuple[str, list[str]]] = {}

if settings.db_url:
    try:
        preload_schema_cache(settings.db_url, settings.database_schema)
    except Exception:
        pass


# Helpers

def _invoke_structured(
    model: type[BaseModel],
    *,
    system_prompt: str,
    user_prompt: str,
) -> BaseModel:
    return _llm.with_structured_output(model).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )


def _record_latency(state: DBDesignerState, key: str, start_time: float) -> None:
    state.latency_breakdown_ms[key] = round((time.perf_counter() - start_time) * 1000, 2)


def _clarification_message_from_intent(decision: IntentRouterOutput) -> str:
    """
    Derive a clarification or rejection message from the intent output fields.
    No CLARIFICATION_TEMPLATE needed — message is derived from structured output.
    """
    if decision.reject_reason:
        return decision.reject_reason
    if decision.resolved_context:
        return f"Could you clarify: {decision.resolved_context}"
    return "Please clarify your database question."


# 
# NODE: build_schema_context 


@node_trace("build_schema_context", input_fields=["db_url", "db_schema"])
def build_schema_context(state: DBDesignerState) -> dict[str, Any]:
    """Load schema dynamically when needed and compress it into DDL summary."""
    start_time = time.perf_counter()
    schema: SchemaSnapshot | None = state.current_schema
    schema_cache_key = state.schema_cache_key
    schema_cache_hit = state.schema_cache_hit
    schema_load_time_ms = state.schema_load_time_ms

    if schema is None and state.db_url:
        cache_result = get_cached_schema_snapshot(state.db_url, state.db_schema)
        schema = cache_result.snapshot
        schema_cache_key = cache_result.cache_key
        schema_cache_hit = cache_result.cache_hit
        schema_load_time_ms = cache_result.load_time_ms

    # OPTIMIZATION: reuse ddl_summary and table_names when schema cache key unchanged
    ddl_summary = "-- No schema available"
    if schema:
        cache_key = f"{schema.schema_id}:{schema.version}"
        cached = _schema_summary_cache.get(cache_key)
        if cached:
            ddl_summary, table_names = cached
        else:
            ddl_summary = _schema_builder.build(schema)
            table_names = _schema_builder.table_names(schema)
            _schema_summary_cache[cache_key] = (ddl_summary, table_names)
    else:
        table_names = []
    state.latency_breakdown_ms["schema_load_time"] = schema_load_time_ms
    state.latency_breakdown_ms["schema_cache_hit"] = 1.0 if schema_cache_hit else 0.0
    state.latency_breakdown_ms["schema_cache_miss"] = 0.0 if schema_cache_hit else 1.0
    _record_latency(state, "schema_building", start_time)

    return {
        "current_schema": schema,
        "db_dialect": schema.dialect if schema else state.db_dialect,
        "ddl_summary": ddl_summary,
        "schema_cache_key": schema_cache_key,
        "schema_cache_hit": schema_cache_hit,
        "schema_load_time_ms": schema_load_time_ms,
    }


# NODE: retrieve_fewshots  

@node_trace("retrieve_fewshots", input_fields=["user_query", "query_intent", "query_complexity", "db_dialect"])
def retrieve_fewshots(state: DBDesignerState) -> dict[str, Any]:
    """Retrieve canonical few-shot examples with request-level caching."""
    start_time = time.perf_counter()
    schema_id = state.current_schema.schema_id if state.current_schema else "no-schema"
    db_dialect = _effective_db_dialect(state)
    cache_key = (
        f"{schema_id}:{state.current_schema.version if state.current_schema else 0}",
        state.user_query.strip().lower(),
        state.query_intent,
        state.query_complexity,
        db_dialect,
        settings.retriever_top_k,
    )

    if cache_key in _retrieval_cache:
        examples = _retrieval_cache[cache_key]
    else:
        schema_tables = (
            _schema_builder.table_names(state.current_schema)
            if state.current_schema
            else []
        )
        examples = _retriever.retrieve(
            question=state.user_query,
            intent=state.query_intent,
            complexity=state.query_complexity,
            schema_tables=schema_tables,
            dialect=db_dialect,
        )
        _retrieval_cache[cache_key] = examples

    _record_latency(state, "retrieval", start_time)
    return {"retrieved_fewshots": examples}


def _effective_db_dialect(state: DBDesignerState) -> str:
    dialect = (state.db_dialect or "").strip().lower()
    if dialect and dialect != "unknown":
        return dialect
    if state.current_schema and state.current_schema.dialect:
        return state.current_schema.dialect.strip().lower()
    return "unknown"


# NODE: optional_decompose_query

@node_trace("optional_decompose_query", input_fields=["user_query", "is_analytical", "query_complexity"])
def optional_decompose_query(state: DBDesignerState) -> dict[str, Any]:
    """
    Format a CTE plan for complex analytical queries without an LLM call.
    Returns an empty list for non-analytical or simple/medium queries —
    build_generation_prompt treats an empty list as no CTE plan block.
    """
    if not state.is_analytical or state.query_complexity != "complex":
        return {"decomposition_plan": []}

    # Lightweight heuristic plan — enough signal for the generator to structure CTEs correctly. 
    plan = [
        {"name": "base", "purpose": "filter and select raw rows from source tables"},
        {"name": "aggregated", "purpose": "apply grouping and aggregation logic"},
        {"name": "final", "purpose": "join, rank, or format the result set"},
    ]
    return {"decomposition_plan": plan}


# NODE: validate_sql  ( schema/AST validation is prompt-independent)

@node_trace("validate_sql", input_fields=["generated_sql_raw", "db_dialect"])
def validate_sql(state: DBDesignerState) -> dict[str, Any]:
    start_time = time.perf_counter()

    try:
        parsed_payload = SQLGenerationOutput.model_validate_json(state.generated_sql_raw)
    except Exception as exc:
        _record_latency(state, "validation", start_time)
        return _validation_result_updates(state, [f"STRUCTURED_OUTPUT_ERROR: {exc}"], "HARD_ERROR")

    if parsed_payload.query.strip():
        parsed_payload = parsed_payload.model_copy(update={"needs_clarification": False})

    dialect = _effective_db_dialect(state)
    result = validate_sql_query(
        sql=parsed_payload.query,
        declared_tables=parsed_payload.tables_used,
        schema=state.current_schema,
        dialect=dialect,
    )
    errors = result.errors
    error_severity = result.severity

    _record_latency(state, "validation", start_time)
    return _validation_result_updates(state, errors, error_severity)


# NODE: self_correct

@node_trace("self_correct", input_fields=["validation_errors", "correction_attempts", "db_dialect"])
def self_correct(state: DBDesignerState) -> dict[str, Any]:
    """Retry SQL correction with validator feedback using new prompt signature."""
    correction_start = time.perf_counter()

    system_prompt, user_prompt = build_correction_prompt(
        bad_sql=state.generated_sql_raw,
        errors=state.validation_errors,
        schema_ddl=state.ddl_summary,
    )

    # Include schema id/version in the correction cache key for determinism
    schema_id = getattr(state.current_schema, "schema_id", "no-schema")
    schema_ver = getattr(state.current_schema, "version", 0)
    correction_key = (
        _hash_text(state.generated_sql_raw),
        state.validation_error_signature,
        state.ddl_summary,
        schema_id,
        schema_ver,
        _effective_db_dialect(state),
    )

    # NEW CACHE: only reuse cached correction when no prior correction attempts
    if state.correction_attempts == 0 and correction_key in _correction_cache:
        corrected_json = _correction_cache[correction_key]
    else:
        output = _invoke_structured(
            SQLCorrectionOutput,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        # Re-wrap into SQLGenerationOutput format so validate_sql can consume it
        # without knowing whether it came from generation or correction.
        try:
            prev = SQLGenerationOutput.model_validate_json(state.generated_sql_raw)
        except Exception:
            prev = SQLGenerationOutput(
                classification="AMBIGUOUS",
                complexity="MODERATE",
                strategy="SCHEMA_ONLY",
                query="",
                confidence=0.0,
                needs_clarification=False,
            )

        corrected = prev.model_copy(update={
            "query": output.query,
            "tables_used": output.tables_used,
            "confidence": output.confidence,
        })
        corrected_json = corrected.model_dump_json()
        _correction_cache[correction_key] = corrected_json

    correction_elapsed = round((time.perf_counter() - correction_start) * 1000, 2)
    _record_latency(state, f"correction_llm_{state.correction_attempts + 1}", correction_start)

    return {
        "generated_sql_raw": corrected_json,
        "correction_attempts": state.correction_attempts + 1,
        "correction_loop_count": state.correction_loop_count + 1,
        "correction_total_time_ms": round(state.correction_total_time_ms + correction_elapsed, 2),
        "validation_errors": [],
    }


# NODE: format_result  

@node_trace("format_result", input_fields=["generated_sql_raw"])
def format_result(state: DBDesignerState) -> dict[str, Any]:
    """Convert the structured SQL payload into the shared result dataclass.

    Identifier normalization runs here — after validation has passed — so
    the final SQL uses the exact quoted identifiers the database expects,
    regardless of what case the LLM generated or how the table was created.
    Works for any database: quoted mixed-case ("Employee"), lowercase
    (employee), or uppercase (EMPLOYEE).
    """
    parsed = SQLGenerationOutput.model_validate_json(state.generated_sql_raw)

    # Rewrite table/column names to match exact stored casing and add
    # dialect-correct quoting.  No-op when snapshot is None.
    normalized_sql = normalize_sql_for_snapshot(parsed.query, state.current_schema)

    result = SQLGenerationResult(
        classification=parsed.classification,
        complexity=parsed.complexity,
        strategy=parsed.strategy,
        query=normalized_sql,
        confidence=parsed.confidence,
        needs_clarification=parsed.needs_clarification,
        proposed_artifact=ProposedArtifact(**parsed.proposed_artifact.model_dump()),
    )
    return {"query_result": result}


# NODE: request_clarification

@node_trace("request_clarification", input_fields=["validation_errors", "user_query"])
def request_clarification(state: DBDesignerState) -> dict[str, Any]:
    """Fallback node when generation does not validate successfully."""
    if state.validation_errors:
        errors_str = "; ".join(state.validation_errors[:3])  # Cap to first 3 for readability
        message = (
            f"Could not generate a valid query for: \"{state.user_query}\". "
            f"Issues: {errors_str}. "
            "Please provide more detail or rephrase."
        )
    else:
        # Clarification path — use resolved_context from intent_router if available
        message = (
            f"Please clarify your request: {state.resolved_context}"
            if state.resolved_context
            else f"Please clarify: \"{state.user_query}\""
        )
    return {
        "needs_clarification": True,
        "clarification_message": message,
    }



def build_query_agent_graph():
    return _build_query_agent_graph(
        build_schema_context=build_schema_context,
        retrieve_fewshots=retrieve_fewshots,
        optional_decompose_query=optional_decompose_query,
        generate_sql=generate_sql,
        validate_sql=validate_sql,
        self_correct=self_correct,
        format_result=format_result,
        request_clarification=request_clarification,
    )


# run_query_agent — top-level orchestrator

def run_query_agent(state: DBDesignerState) -> DBDesignerState:
    """
    Run the full production query-agent pipeline.

    intent_router runs first, outside the graph.  Early exits for clarify/reject
    avoid graph initialisation cost (~2ms) on the most common non-query paths.
    All downstream nodes receive resolved_context via state — raw history is
    never passed to generation or correction prompts.
    """
    total_start = time.perf_counter()

    def apply(updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            setattr(state, key, value)

    # Step 1: Combined intent / safety / classification 
    apply(intent_router(state))

    if state.router_decision == "clarify":
        state.needs_clarification = True
        state.clarification_message = _clarification_message_from_intent(
            _router_cache.get(state.user_query.strip())
            or type("_", (), {"reject_reason": "", "resolved_context": state.resolved_context})()
        )
        _record_latency(state, "total_pipeline", total_start)
        return state

    if state.router_decision == "reject" or not state.safety_passed:
        state.rejection_message = state.rejection_message or state.router_reason or "Request rejected."
        _record_latency(state, "total_pipeline", total_start)
        return state

    if state.current_schema is None and not state.db_url:
        state.needs_clarification = True
        state.clarification_message = "A database connection or schema snapshot is required."
        _record_latency(state, "total_pipeline", total_start)
        return state

    #  Step 2: Graph (schema → retrieve → [decompose] → generate → validate) 
    global _graph
    if _graph is None:
        _graph = build_query_agent_graph()

    try:
        graph_result = _graph.invoke(state)
        if isinstance(graph_result, DBDesignerState):
            state = graph_result
        elif isinstance(graph_result, dict):
            for key, value in graph_result.items():
                setattr(state, key, value)
    except Exception:
        _run_graph_manually(state)

    # Step 3: Optional execution 
    if state.query_result and state.execution_requested:
        state.human_approval_required = settings.require_human_approval
        if state.approved_for_execution is True or not settings.require_human_approval:
            state.approved_for_execution = True
            execute_query_result(state)
            updates = generate_nl_response(state)
            for key, value in updates.items():
                setattr(state, key, value)

    _append_user_assistant_history(state)
    state.latency_breakdown_ms["correction_loop_count"] = float(state.correction_loop_count)
    state.latency_breakdown_ms["correction_total_time"] = round(state.correction_total_time_ms, 2)
    _record_latency(state, "total_pipeline", total_start)

    # LATENCY FIX: compute top-3 slowest nodes for quick triage
    try:
        items = [
            (k, v) for k, v in state.latency_breakdown_ms.items()
            if isinstance(v, (int, float)) and k != "total_pipeline"
        ]
        items.sort(key=lambda x: -x[1])
        top3 = items[:3]
        state.latency_breakdown_ms["top_3_nodes"] = ", ".join(f"{k}:{v:.0f}ms" for k, v in top3)
    except Exception:
        pass

    return state


@node_trace("generate_nl_response", input_fields=["user_query", "execution_result"])
def generate_nl_response(state: DBDesignerState) -> dict[str, Any]:
    """Generate a natural language summary of the execution results."""
    if not state.execution_result and not state.execution_error:
        return {"nl_response": "No results to summarise."}

    if state.execution_error:
        return {"nl_response": f"The query could not be executed: {state.execution_error}"}

    sql = state.query_result.query if state.query_result else ""

    system_prompt, user_prompt = build_nl_response_prompt(
        user_query=state.user_query,
        sql=sql,
        results=state.execution_result,
    )

    start_time = time.perf_counter()
    output = _invoke_structured(
        NLResponseOutput,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    _record_latency(state, "nl_response_llm", start_time)

    return {"nl_response": output.response}


# Internal helpers

def _run_graph_manually(state: DBDesignerState) -> None:
    """Fallback execution path that mirrors the LangGraph routing."""
    def apply(updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            setattr(state, key, value)

    apply(build_schema_context(state))
    apply(retrieve_fewshots(state))
    if route_after_retrieval(state) == "optional_decompose_query":
        apply(optional_decompose_query(state))
    apply(generate_sql(state))
    apply(validate_sql(state))
    while route_after_validation(state) == "self_correct":
        apply(self_correct(state))
        apply(validate_sql(state))
    if route_after_validation(state) == "format_result":
        apply(format_result(state))
    else:
        apply(request_clarification(state))
