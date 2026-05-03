"""
state.py — Shared state contract for the query agent pipeline

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, List


@dataclass
class ForeignKeyDef:
    """Normalized foreign key metadata for one constrained column."""

    column: str
    references_table: str
    references_column: str


@dataclass
class ColumnDef:
    """Production-facing column metadata used for schema compression and validation."""

    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str | None = None
    default: str | None = None
    unique: bool = False
    indexes: list[str] = field(default_factory=list)
    comment: str | None = None


@dataclass
class TableDef:
    """Normalized table metadata extracted from the runtime database."""

    name: str
    columns: list[ColumnDef]
    indexes: list[str] = field(default_factory=list)
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDef] = field(default_factory=list)
    comment: str | None = None


@dataclass
class SchemaSnapshot:
    """Schema snapshot cached and reused across retrieval and validation."""

    schema_id: str
    version: int
    tables: list[TableDef]
    dialect: str = "postgresql"
    source: str | None = None


@dataclass
class ProposedArtifact:
    type: Literal["NONE", "CREATE_VIEW", "SAVE_QUERY_TEMPLATE", "CACHE_RESULT"]
    reason: str = ""


@dataclass
class SQLGenerationResult:
    """Validated SQL query returned by the agent."""

    classification: str
    complexity: str
    strategy: str
    query: str
    confidence: float
    needs_clarification: bool
    proposed_artifact: ProposedArtifact
    
@dataclass
class ChatHistoryEntry:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class DBDesignerState: 
    """
    Centralized state contract for the Query Agent pipeline. All nodes read from
    and write to this state, which is initialized at the start of each agent turn
    and passed through the entire pipeline. This design allows for easy extension
    with new fields as needed without changing function signatures, and also
    provides a single source of truth for observability and debugging.
"""
    # Upstream request context
    user_query: str = ""
    tenant_id: str = ""
    session_id: str = ""
    intent: str = ""
    db_url: str = ""
    db_schema: str | None = None
    db_dialect: str = "unknown"  # Propagated from SchemaSnapshot and carried through the pipeline.

    # Schema context
    current_schema: SchemaSnapshot | None = None
    ddl_summary: str = ""
    schema_cache_key: str = ""
    schema_cache_hit: bool = False
    schema_load_time_ms: float = 0.0

    # Router and safety
    router_decision: str = ""
    router_reason: str = ""
    resolved_context: str = ""  
    safety_passed: bool = False
    relevance_passed: bool = False
    rejection_message: str = ""

    # Query-agent pipeline
    query_intent: str = ""
    query_complexity: str = ""
    selected_tables: list[str] = field(default_factory=list)
    selected_table_reason: str = ""
    selected_schema_summary: str = ""
    safety_violations: list[str] = field(default_factory=list)
    retrieved_fewshots: list[dict[str, Any]] = field(default_factory=list)
    decomposition_plan: list[dict[str, Any]] = field(default_factory=list)
    generated_sql_raw: str = ""
    validation_errors: list[str] = field(default_factory=list)
    error_severity: str = "NO_ERROR"
    validation_error_types: list[str] = field(default_factory=list)
    validation_error_signature: str = ""
    validation_error_type_signature: str = ""
    repeated_validation_count: int = 0
    correction_attempts: int = 0
    correction_loop_count: int = 0
    correction_total_time_ms: float = 0.0
    failed_generation_signatures: list[str] = field(default_factory=list)
    is_analytical: bool = False

    # Output and execution
    query_result: SQLGenerationResult | None = None
    needs_clarification: bool = False
    clarification_message: str = ""
    human_approval_required: bool = False
    approved_for_execution: bool | None = None
    execution_requested: bool = False
    execution_result: list[dict[str, Any]] = field(default_factory=list)
    execution_error: str = ""
    nl_response: str = ""


    # Observability
    latency_breakdown_ms: dict[str, float] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    chat_history: list[ChatHistoryEntry] = field(default_factory=list)
    
