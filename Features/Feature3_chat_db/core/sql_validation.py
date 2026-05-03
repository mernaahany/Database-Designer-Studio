"""
core/sql_validation.py — SQL validation for the Query Agent pipeline.

"""
 
from __future__ import annotations
 
import re
from dataclasses import dataclass, field
from typing import Any
 
import sqlglot
import sqlglot.expressions as exp
 
 
 
@dataclass
class ValidationResult:
    errors:   list[str] = field(default_factory=list)
    severity: str       = "NO_ERROR"   # NO_ERROR | SOFT_WARNING | HARD_ERROR
 
 
_PLACEHOLDER_RE = re.compile(
    r":[a-zA-Z_][a-zA-Z0-9_]*"        # SQLAlchemy :name
    r"|%\([a-zA-Z_][a-zA-Z0-9_]*\)s"  # psycopg2  %(name)s
)
 
_SQLGLOT_DIALECT: dict[str, str] = { #sqlglot for parsing, not execution — maps common dialect names to sqlglot's expected dialect strings. defaults to "postgres" if unrecognized. ot means that if the schema snapshot says "mysql", we'll parse with sqlglot's "mysql" dialect, which should improve parsing accuracy for MySQL-specific syntax. If we get an unrecognized dialect, we'll default to "postgres" parsing, which is a reasonable general-purpose choice.
    "postgresql": "postgres",
    "postgres":   "postgres",
    "mysql":      "mysql",
    "mariadb":    "mysql",
    "sqlite":     "sqlite",
    "mssql":      "tsql",
    "tsql":       "tsql",
}
 
_HARD_PREFIXES = (
    "SQL_PARSE_ERROR",
    "DML_FORBIDDEN",
    "UNBOUND_PLACEHOLDER",
    "STRUCTURED_OUTPUT_ERROR",
    "HALLUCINATED_TABLE",
    "TABLES_USED_MISMATCH",
)
 
 
 
def classify_error_severity(errors: list[str]) -> str:
    """Derive overall severity from a list of error strings."""
    if not errors:
        return "NO_ERROR"
    if any(e.split(":", 1)[0] in _HARD_PREFIXES for e in errors):
        return "HARD_ERROR"
    return "SOFT_WARNING"
 
 
 
def _build_index(schema: Any) -> dict[str, Any]:
    """
    Case-insensitive lookup structures from SchemaSnapshot.
    Returns empty dicts when schema is None so callers need no guard.
    """
    tables:   dict[str, Any]      = {}
    pk_cols:  dict[str, set[str]] = {}
    fk_pairs: set[frozenset]      = set()
 
    if schema is None:
        return {"tables": tables, "pk_cols": pk_cols, "fk_pairs": fk_pairs}
 
    for tbl in schema.tables:
        tl = tbl.name.lower()
        tables[tl] = tbl
        pk_cols[tl] = {c.name.lower() for c in tbl.columns if c.primary_key}
        for fk in tbl.foreign_keys:
            fk_pairs.add(frozenset([
                (tl,                          fk.column.lower()),
                (fk.references_table.lower(), fk.references_column.lower()),
            ]))
 
    return {"tables": tables, "pk_cols": pk_cols, "fk_pairs": fk_pairs}
 
 
 
def _check_tables(parsed: exp.Expression, index: dict) -> list[str]:
    if not index["tables"]:
        return []
 
    # CTE names are valid table references — do not flag them. cte means common table expression, which is a temporary named result set defined within the execution of a single SQL statement. if the SQL query defines a CTE like "WITH recent_orders AS (SELECT * FROM orders WHERE created_at > '2024-01-01')", then "recent_orders" is a valid table reference within that query, even though it's not part of the schema snapshot. so we collect all CTE aliases and consider them valid tables for the purpose of this check.
    cte_names = {
        cte.alias.lower()
        for cte in parsed.find_all(exp.CTE)
        if cte.alias
    }
 
    errors = []
    for tbl in parsed.find_all(exp.Table):
        name = tbl.name
        if name and name.lower() not in index["tables"] and name.lower() not in cte_names:
            errors.append(
                f"HALLUCINATED_TABLE: '{name}' not found in schema. "
                f"Known: {sorted(index['tables'])}"
            )
    return errors
 
 
