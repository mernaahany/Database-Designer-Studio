"""
core/identifier_normalizer.py — Rewrite LLM-generated SQL so every table
and column reference matches the exact name stored in the database.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
import sqlglot.expressions as exp

if TYPE_CHECKING:
    from state import SchemaSnapshot


#  Dialect map 
_SQLGLOT_DIALECT: dict[str, str] = {
    "postgresql": "postgres",
    "postgres":   "postgres",
    "mysql":      "mysql",
    "mariadb":    "mysql",
    "sqlite":     "sqlite",
    "mssql":      "tsql",
    "tsql":       "tsql",
}


def _build_lookup(snapshot: "SchemaSnapshot") -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """
    table_map : {lower(stored_name): stored_name}
    col_map   : {lower(stored_name): {lower(col_name): col_name}}
    """
    table_map: dict[str, str] = {}
    col_map:   dict[str, dict[str, str]] = {}
    for table in snapshot.tables:
        table_map[table.name.lower()] = table.name
        col_map[table.name.lower()] = {
            col.name.lower(): col.name for col in table.columns
        }
    return table_map, col_map


def normalize_identifiers(sql: str, snapshot: "SchemaSnapshot") -> str:
    """
    Rewrite *sql* so every table/column identifier matches exact DB casing
    and is correctly quoted.  When a table is aliased, column qualifiers
    use the alias — never the original name — so PostgreSQL accepts the SQL.
    """
    if not sql or not sql.strip():
        return sql

    dialect      = snapshot.dialect.lower()
    sg_dialect   = _SQLGLOT_DIALECT.get(dialect, "postgres")
    table_map, col_map = _build_lookup(snapshot)

    try:
        statements = sqlglot.parse(sql, read=sg_dialect)
    except Exception:
        return sql

    rewritten: list[str] = []

    for statement in statements:
        if statement is None:
            continue

        # ── Step 1: rewrite table nodes; build two alias maps 

        alias_map:      dict[str, str] = {}
        table_to_alias: dict[str, str] = {}   # stored_lower → alias string

        for node in statement.find_all(exp.Table):
            raw_name = node.name or ""
            stored   = table_map.get(raw_name.lower())
            if not stored:
                continue

            alias = node.alias or ""

            # Rewrite table identifier to quoted stored name
            node.set("this", exp.Identifier(this=stored, quoted=True))

            if alias:
                alias_lower = alias.lower()
                alias_map[alias_lower]             = stored
                table_to_alias[stored.lower()]     = alias   # remember alias for Step 2
                # Keep alias node unchanged (no re-quoting needed for aliases)
                node.set("alias", exp.TableAlias(
                    this=exp.Identifier(this=alias)
                ))

        # ── Step 2: rewrite column table-qualifiers ───────────────────────────

        for node in statement.find_all(exp.Column):
            col_name_raw  = node.name or ""
            table_ref_raw = (node.table or "").lower()

            # Resolve which stored table this column belongs to
            stored_table: str | None = None
            if table_ref_raw:
                stored_table = alias_map.get(table_ref_raw) or table_map.get(table_ref_raw)
            else:
                # No explicit qualifier — search all tables
                for tname_lower, cols in col_map.items():
                    if col_name_raw.lower() in cols:
                        stored_table = table_map[tname_lower]
                        break

            # Rewrite column name to exact stored casing
            if stored_table:
                stored_col = col_map.get(stored_table.lower(), {}).get(col_name_raw.lower())
                if stored_col:
                    node.set("this", exp.Identifier(this=stored_col, quoted=True))

            # Rewrite column table qualifier
            if stored_table and node.table:
                alias_for_table = table_to_alias.get(stored_table.lower())
                if alias_for_table:
                    # Table was aliased — PostgreSQL requires the alias here
                    node.set("table", exp.Identifier(this=alias_for_table))
                else:
                    # No alias — use quoted stored name
                    node.set("table", exp.Identifier(this=stored_table, quoted=True))

        #  Step 3: generate SQL 
        try:
            rewritten.append(statement.sql(dialect=sg_dialect, pretty=False))
        except Exception:
            rewritten.append(sql)

    return ";\n".join(rewritten) if rewritten else sql


def normalize_sql_for_snapshot(sql: str, snapshot: "SchemaSnapshot | None") -> str:
    """Safe wrapper — returns *sql* unchanged when snapshot is None."""
    if snapshot is None:
        return sql
    try:
        return normalize_identifiers(sql, snapshot)
    except Exception:
        return sql