"""
graph.py - LangGraph workflow for the DB modification pipeline
 
Flow:
  START
    │
    ▼
  [clarifier] ──needs_more──► PAUSE (UI asks user)
    │ enough_info
    ▼
  [modifier]
    │
    ▼
  [validator] ──issues, iterations < MAX──► [modifier]  (refine loop)
    │ approved OR max iterations reached
    ▼
  PAUSE for human review (UI shows plan, waits for approval)
    │
    ├── approved ──► [executor] ──► END (done)
    │
    └── rejected/edit ──► [modifier]  (incorporate human feedback)
"""
from langgraph.graph import StateGraph, START, END
from .state import GraphState
from .agents.clarifier import run_clarifier
from .agents.modifier import run_modifier
from .agents.validator import run_validator
from .agents.executor import run_executor




# Node wrappers 
 
def clarifier_node(state: GraphState) -> dict:
    return run_clarifier(state)
 
def modifier_node(state: GraphState) -> dict:
    return run_modifier(state)
 
def validator_node(state: GraphState) -> dict:
    return run_validator(state)
 
def executor_node(state: GraphState) -> dict:
    return run_executor(state)




# Conditional routing functions 

def route_after_clarifier(state: GraphState) -> str:
    return state.get("next_action", "modify")   # "clarify" or "modify"
 
def route_after_validator(state: GraphState) -> str:
    return state.get("next_action", "human_review")   # "modify" or "human_review"
 
def route_after_human(state: GraphState) -> str:
    """Called after the UI injects human_approved back into state."""
    approved = state.get("human_approved")
    if approved is True:
        return "execute"
    else:
        # User wants edits: go back to modifier with human_feedback
        return "modify"
    



# Graph construction 

def build_graph():
    builder = StateGraph(GraphState)

    # Register nodes
    builder.add_node("clarifier",    clarifier_node)
    builder.add_node("modifier",     modifier_node)
    builder.add_node("validator",    validator_node)
    builder.add_node("human_review", lambda s: s)   # passthrough – UI handles this
    builder.add_node("executor",     executor_node)

    # Entry
    builder.add_edge(START, "clarifier")


    # Clarifier → next
    builder.add_conditional_edges(
        "clarifier",
        route_after_clarifier,
        {"clarify": END, "modify": "modifier"}   # "clarify" exits so UI can ask
    )


    # Modifier → Validator
    builder.add_edge("modifier", "validator")


    # Validator → refine loop or human review
    builder.add_conditional_edges(
        "validator",
        route_after_validator,
        {"modify": "modifier", "human_review": "human_review"}
    )


    # Human review → executor or back to modifier
    builder.add_conditional_edges(
        "human_review",
        route_after_human,
        {"execute": "executor", "modify": "modifier"}
    )
 

    # Executor → done
    builder.add_edge("executor", END)
 
    return builder.compile()




# Singleton 
_graph = None
 
def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