def _check_joins(parsed: exp.Expression, index: dict) -> list[str]:
    errors = []
    for join in parsed.find_all(exp.Join):
        on_clause = join.args.get("on")
 
        # USING / NATURAL / cross joins — accepted as-is
        if on_clause is None:
            if not join.args.get("using"):
                errors.append("MISSING_JOIN_CONDITION: JOIN without ON or USING clause.")
            continue
 
        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.left, eq.right
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
 
            lt = (left.table  or "").lower()
            rt = (right.table or "").lower()
            lc = left.name.lower()
            rc = right.name.lower()
 
            # Skip if no table qualifiers — can't validate, delegate to DB
            if not lt or not rt:
                continue
            # Skip if either table is unknown — already flagged by _check_tables
            if index["tables"] and (lt not in index["tables"] or rt not in index["tables"]):
                continue
 
            # 1. FK pair — either direction
            if frozenset([(lt, lc), (rt, rc)]) in index["fk_pairs"]:
                continue
 
            # 2. Same column name (natural key)
            if lc == rc:
                continue
 
            # 3. At least one side is a PK
            if lc in index["pk_cols"].get(lt, set()) or rc in index["pk_cols"].get(rt, set()):
                continue
 
            errors.append(
                f"INVALID_JOIN_CONDITION: could not validate "
                f"`{left.table}.{left.name} = {right.table}.{right.name}`. "
                "Ensure columns are FK-related, share the same name, or reference a PK."
            )
 
    return errors
 
 
 
def validate_joins(sql: str, schema: Any = None, dialect: str = "postgresql") -> list[str]:
    """Standalone join checker (backwards-compatible signature)."""
    sg = _SQLGLOT_DIALECT.get(dialect.lower(), "postgres")
    try:
        parsed = sqlglot.parse_one(sql, dialect=sg)
    except Exception:
        return []
    return _check_joins(parsed, _build_index(schema))
 
 
def validate_sql_query(
    sql:             str,
    declared_tables: list[str] | None = None,
    schema:          Any = None,
    dialect:         str = "postgresql",
) -> ValidationResult:
    """
    Full validation pipeline.  Returns ValidationResult(errors, severity).
 
    Severity rules:
      HARD_ERROR   — placeholder / parse / DML violation  (block execution)
      SOFT_WARNING — unknown table / bad join              (route to correction)
      NO_ERROR     — all checks passed
    """
    # 1. Placeholders
    placeholders = _PLACEHOLDER_RE.findall(sql)
    if placeholders:
        return ValidationResult(
            errors=[
                f"UNBOUND_PLACEHOLDER: SQL contains {placeholders}. "
                "Generate literal values, not :param or %(param)s placeholders."
            ],
            severity="HARD_ERROR",
        )
 
    # 2. Parse
    sg = _SQLGLOT_DIALECT.get(dialect.lower(), "postgres")
    try:
        parsed = sqlglot.parse_one(sql, dialect=sg)
    except Exception as exc:
        return ValidationResult(errors=[f"SQL_PARSE_ERROR: {exc}"], severity="HARD_ERROR")
 
    # 3. DML safety
    if not isinstance(parsed, exp.Select):
        kind = type(parsed).__name__.upper()
        return ValidationResult(
            errors=[f"DML_FORBIDDEN: only SELECT allowed; got {kind}."],
            severity="HARD_ERROR",
        )
 
    # 4-5. Soft checks
    index  = _build_index(schema)
    errors = _check_tables(parsed, index) + _check_joins(parsed, index)
 
    return ValidationResult(errors=errors, severity=classify_error_severity(errors))