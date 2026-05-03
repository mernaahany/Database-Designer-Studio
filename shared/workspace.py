from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class WorkspaceState(str, Enum):
    ENTRY          = "entry" 
    EMPTY          = "empty"
    SCHEMA_CREATED = "schema_created"
    DB_READY       = "db_ready"
    MODIFIED       = "modified"
    QUERY_READY    = "query_ready"
        

class Workspace(BaseModel):

    #  Identity 
    workspace_id: str
    state: WorkspaceState = WorkspaceState.EMPTY

    # ── Shared outputs (all features read these) ───────────────
    schema_ddl:   Optional[str]  = None   # DDL string (SQL)
    schema_json:  Optional[dict] = None   # DatabaseSchema.model_dump()
    erd_blob_url: Optional[str]  = None   # Azure Blob URL for ERD image
    db_blob_url:  Optional[str]  = None   # Azure Blob URL for .db file
    db_conn_url:  Optional[str]  = None   # live connection string (Feature 3)
    artifacts:    list[str]      = Field(default_factory=list)  # report/diagram URLs
    entry_mode: Optional[str] = None

    # ── Feature 1 intermediates (flat, explicit) ───────────────
    requirement_analysis: Optional[dict] = None   # RequirementAnalysis.model_dump()
    suggestion_plan:      Optional[dict] = None   # SuggestionPlan.model_dump()
    validation_result:    Optional[dict] = None   # ValidationResult.model_dump()
    query_set:            Optional[dict] = None   # QuerySet.model_dump()
    final_report:         Optional[dict] = None   # generate_final_report() output

    # ── Feature 1 human-in-the-loop gate ──────────────────────

    approval_status: Optional[str] = None

    # ── Temp local paths (orchestrator uploads, then clears) ───
    db_local_path: Optional[str] = None   # local .db file path
    erd_html:      Optional[str] = None   # raw ERD HTML string

    # ── Session ────────────────────────────────────────────────
    user_input: Optional[str] = None      # raw first prompt (convenience)
    history:    list[dict]    = Field(default_factory=list)  # {"role": ..., "content": ...}
    db_blob_name:        Optional[str]  = None
    modification_history: list[dict]   = Field(default_factory=list)
    feature2_data:        dict          = Field(default_factory=dict)
    query_result: dict = Field(default_factory=dict)
    last_query_result: dict = Field(default_factory=dict)
    query_history: list[dict] = Field(default_factory=list)
    feature3_data: dict = Field(default_factory=dict)
    
Workspace.model_rebuild()
