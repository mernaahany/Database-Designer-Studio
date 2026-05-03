"""Central LLM service factory for the DB Designer Agent."""
from __future__ import annotations

from shared.config import get_chat_llm as _project_get_chat_llm, get_embeddings as _project_get_embeddings


def get_chat_llm(temperature: float = 0.0):
    """Return the project-configured Azure chat model."""
    return _project_get_chat_llm(temperature=temperature)


def get_embeddings():
    """Return the project-configured Azure embeddings client."""
    return _project_get_embeddings()
