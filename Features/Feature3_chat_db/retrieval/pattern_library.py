"""
retrieval/pattern_library.py — Global few-shot pattern library.

A schema-agnostic few-shot corpus for SQL generation. Examples capture reusable
SQL reasoning patterns and intentionally use placeholder table names so they
can transfer across schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_ALLOWED_INTENTS = {"aggregation", "join", "filter", "analytical"}
_ALLOWED_COMPLEXITIES = {"simple", "medium", "complex"}
_ALLOWED_DIALECTS = {"generic", "postgresql", "sqlite", "mysql", "sqlserver"}
_DEFAULT_DIALECT = "generic"


@dataclass(frozen=True, slots=True)
class FewShotExample:
    """Canonical few-shot example shape shared across retrieval and prompting."""

    id: str
    question: str
    sql: str
    intent: str
    complexity: str
    pattern_tags: list[str]
    num_tables: int
    dialect: str = _DEFAULT_DIALECT
    explanation: str = ""

    def __post_init__(self) -> None:
        normalized_id = self.id.strip()
        normalized_question = self.question.strip()
        normalized_sql = self.sql.strip()
        normalized_intent = self.intent.strip().lower()
        normalized_complexity = self.complexity.strip().lower()
        normalized_dialect = self.dialect.strip().lower() or _DEFAULT_DIALECT
        normalized_explanation = self.explanation.strip()
        normalized_tags = [str(tag).strip().lower() for tag in self.pattern_tags if str(tag).strip()]

        if not normalized_id:
            raise ValueError("FewShotExample.id must be non-empty.")
        if not normalized_question:
            raise ValueError(f"FewShotExample '{normalized_id}' must have a non-empty question.")
        if not normalized_sql:
            raise ValueError(f"FewShotExample '{normalized_id}' must have non-empty sql.")
        if normalized_intent not in _ALLOWED_INTENTS:
            raise ValueError(
                f"FewShotExample '{normalized_id}' has invalid intent '{self.intent}'."
            )
        if normalized_complexity not in _ALLOWED_COMPLEXITIES:
            raise ValueError(
                f"FewShotExample '{normalized_id}' has invalid complexity '{self.complexity}'."
            )
        if normalized_dialect not in _ALLOWED_DIALECTS:
            raise ValueError(
                f"FewShotExample '{normalized_id}' has invalid dialect '{self.dialect}'. "
                f"Must be one of {_ALLOWED_DIALECTS}."
            )
        if not normalized_tags:
            raise ValueError(
                f"FewShotExample '{normalized_id}' must include at least one pattern tag."
            )
        if self.num_tables < 1:
            raise ValueError(
                f"FewShotExample '{normalized_id}' must have num_tables >= 1."
            )

        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "question", normalized_question)
        object.__setattr__(self, "sql", normalized_sql)
        object.__setattr__(self, "intent", normalized_intent)
        object.__setattr__(self, "complexity", normalized_complexity)
        object.__setattr__(self, "pattern_tags", normalized_tags)
        object.__setattr__(self, "dialect", normalized_dialect)
        object.__setattr__(self, "explanation", normalized_explanation)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical retriever/prompt-compatible record."""
        return {
            "id": self.id,
            "question": self.question,
            "sql": self.sql,
            "intent": self.intent,
            "complexity": self.complexity,
            "pattern_tags": list(self.pattern_tags),
            "num_tables": self.num_tables,
            "dialect": self.dialect,
            "explanation": self.explanation,
        }


