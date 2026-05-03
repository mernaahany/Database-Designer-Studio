"""
Feature 1 — Create DB
__init__.py — Orchestrates the end-to-end flow for the "Create Database Schema" feature.


Entry point:  run_feature_1(workspace) -> Workspace

Phases:
  run_feature_1_pre    → analyze + suggest (wait for approval)
  modify_feature_1_plan → user edits plan
  run_feature_1_post   → schema + validation + DB + report
"""

from __future__ import annotations

from shared.workspace import Workspace, WorkspaceState

from .agents import ( run_requirement_analyzer, run_suggestion_agent, run_schema_designer,run_validation_agent,run_query_generator,run_plan_modifier,)

from .utils import (create_sqlite_database, generate_final_report,build_erd_html_from_schema,)

from .validators import production_validation


# Helper

def _last_user_message(workspace: Workspace) -> str:
    for entry in reversed(workspace.history):
        if entry.get("role") == "user":
            return entry.get("content", "")
    return workspace.user_input or ""


# Phase 1 — Pre approval

def run_feature_1_pre(workspace: Workspace) -> Workspace:
    prompt = _last_user_message(workspace)
    workspace.user_input = prompt

    requirement_analysis = run_requirement_analyzer(prompt)
    suggestion_plan = run_suggestion_agent(requirement_analysis)

    # Store flat (NO feature_data)
    workspace.requirement_analysis = requirement_analysis.model_dump()
    workspace.suggestion_plan = suggestion_plan.model_dump()

    workspace.approval_status = "awaiting"
    workspace.state = WorkspaceState.SCHEMA_CREATED

    return workspace


# Modify suggestion plan

def modify_feature_1_plan(workspace: Workspace) -> Workspace:
    from .models import SuggestionPlan, RequirementAnalysis

    instruction = _last_user_message(workspace)

    suggestion_plan = SuggestionPlan.model_validate(
        workspace.suggestion_plan
    )

    requirement_analysis = RequirementAnalysis.model_validate(
        workspace.requirement_analysis
    )

    updated_plan = run_plan_modifier(
        suggestion_plan,
        instruction,
        requirement_analysis,
    )

    workspace.suggestion_plan = updated_plan.model_dump()
    workspace.approval_status = "awaiting"

    return workspace


# Phase 2 — Post approval

def run_feature_1_post(workspace: Workspace) -> Workspace:
    from .models import SuggestionPlan, RequirementAnalysis

    suggestion_plan = SuggestionPlan.model_validate(
        workspace.suggestion_plan
    )

    requirement_analysis = RequirementAnalysis.model_validate(
        workspace.requirement_analysis
    )

    prompt = workspace.user_input or "db_design"

    #  Schema design 
    schema = run_schema_designer(suggestion_plan)

    #  Fast rule-based validation 
    schema, _ = production_validation(suggestion_plan, schema)

    #  LLM validation 
    domain = requirement_analysis.domain or "unknown"

    validation_result = run_validation_agent(schema, domain=domain)

    if validation_result and validation_result.corrected_schema:
        schema = validation_result.corrected_schema

    #  Query generation 
    query_set = run_query_generator(schema)

    #  DB creation 
    db_local_path, sql_schema = create_sqlite_database(schema, prompt)

    #  ERD generation 
    erd_html = build_erd_html_from_schema(schema)

    #  Final report 
    report = generate_final_report(
        session_id=workspace.workspace_id,
        user_input=prompt,
        requirement_analysis=requirement_analysis,
        suggestion_plan=suggestion_plan,
        schema=schema,
        validation_result=validation_result,
        query_set=query_set,
    )

    #  Write flat workspace outputs 
    workspace.schema_json = schema.model_dump()
    workspace.schema_ddl = sql_schema
    workspace.db_local_path = db_local_path
    workspace.erd_html = erd_html

    workspace.final_report = report
    workspace.validation_result = validation_result.model_dump()
    workspace.query_set = query_set.model_dump()

    workspace.state = WorkspaceState.DB_READY
    workspace.approval_status = "done"

    return workspace


# Main entry

def run_feature_1(workspace: Workspace) -> Workspace:
    status = workspace.approval_status

    if status is None:
        return run_feature_1_pre(workspace)

    if status == "modifying":
        return modify_feature_1_plan(workspace)

    if status == "approved":
        return run_feature_1_post(workspace)

    return workspace