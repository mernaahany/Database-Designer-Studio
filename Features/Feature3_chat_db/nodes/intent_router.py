"""
nodes/intent_router.py — Intent routing node for the LangGraph Query Agent.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel

from shared.config import settings
from observability.tracing import NodeTracer
from shared.cache import TTLCache
from prompts.templates import build_intent_prompt
from state import DBDesignerState


# Pydantic output schema


class IntentRouterOutput(BaseModel):
    """
    Combined output for route + safety + relevance + classification.
    Replaces IntentRouterOutput + SafetyCheckOutput + QueryClassificationOutput.
    Mirrors INTENT_SYSTEM JSON schema exactly.
    """
    route: Literal["query", "clarify", "reject"]
    intent: Literal["aggregation", "join", "filter", "analytical"] # 
    complexity: Literal["simple", "medium", "complex"]
    is_analytical: bool
    safe: bool
    resolved_context: str = ""
    reject_reason: str = ""


# Module-level singletons (private to this module)

_llm = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
    azure_deployment=settings.azure_chat_deployment,
    temperature=0,
)

# Process-level cache.
_router_cache = TTLCache(default_ttl=600)

import hashlib


def _cache_key_for_intent(state: 'DBDesignerState') -> str:
    parts = [
        state.user_query.strip().lower(),
        (state.resolved_context or "").strip().lower(),
        getattr(state.current_schema, "schema_id", "no-schema"),
        str(getattr(state.current_schema, "version", 0)),
    ]
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return h


# Private helpers (mirrors the equivalents in query_agent.py; kept private to

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

# NODE: intent_router

def intent_router(state: DBDesignerState) -> dict[str, Any]:
    """
    Single combined node for routing, safety, relevance, and classification.
    Downstream nodes receive resolved_context instead of raw chat history.
    """
    cache_key = _cache_key_for_intent(state)
    start_time = time.perf_counter()

    with NodeTracer(state.__dict__, "intent_router", ["user_query"]) as tracer:
        cached = _router_cache.get(cache_key)
        if cached is not None:
            decision = cached
        else:
            system_prompt, user_prompt = build_intent_prompt(
                user_query=state.user_query,
                history=state.chat_history,
            )
            decision = _invoke_structured(
                IntentRouterOutput,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            # OPTIMIZATION: cache LLM output for identical query+schema context
            _router_cache.set(cache_key, decision)

        result = {
            # Routing
            "router_decision": decision.route,
            "router_reason": decision.reject_reason,
            # Safety / relevance (derived from route + safe flag)
            "safety_passed": decision.safe,
            "relevance_passed": decision.route != "reject",
            "rejection_message": decision.reject_reason if not decision.safe else "",
            # Classification — previously a separate node
            "query_intent": decision.intent,
            "query_complexity": decision.complexity,
            "is_analytical": decision.is_analytical,
            # Resolved context — the ONLY thing downstream nodes see from history
            "resolved_context": decision.resolved_context,
        }
        tracer.set_output(result)

    _record_latency(state, "intent_router", start_time)
    return result
