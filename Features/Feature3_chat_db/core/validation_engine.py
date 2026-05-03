"""
core/validation_engine.py — Validation result processing and correction retry logic for the Query Agent pipeline.
"""

from __future__ import annotations

from typing import Any

from utils.validation_utils import (_validation_error_types, _validation_error_signature,_hash_text,)
from shared.config import settings
from state import DBDesignerState

# Validation result processing and correction retry logic for the Query Agent pipeline. this module contains helper functions that process the results of SQL validation checks and determine whether the agent should attempt a correction and retry cycle. The main function, _validation_result_updates(), takes the current state and the list of validation errors and computes the new state updates, including error signatures and counts. The _should_retry_correction() function uses the updated state to decide if the agent should try to correct the SQL and retry execution, based on factors like error severity, error types, repeated errors, and correction attempts.
def _validation_result_updates(
    state: DBDesignerState,
    errors: list[str],
    error_severity: str,
) -> dict[str, Any]:
    error_types = _validation_error_types(errors)
    signature = _validation_error_signature(errors)
    type_signature = "|".join(error_types)
    repeated_count = 0
    if errors:
        repeated_count = (
            state.repeated_validation_count + 1
            if type_signature == state.validation_error_type_signature
            else 1
        )

    failed_generation_signatures = list(state.failed_generation_signatures)
    sql_signature = _hash_text(state.generated_sql_raw)
    if errors and sql_signature not in failed_generation_signatures:
        failed_generation_signatures.append(sql_signature)

    return {
        "validation_errors": errors,
        "error_severity": error_severity,
        "validation_error_types": error_types,
        "validation_error_signature": signature,
        "validation_error_type_signature": type_signature,
        "repeated_validation_count": repeated_count,
        "failed_generation_signatures": failed_generation_signatures,
    }


def _should_retry_correction(state: DBDesignerState) -> bool:
    if state.error_severity != "HARD_ERROR":
        return False
    if not state.validation_errors:
        return False
    if state.correction_attempts >= min(settings.max_correction_attempts, 2):
        return False
    if state.repeated_validation_count >= 2:
        return False
    if not _contains_retryable_error(state.validation_error_types):
        return False
    if not _contains_schema_or_syntax_violation(state.validation_error_types):
        return False
    if _hash_text(state.generated_sql_raw) in state.failed_generation_signatures and state.correction_attempts > 0:
        return False
    return True


def _contains_retryable_error(error_types: list[str]) -> bool:
    retryable_types = {
        "SQL_PARSE_ERROR",
        "HALLUCINATED_TABLE",
        "TABLES_USED_MISMATCH",
        "UNKNOWN_COLUMN",
    }
    return any(et in retryable_types for et in error_types)


def _contains_schema_or_syntax_violation(error_types: list[str]) -> bool:
    schema_or_syntax_types = {
        "SQL_PARSE_ERROR",
        "HALLUCINATED_TABLE",
        "TABLES_USED_MISMATCH",
        "UNKNOWN_COLUMN",
    }
    return any(et in schema_or_syntax_types for et in error_types)
