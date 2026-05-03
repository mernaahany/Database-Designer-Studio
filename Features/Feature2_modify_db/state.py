"""
state.py - Shared LangGraph state typed dict
"""

from typing import TypedDict, Annotated, List, Optional, Dict, Any
from langgraph.graph.message import add_messages



class ModificationRecord(TypedDict):
    """One accepted modification applied to the database."""
    timestamp: str
    user_request: str
    sql_statements: List[str]
    description: str



class GraphState(TypedDict):
    """
    Central state passed between all LangGraph nodes.
 
    Fields
    ------
    messages          : Full chat history (user + assistant turns).
    db_path           : Filesystem path of the working SQLite database.
    db_schema         : Human-readable schema dump of the current DB.
    user_request      : The latest user modification request (raw text).
    clarification_needed : True when the modifier needs more info.
    clarification_question : The question to ask the user next.
    clarification_answers  : Accumulated Q&A pairs from clarification turns.
    modification_plan : Dict produced by the modifier agent.
                        Keys: 'description', 'sql_statements', 'warnings'
    validation_result : Dict produced by the validator agent.
                        Keys: 'approved' (bool), 'issues' (list), 'feedback'
    validation_iterations : Counter for modifier ↔ validator loop.
    human_approved    : None = waiting, True = approved, False = rejected/edit.
    human_feedback    : Optional text from user when they request changes.
    modification_history : List[ModificationRecord] – the session audit log.
    error             : Any error message to surface to the UI.
    next_action       : Internal routing hint for the graph.
    """

    messages               : Annotated[list, add_messages]
    db_path                : str
    db_schema              : str
    user_request           : str
    clarification_needed   : bool
    clarification_question : str
    clarification_answers  : List[Dict[str, str]]   # [{"q": ..., "a": ...}]
    modification_plan      : Optional[Dict[str, Any]]
    validation_result      : Optional[Dict[str, Any]]
    validation_iterations  : int
    human_approved         : Optional[bool]
    human_feedback         : str
    modification_history   : List[ModificationRecord]
    error                  : str
    next_action            : str   # "clarify" | "modify" | "validate" | "human_review" | "execute" | "done" | "error"

