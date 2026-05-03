"""
sql_utils.py — SQL execution safety utilities.

"""
import re
_SAFETY_CAP = 500


def enforce_safety_cap(sql: str, cap: int = _SAFETY_CAP) -> str:
    """
    Appends LIMIT only when the SQL has NO limit at all.
    Never modifies a query that already contains a LIMIT clause.

    This is a last-resort guard, not a formatting default.
    The LLM is responsible for choosing the semantically correct limit.
    """
    if not sql:
        return sql
    # Case-insensitive check — covers LIMIT, Limit, limit
    if re.search(r"\bLIMIT\b", sql, flags=re.IGNORECASE):
        return sql
    return sql.rstrip().rstrip(";") + f" LIMIT {cap}"