"""
agents/clarifier.py
 
Determines whether the user's modification request contains enough information
to proceed. If not, it generates a targeted clarifying question.
"""
import json 
from langchain_core.messages import HumanMessage, SystemMessage
from ..config import get_llm
from ..state import GraphState
from ..utils.memory import format_history_for_prompt




_SYSTEM_PROMPT = """You are a database design assistant helping users modify an existing SQLite database.
 
Your job is to assess whether a user's modification request contains ENOUGH information to generate
correct and complete SQL statements — and if not, ask ONE clear follow-up question.
 
You will be given:
1. The current database schema
2. The user's modification request
3. Any clarification Q&A already gathered
4. The history of modifications already applied
 
Rules:
- Only ask for missing information that is ACTUALLY required to write the SQL (e.g. column types, constraint names, target values, etc.)
- Do NOT ask for optional or cosmetic details
- If ONE question covers multiple gaps, combine them clearly
- If you have enough information, say so
 
Respond ONLY with valid JSON in this exact format:
{
  "needs_clarification": true | false,
  "question": "The follow-up question (empty string if not needed)",
  "reason": "Brief internal note on what is missing or why it is sufficient"
}"""


def run_clarifier(state: GraphState) -> dict:
    """
    LangGraph node: checks if the user request needs clarification.
    Returns state updates.
    """
    llm = get_llm(temperature=0.0)

    history_str = format_history_for_prompt(state.get("modification_history", []))
    answers_str = ""
    for qa in state.get("clarification_answers", []):
        answers_str += f"Q: {qa['q']}\nA: {qa['a']}\n"

    user_content = f"""DATABASE SCHEMA:
{state['db_schema']}
 
MODIFICATION REQUEST:
{state['user_request']}
 
CLARIFICATION Q&A SO FAR:
{answers_str or "None yet."}
 
MODIFICATION HISTORY:
{history_str}
"""   
    
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]


    response = llm.invoke(messages)
    raw = response.content.strip()


    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()


    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: assume enough info
        result = {"needs_clarification": False, "question": "", "reason": "Parse error – proceeding"}
 
    if result.get("needs_clarification"):
        return {
            "clarification_needed": True,
            "clarification_question": result.get("question", ""),
            "next_action": "clarify"
        }
    else:
        return {
            "clarification_needed": False,
            "clarification_question": "",
            "next_action": "modify"
        }
