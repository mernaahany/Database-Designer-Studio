"""
core/schema_builder.py — SchemaContextBuilder.
"""

from __future__ import annotations

from state import ColumnDef, SchemaSnapshot, TableDef

class SchemaContextBuilder:
    """
    Convert SchemaSnapshot objects into compact schema summaries.

    Results are cached by `(schema_id, version)` so repeated requests for the
    same database schema avoid recomputing the prompt-ready summary.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def build(self, schema: SchemaSnapshot) -> str:
        """Return a cached prompt-ready schema summary."""
        cache_key = self._cache_key(schema)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._build_summary(schema)
        return self._cache[cache_key]

    def build_for_tables(self, schema: SchemaSnapshot, table_names: list[str]) -> str:
        """Return a prompt-ready summary for a deterministic subset of tables."""
        if not table_names:
            return "-- No tables selected"

        requested = {name.strip().lower() for name in table_names if name and name.strip()}
        selected_tables = [table for table in self._sorted_tables(schema.tables) if table.name.lower() in requested]
        lines = [f"-- Dialect: {schema.dialect}"]
        if schema.source:
            lines.append(f"-- Source: {schema.source}")
        if not selected_tables:
            lines.append("-- No tables selected")
            return "\n".join(lines)
        for table in selected_tables:
            lines.append(self._table_to_ddl(table))
        return "\n".join(lines)

    def build_table_inventory(self, schema: SchemaSnapshot) -> str:
        """Return a compact inventory for table selection."""
        lines = [f"-- Dialect: {schema.dialect}", "-- Table inventory"]
        for table in self._sorted_tables(schema.tables):
            column_names = ", ".join(column.name for column in table.columns)
            lines.append(f"{table.name}: {column_names}")
        return "\n".join(lines)

    def invalidate(self, schema: SchemaSnapshot | None = None) -> None:
        """Clear either one schema summary or the entire in-memory cache."""
        if schema is None:
            self._cache.clear()
            return
        self._cache.pop(self._cache_key(schema), None)

    def table_names(self, schema: SchemaSnapshot) -> list[str]:
        """Return table names in deterministic order for retrieval hints."""
        return [table.name for table in self._sorted_tables(schema.tables)]

    def _cache_key(self, schema: SchemaSnapshot) -> str:
        return f"{schema.schema_id}:v{schema.version}"

    def _build_summary(self, schema: SchemaSnapshot) -> str:
        lines = [f"-- Dialect: {schema.dialect}"]
        if schema.source:
            lines.append(f"-- Source: {schema.source}")

        tables = self._sorted_tables(schema.tables)
        if not tables:
            lines.append("-- No tables available")
            return "\n".join(lines)

        for table in tables:
            lines.append(self._table_to_ddl(table))
        return "\n".join(lines)

    def _sorted_tables(self, tables: list[TableDef]) -> list[TableDef]:
        return sorted(tables, key=lambda table: table.name.lower())

    def _table_to_ddl(self, table: TableDef) -> str:
        """Render one table as a compact single-line DDL summary."""
        columns = ", ".join(self._column_to_str(column) for column in table.columns)
        parts = [f"{table.name}({columns})"]

        if table.primary_keys:
            parts.append(f"[PK: {', '.join(sorted(table.primary_keys))}]")
        if table.indexes:
            parts.append(f"[IDX: {', '.join(sorted(table.indexes))}]")
        if table.foreign_keys:
            fk_parts = [
                f"{fk.column}->{fk.references_table}.{fk.references_column}"
                for fk in sorted(
                    table.foreign_keys,
                    key=lambda foreign_key: (
                        foreign_key.column.lower(),
                        foreign_key.references_table.lower(),
                        foreign_key.references_column.lower(),
                    ),
                )
            ]
            parts.append(f"[FK: {', '.join(fk_parts)}]")
        if table.comment:
            parts.append(f"-- {table.comment}")

        return " ".join(parts)

    def _column_to_str(self, column: ColumnDef) -> str:
        """Encode one column using short SQL-friendly metadata markers."""
        parts = [column.name, column.type]

        if column.primary_key:
            parts.append("PK")
        if column.foreign_key:
            parts.append(f"FK->{column.foreign_key}")
        if not column.nullable and not column.primary_key:
            parts.append("NN")
        if column.unique and not column.primary_key:
            parts.append("UQ")
        if column.default is not None:
            parts.append(f"DEF:{column.default}")
        if column.indexes:
            parts.append(f"IDX:{'|'.join(sorted(column.indexes))}")
        if column.comment:
            parts.append(f"COMMENT:{column.comment}")

        return " ".join(parts)
    
class SchemaGraph:
    """
    Provides relationship + FK validation on top of SchemaSnapshot 
    to support SQL validation without hard joins metadata requirements. 
    The graph is built lazily on first use and cached for subsequent validations within the same agent turn.
    """

    def __init__(self, schema: SchemaSnapshot):
        self.schema = schema
        self._fk_map = self._build_fk_map()

    def _build_fk_map(self):
        fk_map = {}

        for table in self.schema.tables:
            for fk in table.foreign_keys:
                key = (table.name.lower(), fk.column.lower())
                value = (
                    fk.references_table.lower(),
                    fk.references_column.lower()
                )
                fk_map[key] = value

        return fk_map

    # Column-level FK validation (best effort, if metadata available)
    def has_fk(self, table1, col1, table2, col2):
        t1 = table1.lower()
        t2 = table2.lower()

        key = (t1, col1.lower())
        if key in self._fk_map:
            ref_table, ref_col = self._fk_map[key]
            return ref_table == t2 and ref_col == col2.lower()

        # check reverse direction
        key = (t2, col2.lower())
        if key in self._fk_map:
            ref_table, ref_col = self._fk_map[key]
            return ref_table == t1 and ref_col == col1.lower()

        return False

    #  Table-level fallback validation (if FK metadata is missing or incomplete)
    def has_relationship(self, table1, table2):
        t1 = table1.lower()
        t2 = table2.lower()

        for (src_table, _), (ref_table, _) in self._fk_map.items():
            if (src_table == t1 and ref_table == t2) or (
                src_table == t2 and ref_table == t1
            ):
                return True

        return False