SEED_EXAMPLES: list[FewShotExample] = [
    FewShotExample(
        id="filter_001",
        question="Get all active records from an entity that were created in the last 30 days",
        sql="""
SELECT *
FROM {primary}
WHERE status = 'active'
  AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC;
""",
        intent="filter",
        complexity="simple",
        pattern_tags=["filter", "date_range", "status"],
        num_tables=1,
        dialect="postgresql",
        explanation="Date range filter + status filter on a single table. NOW() and INTERVAL syntax are PostgreSQL-specific.",
    ),
    FewShotExample(
        id="filter_002",
        question="Find records matching multiple optional criteria (nullable fields)",
        sql="""
SELECT *
FROM {primary}
WHERE (category = :category OR :category IS NULL)
  AND (region   = :region   OR :region IS NULL)
  AND deleted_at IS NULL
ORDER BY updated_at DESC;
""",
        intent="filter",
        complexity="simple",
        pattern_tags=["filter", "optional_params", "soft_delete"],
        num_tables=1,
        dialect="generic",
        explanation="Nullable parameter pattern: (col = :param OR :param IS NULL) handles optional filters without dynamic SQL. Portable across all major dialects.",
    ),
    FewShotExample(
        id="join_001",
        question="Get all records from one entity with their related parent entity details",
        sql="""
SELECT
    p.id          AS primary_id,
    p.name        AS primary_name,
    s.id          AS secondary_id,
    s.name        AS secondary_name,
    p.created_at
FROM {primary} p
JOIN {secondary} s ON p.secondary_id = s.id
WHERE p.deleted_at IS NULL
ORDER BY p.created_at DESC;
""",
        intent="join",
        complexity="simple",
        pattern_tags=["join", "inner_join", "foreign_key"],
        num_tables=2,
        dialect="generic",
        explanation="Standard FK join. Always alias tables. Always include soft-delete guard if schema has deleted_at. Portable across all SQL dialects.",
    ),
    FewShotExample(
        id="join_002",
        question="Count how many children each parent has, including parents with zero children",
        sql="""
SELECT
    p.id,
    p.name,
    COUNT(c.id) AS child_count
FROM {primary} p
LEFT JOIN {secondary} c ON c.primary_id = p.id AND c.deleted_at IS NULL
GROUP BY p.id, p.name
ORDER BY child_count DESC;
""",
        intent="join",
        complexity="medium",
        pattern_tags=["join", "left_join", "count", "group_by", "zero_safe"],
        num_tables=2,
        dialect="generic",
        explanation="LEFT JOIN + COUNT to include parents with zero children. Push filter into ON clause, not WHERE, to preserve left-side rows. Portable across all SQL dialects.",
    ),
    FewShotExample(
        id="join_003",
        question="Join three tables: an entity, its category, and its tags/labels",
        sql="""
SELECT
    e.id,
    e.name,
    c.name   AS category_name,
    STRING_AGG(t.label, ', ' ORDER BY t.label) AS tags
FROM {primary} e
JOIN {secondary} c ON e.category_id = c.id
LEFT JOIN {tertiary} t ON t.entity_id = e.id
GROUP BY e.id, e.name, c.name
ORDER BY e.name;
""",
        intent="join",
        complexity="medium",
        pattern_tags=["join", "three_tables", "string_agg", "group_by"],
        num_tables=3,
        dialect="postgresql",
        explanation="Three-table join with array aggregation. STRING_AGG is PostgreSQL-specific for collapsing one-to-many tags into a single column.",
    ),
    FewShotExample(
        id="agg_001",
        question="Calculate totals and averages grouped by a category, with minimum threshold",
        sql="""
SELECT
    category,
    COUNT(*)            AS record_count,
    SUM(amount)         AS total_amount,
    AVG(amount)         AS avg_amount,
    MIN(amount)         AS min_amount,
    MAX(amount)         AS max_amount
FROM {primary}
WHERE created_at >= DATE_TRUNC('month', NOW())
GROUP BY category
HAVING COUNT(*) >= 5
ORDER BY total_amount DESC;
""",
        intent="aggregation",
        complexity="medium",
        pattern_tags=["aggregation", "group_by", "having", "sum", "avg", "count"],
        num_tables=1,
        dialect="postgresql",
        explanation="Full aggregation pattern. Use HAVING (not WHERE) to filter after grouping. DATE_TRUNC and NOW() are PostgreSQL-specific.",
    ),
    FewShotExample(
        id="agg_002",
        question="Get top N entities by a metric, partitioned by category",
        sql="""
WITH ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY category
            ORDER BY amount DESC
        ) AS rank
    FROM {primary}
    WHERE deleted_at IS NULL
)
SELECT *
FROM ranked
WHERE rank <= 5;
""",
        intent="aggregation",
        complexity="complex",
        pattern_tags=["aggregation", "window_function", "row_number", "partition_by", "top_n"],
        num_tables=1,
        dialect="generic",
        explanation="Top-N per group pattern using ROW_NUMBER() window function. CTEs and window functions are standard SQL across dialects.",
    ),
    FewShotExample(
        id="analytical_001",
        question="Calculate month-over-month growth rate for a metric",
        sql="""
WITH monthly AS (
    SELECT
        DATE_TRUNC('month', created_at) AS month,
        SUM(amount)                     AS total
    FROM {primary}
    WHERE created_at >= NOW() - INTERVAL '12 months'
    GROUP BY 1
),
with_prev AS (
    SELECT
        month,
        total,
        LAG(total) OVER (ORDER BY month) AS prev_total
    FROM monthly
)
SELECT
    month,
    total,
    prev_total,
    ROUND(
        (total - prev_total) / NULLIF(prev_total, 0) * 100,
    2) AS growth_pct
FROM with_prev
ORDER BY month;
""",
        intent="analytical",
        complexity="complex",
        pattern_tags=["analytical", "window_function", "lag", "growth_rate", "cte", "time_series"],
        num_tables=1,
        dialect="postgresql",
        explanation="Month-over-month growth: DATE_TRUNC and INTERVAL syntax are PostgreSQL-specific. LAG window function patterns are portable.",
    ),
    FewShotExample(
        id="analytical_002",
        question="Calculate cumulative running total over time, partitioned by category",
        sql="""
SELECT
    category,
    created_at,
    amount,
    SUM(amount) OVER (
        PARTITION BY category
        ORDER BY created_at
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS running_total
FROM {primary}
ORDER BY category, created_at;
""",
        intent="analytical",
        complexity="complex",
        pattern_tags=["analytical", "window_function", "cumulative_sum", "running_total", "partition_by"],
        num_tables=1,
        dialect="generic",
        explanation="Running total per category using SUM window function with ROWS BETWEEN frame clause. Standard SQL window function syntax portable across dialects.",
    ),
    FewShotExample(
        id="analytical_003",
        question="Pivot: count records by status per month as separate columns",
        sql="""
SELECT
    DATE_TRUNC('month', created_at) AS month,
    COUNT(*) FILTER (WHERE status = 'active')   AS active_count,
    COUNT(*) FILTER (WHERE status = 'inactive') AS inactive_count,
    COUNT(*) FILTER (WHERE status = 'pending')  AS pending_count,
    COUNT(*)                                    AS total_count
FROM {primary}
GROUP BY 1
ORDER BY 1;
""",
        intent="analytical",
        complexity="medium",
        pattern_tags=["analytical", "pivot", "conditional_count", "filter_aggregate"],
        num_tables=1,
        dialect="postgresql",
        explanation="PostgreSQL-specific syntax: DATE_TRUNC for date truncation and COUNT(*) FILTER (WHERE ...) for conditional aggregation.",
    ),
    FewShotExample(
        id="analytical_004",
        question="Funnel analysis: count how many entities pass through each stage",
        sql="""
WITH stages AS (
    SELECT
        entity_id,
        MAX(CASE WHEN stage = 'visit'   THEN 1 ELSE 0 END) AS did_visit,
        MAX(CASE WHEN stage = 'signup'  THEN 1 ELSE 0 END) AS did_signup,
        MAX(CASE WHEN stage = 'convert' THEN 1 ELSE 0 END) AS did_convert
    FROM {primary}
    GROUP BY entity_id
)
SELECT
    SUM(did_visit)   AS visits,
    SUM(did_signup)  AS signups,
    SUM(did_convert) AS conversions,
    ROUND(SUM(did_signup)::numeric  / NULLIF(SUM(did_visit),0) * 100, 2)  AS visit_to_signup_pct,
    ROUND(SUM(did_convert)::numeric / NULLIF(SUM(did_signup),0) * 100, 2) AS signup_to_convert_pct
FROM stages;
""",
        intent="analytical",
        complexity="complex",
        pattern_tags=["analytical", "funnel", "cte", "conditional_aggregation", "conversion_rate"],
        num_tables=1,
        dialect="postgresql",
        explanation="PostgreSQL-specific type casting (::numeric) used for numeric conversion. Core funnel pattern (CASE flags) is portable.",
    ),
    FewShotExample(
        id="analytical_005",
        question="Cohort retention: which signup-month cohorts are still active N months later",
        sql="""
WITH cohorts AS (
    SELECT
        user_id,
        DATE_TRUNC('month', created_at) AS cohort_month
    FROM {primary}
),
activity AS (
    SELECT
        user_id,
        DATE_TRUNC('month', activity_date) AS active_month
    FROM {secondary}
)
SELECT
    c.cohort_month,
    COUNT(DISTINCT c.user_id)                          AS cohort_size,
    COUNT(DISTINCT CASE WHEN a.active_month = c.cohort_month + INTERVAL '1 month'
                        THEN a.user_id END)            AS retained_month_1,
    COUNT(DISTINCT CASE WHEN a.active_month = c.cohort_month + INTERVAL '3 months'
                        THEN a.user_id END)            AS retained_month_3
FROM cohorts c
LEFT JOIN activity a ON a.user_id = c.user_id
GROUP BY c.cohort_month
ORDER BY c.cohort_month;
""",
        intent="analytical",
        complexity="complex",
        pattern_tags=["analytical", "cohort", "retention", "cte", "left_join", "conditional_distinct_count"],
        num_tables=2,
        dialect="postgresql",
        explanation="PostgreSQL-specific: DATE_TRUNC and INTERVAL arithmetic syntax. Core cohort logic (CTEs and conditional counting) is portable.",
    ),
]


def get_seed_library() -> list[dict[str, Any]]:
    """Return canonical seed examples for the retriever and prompt builder."""
    return [example.to_dict() for example in SEED_EXAMPLES]
