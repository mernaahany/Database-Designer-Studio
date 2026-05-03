# orchestrator/router.py
from shared.workspace import Workspace, WorkspaceState

def route(workspace: Workspace, user_intent: str) -> str:
    """Returns which feature to dispatch to."""
    intent = user_intent.lower()
    
    if workspace.state == WorkspaceState.EMPTY:
        if "create" in intent:
            return "feature_1"
        elif "upload" in intent or "existing" in intent:
            return "upload_handler"

    if workspace.state in [WorkspaceState.SCHEMA_CREATED, WorkspaceState.DB_READY, WorkspaceState.MODIFIED]:
        if "modify" in intent or "edit" in intent or "change" in intent:
            return "feature_2"
        if "chat" in intent or "query" in intent or "ask" in intent:
            return "feature_3"
    
    # Fallback: let LLM classify intent
    return classify_intent_with_llm(workspace, user_intent)