"""
agents/modifier.py
 
Generates a structured modification plan (description + SQL statements)
based on the user's request, the current DB schema, and all clarification
answers collected so far.
"""
import json
import re
from langchain_core.messages import HumanMessage, SystemMessage
from ..config import get_llm
from ..state import GraphState
from ..utils.memory import format_history_for_prompt


def _repair_and_parse_json(raw: str) -> dict | None:
    """
    Try several strategies to parse JSON that the LLM may have mangled.

    Common failure modes:
    - Escaped quotes used as string delimiters: \\"...\\"
    - Literal newlines (\\n) inside JSON strings
    - Mixed quote escaping inside sql_statements array
    """
    # Strategy 1: direct parse — cheapest, try first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: replace \" used as string delimiters → "
    # and literal \n inside strings → space, then retry
    repaired = raw.replace('\\"', '"')
    # Collapse literal \n sequences inside JSON strings to a space
    repaired = re.sub(r'(?<=\w)\\n(?=\w| )', ' ', repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 3: extract fields manually via regex
    try:
        desc_m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
        description = desc_m.group(1) if desc_m else ""

        # sql_statements array — capture everything between the brackets
        sql_m = re.search(
            r'"sql_statements"\s*:\s*\[(.+?)\](?=\s*,\s*"warnings"|\s*})',
            raw, re.DOTALL
        )
        sqls: list[str] = []
        if sql_m:
            block = sql_m.group(1)
            # Split on boundaries between statements: either ," or ,\"
            parts = re.split(r',\s*(?:\\?"|\\")', block)
            for p in parts:
                p = p.strip().strip('\\').strip('"').strip()
                if p:
                    sqls.append(p.replace('\\n', '\n').replace("\\'", "'"))

        warn_m = re.search(r'"warnings"\s*:\s*\[([^\]]*)\]', raw, re.DOTALL)
        warnings: list[str] = []
        if warn_m:
            warnings = re.findall(r'"((?:[^"\\]|\\.)*)"', warn_m.group(1))

        if description or sqls:
            return {
                "description": description,
                "sql_statements": sqls,
                "warnings": warnings,
            }
    except Exception:
        pass

    return None



_SYSTEM_PROMPT = """You are an expert database engineer tasked with generating precise, safe SQLite modification plans.
 
You will receive:
1. The current database schema
2. The user's modification request
3. Clarification Q&A (if any)
4. Validator feedback (if this is a refinement iteration)
5. Modification history so far
 
Your output MUST be valid JSON with this exact structure:
{
  "description": "Plain-English summary of what will be changed and why",
  "sql_statements": [
    "SQL statement 1;",
    "SQL statement 2;"
  ],
  "warnings": ["Any important warnings about data loss, irreversibility, etc."]
}
 
CRITICAL JSON FORMATTING RULES:
- Every string value MUST be delimited with double-quote characters (")
- NEVER use backslash-escaped quotes (\\") as string delimiters inside the JSON
- Multi-line SQL must be written as a single line — replace any real newlines inside SQL strings with a space
- Do NOT use \\n inside JSON string values; keep each SQL statement on one line
- Each element of sql_statements must be a single, self-contained SQL string ending with a semicolon
 
SQL RULES:
- Generate ONLY valid SQLite SQL
- Order statements correctly (create tables before adding FK references, etc.)
- Use IF NOT EXISTS / IF EXISTS where appropriate to make statements idempotent
- Preserve all existing data unless the user explicitly asked to delete something
- For ALTER TABLE: SQLite only supports ADD COLUMN and RENAME – use CREATE TABLE + data copy pattern for other changes
- Include PRAGMA foreign_keys = ON; at the start if any FK changes are involved
- Each SQL string must end with a semicolon
- Do NOT include any explanation outside the JSON
- Do NOT include bare BEGIN / COMMIT / ROLLBACK statements — transaction control is handled externally

DEDUPLICATION RULES (DELETE duplicates, keep one row per group):
- To identify rows to REMOVE, always use this pattern:
    WHERE <pk_col> NOT IN (
      SELECT MIN(<pk_col>) FROM <table> GROUP BY <dup_col1>, <dup_col2>
    )
- NEVER put the primary key column inside GROUP BY when finding duplicates — 
  it is unique by definition so COUNT(*) will never exceed 1.
- NEVER combine GROUP BY with HAVING COUNT(*) > 1 AND <pk> NOT IN (...) 
  in the same subquery — these are mutually exclusive conditions.
- Apply the same NOT IN subquery consistently to UPDATE (nulling FK refs), 
  DELETE from child tables, and DELETE from the main table.

SELF-CHECK RULE:
Before writing your final JSON output, mentally trace through each subquery:
- "Will this subquery actually return the rows I intend?"
- "Is every column referenced in WHERE/HAVING present in GROUP BY or an aggregate?"
- "Would this accidentally match zero rows or all rows?"
If any answer is uncertain, rewrite the subquery using a simpler, more explicit pattern.

TABLE MIGRATION RULES (CREATE new + copy data + DROP old + RENAME):
- Before dropping a table, inspect the schema for any VIEWS that reference it.
  Drop every such view with DROP VIEW IF EXISTS <name>; BEFORE the DROP TABLE statement.
  After the RENAME, recreate every dropped view using its original SQL, updated to
  reference the new column names if they changed.
- Also check for other tables whose FOREIGN KEY references the table being dropped.
  Disable FK enforcement with PRAGMA foreign_keys = OFF; at the very start and
  re-enable with PRAGMA foreign_keys = ON; at the very end.
- Correct statement order for a table migration:
    1. PRAGMA foreign_keys = OFF;
    2. DROP VIEW IF EXISTS <dependent_view>;  (one per dependent view)
    3. CREATE TABLE <new_table> (...);
    4. INSERT INTO <new_table> SELECT ... FROM <old_table>;
    5. DROP TABLE <old_table>;
    6. ALTER TABLE <new_table> RENAME TO <old_table>;
    7. CREATE VIEW <dependent_view> AS ...;  (one per dropped view, updated SQL)
    8. PRAGMA foreign_keys = ON;"""

def run_modifier(state: GraphState) -> dict:
    """
    LangGraph node: generates or refines a SQL modification plan.
    """
    llm = get_llm(temperature=0.0)

    history_str = format_history_for_prompt(state.get("modification_history", []))
 
    answers_str = ""
    for qa in state.get("clarification_answers", []):
        answers_str += f"Q: {qa['q']}\nA: {qa['a']}\n"

    # Include validator feedback if this is a refinement pass
    feedback_str = ""
    val = state.get("validation_result")
    if val and not val.get("approved"):
        issues = "\n".join(f"- {i}" for i in val.get("issues", []))
        feedback_str = f"\nVALIDATOR FEEDBACK (fix these issues):\n{val.get('feedback','')}\nIssues:\n{issues}\n"     


    user_content = f"""DATABASE SCHEMA:
{state['db_schema']}
 
USER MODIFICATION REQUEST:
{state['user_request']}
 
CLARIFICATION Q&A:
{answers_str or "None."}
{feedback_str}
PREVIOUS MODIFICATIONS IN THIS SESSION:
{history_str}
"""    

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ] 

    response = llm.invoke(messages)
    raw = response.content.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
 
    plan = _repair_and_parse_json(raw)
    if plan is None:
        return {
            "error": f"Modifier produced invalid JSON: {raw[:300]}",
            "next_action": "error",
        }
 
    return {
        "modification_plan": plan,
        "error": "",
        "next_action": "validate",
    }
