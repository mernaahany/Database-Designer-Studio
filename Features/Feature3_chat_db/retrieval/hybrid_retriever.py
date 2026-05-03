"""
retrieval/hybrid_retriever.py — Dense + BM25 -> RRF few-shot retriever.

Design:
  - AzureOpenAIEmbeddings for dense vectors.
  - BM25Okapi for keyword recall.
  - Reciprocal Rank Fusion over dense and BM25 ranks only.
  - Optional bounded feedback adjustment after RRF.
"""

from __future__ import annotations

import os
import re
import sys
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from langchain_openai import AzureOpenAIEmbeddings
from rank_bm25 import BM25Okapi

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shared.config import settings

PatternRecord = dict[str, Any]

_CANONICAL_PATTERN_FIELDS = (
    "id",
    "question",
    "sql",
    "intent",
    "complexity",
    "pattern_tags",
    "num_tables",
    "dialect",
    "explanation",
)


@dataclass
class RetrieverConfig:
    """Runtime controls for hybrid retrieval."""

    top_k: int = 3
    dense_top_k: int = 10
    bm25_top_k: int = 10
    rrf_k: int = 60
    dense_weight: float = 1.0
    bm25_weight: float = 1.0
    complexity_filter: str | None = None
    max_tables_filter: int | None = None
    feedback_alpha: float = 0.1
    feedback_max_delta: float = 0.2


@dataclass
class FeedbackEntry:
    """Aggregated feedback counts for a retrievable example."""

    pattern_id: str
    question_key: str | None = None
    positive_hits: int = 0
    negative_hits: int = 0
    decay_weight: float = 1.0

    def record(self, positive: bool) -> None:
        """Update counters for one retrieval outcome."""
        if positive:
            self.positive_hits += 1
        else:
            self.negative_hits += 1

    def effective_counts(self) -> tuple[float, float]:
        """Return counts after decay. Hook stays local and deterministic."""
        decay = self.time_decay_multiplier()
        return self.positive_hits * decay, self.negative_hits * decay

    def time_decay_multiplier(self) -> float:
        """Stub for optional decay logic."""
        return self.decay_weight


class FeedbackMemoryStore:
    """In-memory feedback store with bounded score deltas."""

    def __init__(self, alpha: float = 0.1, max_delta: float = 0.2) -> None:
        self.alpha = alpha
        self.max_delta = max_delta
        self._pattern_entries: dict[str, FeedbackEntry] = {}
        self._question_entries: dict[tuple[str, str], FeedbackEntry] = {}

    def record_feedback(
        self,
        pattern_id: str,
        question: str,
        *,
        positive: bool,
    ) -> None:
        """Record a positive or negative retrieval outcome."""
        normalized_question = HybridFewShotRetriever._normalize_feedback_key(question)
        pattern_entry = self._pattern_entries.setdefault(
            pattern_id,
            FeedbackEntry(pattern_id=pattern_id),
        )
        question_entry = self._question_entries.setdefault(
            (pattern_id, normalized_question),
            FeedbackEntry(pattern_id=pattern_id, question_key=normalized_question),
        )
        pattern_entry.record(positive)
        question_entry.record(positive)

    def feedback_delta(self, pattern_id: str, question: str) -> float:
        """Return the bounded additive delta for one pattern/question pair."""
        normalized_question = HybridFewShotRetriever._normalize_feedback_key(question)
        question_entry = self._question_entries.get((pattern_id, normalized_question))
        if question_entry is not None:
            positive_hits, negative_hits = question_entry.effective_counts()
        else:
            pattern_entry = self._pattern_entries.get(pattern_id)
            if pattern_entry is None:
                return 0.0
            positive_hits, negative_hits = pattern_entry.effective_counts()
        raw_delta = self.alpha * (positive_hits - negative_hits)
        return self._clamp(raw_delta, -self.max_delta, self.max_delta)

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))


