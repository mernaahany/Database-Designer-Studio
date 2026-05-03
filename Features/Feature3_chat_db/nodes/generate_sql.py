"""
nodes/generate_sql.py — SQL generation node for the LangGraph Query Agent.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from shared.config import settings
from observability.tracing import node_trace
from prompts.templates import build_generation_prompt
from state import DBDesignerState
from shared.cache import TTLCache


# Pydantic output schemas
class ProposedArtifactOutput(BaseModel):
    type: Literal["NONE", "CREATE_VIEW", "SAVE_QUERY_TEMPLATE", "CACHE_RESULT"]
    reason: str = ""


class SQLGenerationOutput(BaseModel):
    classification: Literal["SIMPLE_LOOKUP", "AGGREGATION", "JOIN_QUERY", "COMPLEX", "AMBIGUOUS"]
    complexity: Literal["SIMPLE", "MODERATE", "COMPLEX"]
    strategy: Literal["SCHEMA_ONLY", "USE_EXAMPLES"]
    query: str
    tables_used: list[str] = Field(default_factory=list)
    confidence: float
    needs_clarification: bool
    clarification_hint: str = ""
    proposed_artifact: ProposedArtifactOutput = Field(
        default_factory=lambda: ProposedArtifactOutput(type="NONE", reason="")
    )



_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
    azure_deployment=settings.azure_chat_deployment,
    temperature=0,
)

# NEW CACHE: generation cache to avoid redundant LLM calls
import hashlib
import json
_generation_cache = TTLCache(default_ttl=600)


def _generation_cache_key(state: 'DBDesignerState') -> str:
    parts = [
        state.user_query.strip().lower(),
        (state.resolved_context or "").strip().lower(),
        getattr(state.current_schema, "schema_id", "no-schema"),
        str(getattr(state.current_schema, "version", 0)),
        json.dumps(state.decomposition_plan or [], sort_keys=True),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# Private helpers

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

# NODE: generate_sql

@node_trace("generate_sql", input_fields=["user_query", "query_intent", "query_complexity"])
def generate_sql(state: DBDesignerState) -> dict[str, Any]:
    """Generate SQL using the updated build_generation_prompt signature."""
    system_prompt, user_prompt = build_generation_prompt(
        user_query=state.user_query,
        resolved_context=state.resolved_context,
        intent=state.query_intent,
        complexity=state.query_complexity,
        schema_ddl=state.ddl_summary,
        few_shot_examples=state.retrieved_fewshots,
        cte_plan=state.decomposition_plan if state.decomposition_plan else None,
        db_dialect=state.db_dialect,
    )
    start_time = time.perf_counter()

    cache_key = _generation_cache_key(state)
    # Do NOT cache when prior validation errors exist or when correction attempts > 0
    should_skip_cache = bool(getattr(state, "validation_errors", [])) or getattr(state, "correction_attempts", 0) > 0
    if not should_skip_cache:
        cached_json = _generation_cache.get(cache_key)
        if cached_json is not None:
            _record_latency(state, "generation_llm", start_time)
            return {"generated_sql_raw": cached_json}

    output = _invoke_structured(
        SQLGenerationOutput,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    _record_latency(state, "generation_llm", start_time)

    generated_json = output.model_dump_json()
    if not should_skip_cache:
        _generation_cache.set(cache_key, generated_json)

    return {"generated_sql_raw": generated_json}
