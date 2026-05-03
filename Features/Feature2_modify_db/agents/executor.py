"""
agents/executor.py
 
Applies the approved and human-confirmed SQL modification plan to the live
database via Azure Blob Storage — no local filesystem access.
 
All backup, execute, and restore logic is delegated to
utils/db_utils.execute_sql_statements(), which handles the full
cloud-native transaction:
  backup → download → execute in-memory → upload OR restore-from-backup.
"""
import shutil
from datetime import datetime
from ..state import GraphState, ModificationRecord
from ..utils.db_utils import execute_sql_statements, extract_schema
from ..utils.memory import create_modification_record


def run_executor(state: GraphState) -> dict:
    """
    LangGraph node: executes the approved modification plan.
 
    Steps:
    1. Backup the database.
    2. Execute all SQL statements in a single transaction.
    3. If successful → refresh schema, append to modification history.
    4. If failed    → restore from backup, return error.
    """
    plan      = state.get("modification_plan", {})
    sqls      = plan.get("sql_statements", [])
    blob_name = state["db_path"]   # ← blob name, not a local path

    # execute_sql_statements handles: backup → execute → upload (or restore)
    success, message, backup_blob = execute_sql_statements(blob_name, sqls)

    if not success:
        return {
            "error": message,
            "next_action": "error",
        }
    
    # Refresh schema from the freshly-uploaded blob
    try:
        new_schema = extract_schema(blob_name)
    except Exception:
        new_schema = state["db_schema"]    # Refresh schema from the freshly-uploaded blob


    # Append to session modification history
    record = create_modification_record(
        user_request=state.get("user_request", ""),
        sql_statements=sqls,
        description=plan.get("description", ""),
    )
    history = list(state.get("modification_history", []))
    history.append(record)


    return {
        "db_schema": new_schema,
        "modification_history": history,
        # Reset per-request transient state
        "modification_plan": None,
        "validation_result": None,
        "validation_iterations": 0,
        "clarification_needed": False,
        "clarification_question": "",
        "clarification_answers": [],
        "human_approved": None,
        "human_feedback": "",
        "error": "",
        "next_action": "done",
    }