class HybridFewShotRetriever:
    """
    Hybrid few-shot retriever: Filtering -> Dense -> BM25 -> RRF -> Feedback.

    Usage:
        retriever = HybridFewShotRetriever(config=RetrieverConfig(top_k=3))
        retriever.load(get_seed_library())
        examples = retriever.retrieve(question="...", intent="join", complexity="medium", schema_tables=[])
    """

    def __init__(
        self,
        config: RetrieverConfig,
        feedback_store: FeedbackMemoryStore | None = None,
    ) -> None:
        self.config = config
        self._embedder = AzureOpenAIEmbeddings(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_embedding_deployment,
        )
        self._feedback_store = feedback_store or FeedbackMemoryStore(
            alpha=config.feedback_alpha,
            max_delta=config.feedback_max_delta,
        )
        self._patterns: list[PatternRecord] = []
        self._pattern_tokens: list[list[str]] = []
        self._pattern_vectors: list[np.ndarray | None] = []
        self._pattern_vector_norms: list[float | None] = []
        self._embedding_dim: int | None = None
        self._bm25: BM25Okapi | None = None
        self._bm25_dirty: bool = False
        self._corpus_version: int = 0
        self._corpus_fingerprint: tuple[tuple[str, str], ...] = ()

    def load(self, patterns: list[dict[str, Any]]) -> None:
        """Normalize patterns, compute embeddings, and build the BM25 index."""
        if not patterns:
            self._reset_corpus()
            return

        normalized_patterns: list[PatternRecord] = []
        tokenized_patterns: list[list[str]] = []
        for raw_pattern in patterns:
            normalized_pattern = self._normalize_pattern(raw_pattern)
            if normalized_pattern is None:
                continue
            normalized_patterns.append(normalized_pattern)
            tokenized_patterns.append(self._tokenize(normalized_pattern["question"]))

        if not normalized_patterns:
            self._reset_corpus()
            return

        fingerprint = tuple(
            (pattern["id"], pattern["question"]) for pattern in normalized_patterns
        )
        corpus_changed = fingerprint != self._corpus_fingerprint
        self._patterns = normalized_patterns
        self._pattern_tokens = tokenized_patterns
        self._bm25_dirty = True

        if corpus_changed:
            self._corpus_version += 1
            self._corpus_fingerprint = fingerprint

        self._rebuild_embeddings()
        self._rebuild_bm25()

    def retrieve(
        self,
        question: str | None = None,
        intent: str = "",
        complexity: str = "",
        schema_tables: list[str] | None = None,
        dialect: str = "",
        return_scores: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return top-k patterns ranked by RRF plus bounded feedback."""
        del schema_tables

        normalized_question = self._resolve_question(question=question, **kwargs)
        if not normalized_question or not self._patterns:
            return []

        candidate_indices = self._filter_candidates(
            intent=intent,
            complexity=complexity,
            dialect=dialect,
        )
        if not candidate_indices:
            return []

        dense_scores = self._rank_dense(normalized_question, candidate_indices)
        bm25_scores = self._rank_bm25(normalized_question, candidate_indices)
        if not dense_scores and not bm25_scores:
            return []

        dense_ranks = self._sorted_rankings(dense_scores, limit=self.config.dense_top_k)
        bm25_ranks = self._sorted_rankings(bm25_scores, limit=self.config.bm25_top_k)
        rrf_scores = self._fuse_rrf(
            candidate_indices=candidate_indices,
            dense_ranks=dense_ranks,
            bm25_ranks=bm25_ranks,
        )

        scored_results: list[dict[str, Any]] = []
        for index in candidate_indices:
            if index not in rrf_scores:
                continue
            pattern = self._patterns[index]
            feedback_delta = self._apply_feedback(
                pattern_id=pattern["id"],
                question=normalized_question,
            )
            final_score = rrf_scores[index] + feedback_delta
            scored_results.append(
                {
                    "index": index,
                    "pattern": pattern,
                    "dense_score": dense_scores.get(index),
                    "bm25_score": bm25_scores.get(index),
                    "rrf_score": rrf_scores[index],
                    "feedback_delta": feedback_delta,
                    "score": final_score,
                }
            )

        scored_results.sort(
            key=lambda item: (
                -item["score"],
                item["pattern"]["id"],
                item["index"],
            )
        )

        top_results = scored_results[: self.config.top_k]
        return self._format_results(top_results, return_scores=return_scores)

    def _reset_corpus(self) -> None:
        """Clear all retrieval state."""
        self._patterns = []
        self._pattern_tokens = []
        self._pattern_vectors = []
        self._pattern_vector_norms = []
        self._embedding_dim = None
        self._bm25 = None
        self._bm25_dirty = False
        self._corpus_fingerprint = ()

    def _normalize_pattern(self, raw_pattern: dict[str, Any]) -> PatternRecord | None:
        """Validate and canonicalize one pattern record."""
        if not isinstance(raw_pattern, dict):
            warnings.warn("Skipping malformed pattern: expected dict.", stacklevel=2)
            return None

        pattern = dict(raw_pattern)
        if "question" not in pattern and "query" in pattern:
            warnings.warn(
                "Pattern field 'query' is deprecated; normalizing to 'question'.",
                FutureWarning,
                stacklevel=2,
            )
            pattern["question"] = pattern.pop("query")
        else:
            pattern.pop("query", None)

        required_fields = (
            "id",
            "question",
            "sql",
            "intent",
            "complexity",
            "pattern_tags",
            "num_tables",
        )
        missing_fields = [field for field in required_fields if field not in pattern]
        if missing_fields:
            warnings.warn(
                f"Skipping malformed pattern: missing fields {missing_fields}.",
                stacklevel=2,
            )
            return None

        question = str(pattern["question"]).strip()
        if not question:
            warnings.warn(
                f"Skipping malformed pattern '{pattern.get('id', '<unknown>')}': blank question.",
                stacklevel=2,
            )
            return None

        pattern_tags = pattern.get("pattern_tags")
        if not isinstance(pattern_tags, list):
            warnings.warn(
                f"Skipping malformed pattern '{pattern.get('id', '<unknown>')}': pattern_tags must be a list.",
                stacklevel=2,
            )
            return None

        try:
            num_tables = int(pattern["num_tables"])
        except (TypeError, ValueError):
            warnings.warn(
                f"Skipping malformed pattern '{pattern.get('id', '<unknown>')}': num_tables must be an integer.",
                stacklevel=2,
            )
            return None

        normalized: PatternRecord = {
            "id": str(pattern["id"]),
            "question": question,
            "sql": str(pattern["sql"]),
            "intent": self._normalize_text(str(pattern["intent"])),
            "complexity": self._normalize_text(str(pattern["complexity"])),
            "pattern_tags": [self._normalize_text(str(tag)) for tag in pattern_tags],
            "num_tables": num_tables,
            "dialect": str(pattern.get("dialect", "postgresql")),
            "explanation": str(pattern.get("explanation", "")),
        }
        return normalized

    def _rebuild_embeddings(self) -> None:
        """Compute and validate dense vectors for the current corpus."""
        questions = [pattern["question"] for pattern in self._patterns]
        self._pattern_vectors = [None] * len(self._patterns)
        self._pattern_vector_norms = [None] * len(self._patterns)
        self._embedding_dim = None

        try:
            embedded_documents = self._embedder.embed_documents(questions)
        except Exception as exc:
            warnings.warn(
                f"Embedding build failed; dense retrieval disabled for this corpus: {exc}",
                stacklevel=2,
            )
            return

        for index, raw_vector in enumerate(embedded_documents):
            validated_vector = self._validate_vector(raw_vector, index=index)
            if validated_vector is None:
                validated_vector = self._lazy_recompute_vector(index=index)
            if validated_vector is None:
                continue
            self._pattern_vectors[index] = validated_vector
            self._pattern_vector_norms[index] = float(np.linalg.norm(validated_vector))

    def _rebuild_bm25(self) -> None:
        """Build or rebuild the BM25 index from normalized tokens."""
        self._bm25 = None
        if not self._pattern_tokens:
            self._bm25_dirty = False
            return
        self._bm25 = BM25Okapi(self._pattern_tokens)
        self._bm25_dirty = False

    def _filter_candidates(self, intent: str, complexity: str, dialect: str = "") -> list[int]:
        """Apply intent, dialect, and optional metadata filters with relaxation."""
        all_indices = list(range(len(self._patterns)))
        if not all_indices:
            return []

        normalized_dialect = self._normalize_text(dialect)
        if normalized_dialect and normalized_dialect != "unknown":
            dialect_candidates = [
                index
                for index in all_indices
                if self._normalize_text(self._patterns[index]["dialect"]) == normalized_dialect
            ]
            if dialect_candidates:
                all_indices = dialect_candidates
            else:
                generic_candidates = [
                    index
                    for index in all_indices
                    if self._normalize_text(self._patterns[index]["dialect"]) in {"", "generic", "agnostic", "any", "unknown"}
                ]
                if generic_candidates:
                    all_indices = generic_candidates

        normalized_intent = self._normalize_text(intent)
        intent_candidates = [
            index
            for index in all_indices
            if self._patterns[index]["intent"] == normalized_intent
        ]
        base_candidates = intent_candidates or all_indices

        effective_complexity = self._normalize_text(complexity) or (
            self._normalize_text(self.config.complexity_filter)
            if self.config.complexity_filter
            else ""
        )
        effective_max_tables = self.config.max_tables_filter

        filtered_candidates = base_candidates
        if effective_complexity:
            complexity_filtered = [
                index
                for index in filtered_candidates
                if self._patterns[index]["complexity"] == effective_complexity
            ]
            if complexity_filtered:
                filtered_candidates = complexity_filtered

        if effective_max_tables is not None:
            table_filtered = [
                index
                for index in filtered_candidates
                if self._patterns[index]["num_tables"] <= effective_max_tables
            ]
            if table_filtered:
                filtered_candidates = table_filtered

        return filtered_candidates or base_candidates

    def _rank_dense(self, question: str, candidate_indices: list[int]) -> dict[int, float]:
        """Return cosine scores for candidates with valid vectors."""
        if not candidate_indices:
            return {}

        try:
            raw_query_vector = self._embedder.embed_query(question)
        except Exception as exc:
            warnings.warn(
                f"Query embedding failed; falling back to BM25-only retrieval: {exc}",
                stacklevel=2,
            )
            return {}

        query_vector = self._validate_query_vector(raw_query_vector)
        if query_vector is None:
            return {}

        query_norm = float(np.linalg.norm(query_vector))
        if query_norm <= 0.0:
            return {}

        dense_scores: dict[int, float] = {}
        for index in candidate_indices:
            vector = self._pattern_vectors[index] if index < len(self._pattern_vectors) else None
            vector_norm = (
                self._pattern_vector_norms[index]
                if index < len(self._pattern_vector_norms)
                else None
            )
            if vector is None or vector_norm is None or vector_norm <= 0.0:
                continue
            score = float(np.dot(vector, query_vector) / (vector_norm * query_norm))
            dense_scores[index] = score
        return dense_scores

    def _rank_bm25(self, question: str, candidate_indices: list[int]) -> dict[int, float]:
        """Return BM25 scores for the current candidate set."""
        if not candidate_indices or self._bm25 is None or self._bm25_dirty:
            return {}

        query_tokens = self._tokenize(question)
        if not query_tokens:
            return {}

        all_scores = self._bm25.get_scores(query_tokens)
        return {index: float(all_scores[index]) for index in candidate_indices}

    def _sorted_rankings(self, scores: dict[int, float], limit: int) -> list[int]:
        """Return a deterministic ranked list of candidate indices."""
        if limit <= 0 or not scores:
            return []
        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], self._patterns[item[0]]["id"], item[0]),
        )
        return [index for index, _ in ranked[:limit]]

    def _fuse_rrf(
        self,
        *,
        candidate_indices: list[int],
        dense_ranks: list[int],
        bm25_ranks: list[int],
    ) -> dict[int, float]:
        """Fuse dense and BM25 ranks using weighted reciprocal rank fusion."""
        rrf_scores = {index: 0.0 for index in candidate_indices}
        if self.config.dense_weight > 0.0:
            for rank, index in enumerate(dense_ranks, start=1):
                rrf_scores[index] += self.config.dense_weight / (self.config.rrf_k + rank)
        if self.config.bm25_weight > 0.0:
            for rank, index in enumerate(bm25_ranks, start=1):
                rrf_scores[index] += self.config.bm25_weight / (self.config.rrf_k + rank)
        return {index: score for index, score in rrf_scores.items() if score > 0.0}

    def _apply_feedback(self, pattern_id: str, question: str) -> float:
        """Apply bounded feedback after RRF, before final sorting."""
        return self._feedback_store.feedback_delta(pattern_id, question)

    def _format_results(
        self,
        scored_results: list[dict[str, Any]],
        *,
        return_scores: bool,
    ) -> list[dict[str, Any]]:
        """Return canonical pattern records, optionally with score breakdowns."""
        results: list[dict[str, Any]] = []
        for item in scored_results:
            pattern = item["pattern"]
            record: dict[str, Any] = {
                "id": pattern["id"],
                "question": pattern["question"],
                "sql": pattern["sql"],
                "intent": pattern["intent"],
                "complexity": pattern["complexity"],
                "pattern_tags": list(pattern["pattern_tags"]),
                "num_tables": pattern["num_tables"],
                "dialect": pattern["dialect"],
                "explanation": pattern["explanation"],
            }
            if return_scores:
                record.update(
                    {
                        "score": item["score"],
                        "dense_score": item["dense_score"],
                        "bm25_score": item["bm25_score"],
                        "rrf_score": item["rrf_score"],
                        "feedback_delta": item["feedback_delta"],
                    }
                )
            results.append(record)
        return results

    def _resolve_question(self, question: str | None, **kwargs: Any) -> str:
        """Resolve canonical question input at the public API boundary."""
        if "query" in kwargs:
            warnings.warn(
                "Retriever argument 'query' is deprecated; use 'question' instead.",
                FutureWarning,
                stacklevel=2,
            )
            if question is None:
                question = kwargs.pop("query")
            else:
                kwargs.pop("query")
        return str(question or "").strip()

    def _validate_vector(self, raw_vector: Any, *, index: int) -> np.ndarray | None:
        """Validate one stored embedding vector."""
        if raw_vector is None:
            warnings.warn(
                f"Skipping dense vector for pattern '{self._patterns[index]['id']}': missing embedding.",
                stacklevel=2,
            )
            return None
        try:
            vector = np.asarray(raw_vector, dtype=np.float32)
        except (TypeError, ValueError):
            warnings.warn(
                f"Skipping dense vector for pattern '{self._patterns[index]['id']}': invalid embedding type.",
                stacklevel=2,
            )
            return None

        if vector.ndim != 1 or vector.size == 0:
            warnings.warn(
                f"Skipping dense vector for pattern '{self._patterns[index]['id']}': invalid embedding shape.",
                stacklevel=2,
            )
            return None

        if self._embedding_dim is None:
            self._embedding_dim = int(vector.size)
        elif vector.size != self._embedding_dim:
            warnings.warn(
                f"Skipping dense vector for pattern '{self._patterns[index]['id']}': expected dim {self._embedding_dim}, got {vector.size}.",
                stacklevel=2,
            )
            return None

        norm = float(np.linalg.norm(vector))
        if norm <= 0.0:
            warnings.warn(
                f"Skipping dense vector for pattern '{self._patterns[index]['id']}': zero-norm embedding.",
                stacklevel=2,
            )
            return None
        return vector

    def _validate_query_vector(self, raw_vector: Any) -> np.ndarray | None:
        """Validate the query embedding against the corpus embedding dimension."""
        if raw_vector is None:
            return None
        try:
            vector = np.asarray(raw_vector, dtype=np.float32)
        except (TypeError, ValueError):
            return None
        if vector.ndim != 1 or vector.size == 0:
            return None
        if self._embedding_dim is not None and vector.size != self._embedding_dim:
            warnings.warn(
                f"Query embedding dimension mismatch: expected {self._embedding_dim}, got {vector.size}. Dense retrieval disabled for this request.",
                stacklevel=2,
            )
            return None
        return vector

    def _lazy_recompute_vector(self, index: int) -> np.ndarray | None:
        """Hook for future on-demand vector repair."""
        del index
        return None

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        """Normalize text consistently for filtering and keying."""
        if not value:
            return ""
        return re.sub(r"\s+", " ", value.strip().lower())

    @classmethod
    def _tokenize(cls, value: str | None) -> list[str]:
        """Tokenize text with lightweight normalization."""
        normalized = cls._normalize_text(value)
        if not normalized:
            return []
        return re.findall(r"[a-z0-9_]+", normalized)

    @classmethod
    def _normalize_feedback_key(cls, value: str | None) -> str:
        """Use the shared tokenizer for stable feedback lookup keys."""
        return " ".join(cls._tokenize(value))
