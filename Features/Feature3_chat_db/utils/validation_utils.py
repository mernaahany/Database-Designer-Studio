"""
utils/validation_utils.py — SQL validation and dialect helper functions.

Provides utilities for:
- Error type extraction and hashing
- SQL dialect normalization
"""

from __future__ import annotations


def _validation_error_types(errors: list[str]) -> list[str]:
    """Extract error type prefixes from a list of validation error messages."""
    return list(dict.fromkeys(e.split(":", 1)[0] for e in errors))


def _validation_error_signature(errors: list[str]) -> str:
    """Generate a sorted, pipe-delimited signature of error messages."""
    return "|".join(sorted(errors))


def _hash_text(value: str) -> str:
    """Generate a SHA1 hash of text for signature comparison."""
    import hashlib
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _sqlglot_dialect(dialect: str) -> str:
    """Normalize runtime dialect names to sqlglot dialect identifiers."""
    normalized = (dialect or "").strip().lower()
    return {
        "postgresql": "postgres",
        "postgres": "postgres",
        "sqlite3": "sqlite",
        "mariadb": "mysql",
    }.get(normalized, normalized or "postgres")
