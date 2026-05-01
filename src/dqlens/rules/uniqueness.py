"""Uniqueness rules — duplicate detection."""

from __future__ import annotations

from typing import Any

from dqlens.models import CheckResult, Finding, FindingCategory, Severity
from dqlens.rules.base import Dimension, Rule, RuleContext


class UniqueColumnRule(Rule):
    """Verify columns marked as unique/PK remain unique."""

    name = "unique"
    dimension = Dimension.UNIQUENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.row_count > 0
            and (ctx.column.is_unique or ctx.column.is_primary_key)
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        label = "primary key" if ctx.column.is_primary_key else "unique"
        return {
            "check": "unique",
            "column": ctx.column.name,
            "reason": (
                f"Column is {label} ({ctx.column.distinct_count:,} distinct "
                f"values across {ctx.column.row_count:,} rows)."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.row_count == 0:
            return None
        if not (ctx.column.is_unique or ctx.column.is_primary_key):
            return None

        if ctx.column.distinct_count == ctx.column.non_null_count:
            suffix = ", not null" if ctx.column.null_count == 0 else ""
            return CheckResult(
                table=ctx.table.full_name,
                column=ctx.column.name,
                test_name="unique",
                passed=True,
                message=f"unique{suffix}",
            )

        dup_count = ctx.column.non_null_count - ctx.column.distinct_count
        label = "primary key" if ctx.column.is_primary_key else "unique"
        return Finding(
            table=ctx.table.full_name,
            column=ctx.column.name,
            severity=Severity.HIGH,
            category=FindingCategory.UNIQUENESS_VIOLATION,
            message=f"Expected unique but has {dup_count:,} duplicate values",
            detail=(
                f"Flagged because: column is marked as {label} "
                f"but has {ctx.column.distinct_count:,} distinct values "
                f"across {ctx.column.non_null_count:,} non-null rows."
            ),
            current_value=ctx.column.distinct_count,
        )


class UniquenessLostRule(Rule):
    """Detect columns that were unique in baseline but no longer are."""

    name = "uniqueness_lost"
    dimension = Dimension.UNIQUENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        # Only applies during drift comparison
        return False  # Not generated in tests.yaml — evaluated at runtime

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {}  # Not generated

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.baseline_column is None:
            return None
        if ctx.baseline_column.is_unique and not ctx.column.is_unique:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.HIGH,
                category=FindingCategory.UNIQUENESS_VIOLATION,
                message="Was unique in baseline, no longer unique",
                detail=(
                    "Flagged because: this column was fully unique in the "
                    "previous profile but now contains duplicate values. "
                    "This may indicate a data quality regression."
                ),
                current_value=ctx.column.distinct_count,
                baseline_value=ctx.baseline_column.distinct_count,
            )
        return None
