"""
utils/memory.py - Session modification history helpers
"""
from datetime import datetime
from typing import List
from ..state import ModificationRecord




def create_modification_record(
    user_request: str,
    sql_statements: List[str],
    description: str
) -> ModificationRecord:
    """Build a ModificationRecord to append to history."""
    return ModificationRecord(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_request=user_request,
        sql_statements=sql_statements,
        description=description
    )


def format_history_for_prompt(history: List[ModificationRecord]) -> str:
    """
    Render modification history as a compact string for LLM context.
    """
    if not history:
        return "No modifications have been applied yet."

    lines = []

    for i, rec in enumerate(history, 1):

        # Case 1: dict (expected format)
        if isinstance(rec, dict):
            lines.append(
                f"[{i}] {rec.get('timestamp', 'N/A')} — {rec.get('user_request', 'N/A')}"
            )

        # Case 2: string (fallback)
        elif isinstance(rec, str):
            lines.append(f"[{i}] {rec}")

        # Case 3: any unexpected type
        else:
            lines.append(f"[{i}] {str(rec)}")

    return "\n".join(lines)

def format_history_for_display(history: List[ModificationRecord]) -> str:
    """Human-friendly text for display in the Streamlit sidebar."""
    if not history:
        return "No modifications yet."
    blocks = []
    for i, rec in enumerate(history, 1):
        sql_block = "\n".join(f"    {s}" for s in rec["sql_statements"])
        blocks.append(
            f"**#{i} — {rec['timestamp']}**\n"
            f"*Request:* {rec['user_request']}\n"
            f"*Changes:* {rec['description']}\n"
            f"```sql\n{sql_block}\n```"
        )
    return "\n\n---\n\n".join(blocks)       
