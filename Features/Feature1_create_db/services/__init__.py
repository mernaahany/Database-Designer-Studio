"""Service layer package for Feature 1."""

from .llm_service import get_chat_llm, get_embeddings

__all__ = [
    "get_chat_llm",
    "get_embeddings",
]
