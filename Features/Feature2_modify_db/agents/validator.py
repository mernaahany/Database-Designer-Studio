"""
agents/validator.py
 
Validates the modification plan produced by the modifier agent.
Checks for SQL correctness, schema consistency, safety, and completeness.
"""
import json
from langchain_core.messages import HumanMessage, SystemMessage
from ..config import get_llm, MAX_VALIDATION_ITERATIONS
from ..state import GraphState
from ..utils.db_utils import validate_sql_syntax




_SYSTEM_PROMPT = """You are a senior database architect performing a critical review of a proposed database modification plan.
 
You will receive:
1. The current database schema
2. The user's original modification request
3. The proposed modification plan (description + SQL statements)
4. Syntax validation result from a live dry-run
 
Your task: determine if the plan is APPROVED or needs REVISION.

Check for:
- Correctness: Does the SQL actually implement what the user asked?
- Completeness: Are all parts of the request covered?
- Safety: Could this accidentally destroy data not intended to be deleted?
- Compatibility: Is the SQL valid SQLite syntax and schema-consistent?
- Foreign key integrity: Are FK references to existing columns?
- Edge cases: Empty tables, NULL values, cascades, etc.

Respond ONLY with valid JSON:
{
  "approved": true | false,
  "issues": ["issue1", "issue2"],
  "feedback": "Detailed instructions for the modifier to fix the plan (empty if approved)",
  "confidence": "high | medium | low"
}"""


def run_validator(state: GraphState) -> dict:
    """
    LangGraph node: validates the modification plan.
    Returns updated state with validation_result and routing.
    """
    iterations = state.get("validation_iterations", 0) + 1
    plan = state.get("modification_plan", {})

    if not plan:
        return {
            "validation_result": {
                "approved": False,
                "issues": ["No modification plan was produced."],
                "feedback": "The modifier returned an empty plan.",
                "confidence": "low",
            },
            "validation_iterations": iterations,
            "next_action": "error",
        }
    
    # Syntax dry-run 
    sql_statements = plan.get("sql_statements", [])
    syntax_ok, syntax_err = validate_sql_syntax(state["db_path"], sql_statements)
    syntax_note = "Syntax dry-run PASSED." if syntax_ok else f"Syntax dry-run FAILED: {syntax_err}"   


    # LLM semantic review 
    llm = get_llm(temperature=0.0)
 
    sql_block = "\n".join(sql_statements)
    user_content = f"""DATABASE SCHEMA:
{state['db_schema']}
 
USER REQUEST:
{state['user_request']}
 
PROPOSED PLAN:
Description: {plan.get('description', '')}
Warnings: {plan.get('warnings', [])}
 
SQL STATEMENTS:
{sql_block}
 
SYNTAX CHECK RESULT:
{syntax_note}
"""
    

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]
 
    response = llm.invoke(messages)
    raw = response.content.strip()
 
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()


    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "approved": False,
            "issues": ["Validator produced invalid JSON."],
            "feedback": f"Raw output: {raw[:200]}",
            "confidence": "low",
        }
 
    # Also force fail if syntax dry-run failed
    if not syntax_ok:
        result["approved"] = False
        result["issues"] = [f"Syntax error: {syntax_err}"] + result.get("issues", [])
        result["feedback"] = f"Fix SQL syntax errors. {result.get('feedback', '')}"


    # Routing decision 
    if result.get("approved"):
        next_action = "human_review"
    elif iterations >= MAX_VALIDATION_ITERATIONS:
        # Exhausted retries — still send to human review with a warning
        result["issues"].append(
            f"Maximum validation iterations ({MAX_VALIDATION_ITERATIONS}) reached. "
            "Proceeding to human review with outstanding concerns."
        )
        next_action = "human_review"
    else:
        next_action = "modify"   # send back to modifier for refinement
 
    return {
        "validation_result": result,
        "validation_iterations": iterations,
        "next_action": next_action,
    }
                                                                     
