"""Completeness rules — missing values, empty tables, row gaps."""

from __future__ import annotations

from typing import Any

from dqlens.models import CheckResult, Finding, FindingCategory, Severity
from dqlens.rules.base import Dimension, Rule, RuleContext


class EmptyTableRule(Rule):
    """Flag tables with zero rows."""

    name = "empty_table"
    dimension = Dimension.COMPLETENESS
    scope = "table"

    def applies_to(self, ctx: RuleContext) -> bool:
        return True  # Always check row count

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "row_count",
            "expect": "greater_than",
            "value": 0,
            "reason": f"Table had {ctx.table.row_count:,} rows at profile time.",
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.table.row_count == 0:
            return Finding(
                table=ctx.table.full_name,
                column=None,
                severity=Severity.HIGH,
                category=FindingCategory.ROW_COUNT_ANOMALY,
                message="Table is empty (0 rows)",
                detail=(
                    "An empty table in a live database is usually a problem — "
                    "missing data load, truncated table, or broken pipeline."
                ),
            )
        return CheckResult(
            table=ctx.table.full_name,
            column=None,
            test_name="row_count_positive",
            passed=True,
            message=f"row_count > 0 ({ctx.table.row_count:,} rows)",
        )


class RowCountDriftRule(Rule):
    """Flag significant changes in row count between profiles."""

    name = "row_count_drift"
    dimension = Dimension.COMPLETENESS
    scope = "table"

    def applies_to(self, ctx: RuleContext) -> bool:
        return ctx.table.row_count > 0

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "row_count_drift",
            "expect": "change_within",
            "value": 30,
            "unit": "percent",
            "reason": "Flag if row count changes more than 30% between profiles.",
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if not ctx.baseline_table or ctx.baseline_table.row_count == 0:
            return None

        baseline = ctx.baseline_table.row_count
        current = ctx.table.row_count
        change_pct = (current - baseline) / baseline * 100

        if abs(change_pct) > 30:
            direction = "grew" if change_pct > 0 else "shrank"
            return Finding(
                table=ctx.table.full_name,
                column=None,
                severity=Severity.MEDIUM,
                category=FindingCategory.ROW_COUNT_ANOMALY,
                message=(
                    f"Row count {direction} {abs(change_pct):.0f}% "
                    f"({baseline:,} → {current:,})"
                ),
                detail=(
                    f"Flagged because: row count changed by {change_pct:+.1f}% "
                    f"since last profile. Large changes may indicate duplicate "
                    f"ingestion, data loss, or pipeline issues."
                ),
                current_value=current,
                baseline_value=baseline,
            )
        elif change_pct < -5:
            return Finding(
                table=ctx.table.full_name,
                column=None,
                severity=Severity.LOW,
                category=FindingCategory.ROW_COUNT_ANOMALY,
                message=(
                    f"Row count decreased {abs(change_pct):.1f}% "
                    f"({baseline:,} → {current:,})"
                ),
                detail="Flagged because: tables usually grow. A decrease may indicate data deletion or filtering changes.",
                current_value=current,
                baseline_value=baseline,
            )
        return None


class NotNullRule(Rule):
    """Flag columns that were fully non-null but now have nulls."""

    name = "not_null"
    dimension = Dimension.COMPLETENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.row_count > 0
            and ctx.column.null_count == 0
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "not_null",
            "column": ctx.column.name,
            "reason": f"Column was 100% non-null at profile time ({ctx.column.row_count:,} rows).",
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.row_count == 0:
            return None

        if ctx.column.null_count == 0:
            return CheckResult(
                table=ctx.table.full_name,
                column=ctx.column.name,
                test_name="not_null",
                passed=True,
                message="not null",
            )
        return None  # Has nulls — handled by NullRateRule or AllNullRule


class NullRateDriftRule(Rule):
    """Flag significant increases in null rate compared to baseline."""

    name = "null_rate_drift"
    dimension = Dimension.COMPLETENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.row_count > 0
            and 0 < ctx.column.null_count < ctx.column.row_count
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        threshold = round(min(ctx.column.null_pct * 2, 100), 1)
        return {
            "check": "null_rate",
            "column": ctx.column.name,
            "expect": "below",
            "value": threshold,
            "unit": "percent",
            "baseline": ctx.column.null_pct,
            "reason": (
                f"Column had {ctx.column.null_pct}% nulls "
                f"({ctx.column.null_count:,} of {ctx.column.row_count:,} rows). "
                f"Threshold set at 2x baseline. Adjust if this rate is expected."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.baseline_column is None:
            return None
        if ctx.column.row_count == 0 or ctx.baseline_column.row_count == 0:
            return None

        current_pct = ctx.column.null_pct
        baseline_pct = ctx.baseline_column.null_pct
        increase = current_pct - baseline_pct

        if increase > 1.0 and current_pct > 0:
            multiplier = (
                current_pct / baseline_pct if baseline_pct > 0 else float("inf")
            )
            new_nulls = ctx.column.null_count - ctx.baseline_column.null_count

            severity = Severity.MEDIUM
            if multiplier > 10 or increase > 10:
                severity = Severity.HIGH

            detail_parts = [
                f"Flagged because: null rate increased from {baseline_pct}% to {current_pct}%"
            ]
            if multiplier != float("inf"):
                detail_parts.append(f"({multiplier:.0f}x increase)")
            if new_nulls > 0:
                detail_parts.append(
                    f"— {new_nulls:,} new null values since last profile"
                )

            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=severity,
                category=FindingCategory.NULL_ANOMALY,
                message=f"{current_pct}% null (was {baseline_pct}% in baseline)",
                detail=" ".join(detail_parts),
                current_value=current_pct,
                baseline_value=baseline_pct,
            )
        return None


class AllNullRule(Rule):
    """Flag columns that are 100% null."""

    name = "all_null"
    dimension = Dimension.COMPLETENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.row_count > 0
            and ctx.column.null_count == ctx.column.row_count
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "all_null",
            "column": ctx.column.name,
            "severity": "medium",
            "reason": "Column is 100% null. Flag as potential dead column.",
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.row_count == 0:
            return None
        if ctx.column.null_count == ctx.column.row_count:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.MEDIUM,
                category=FindingCategory.NULL_ANOMALY,
                message="100% null — column is entirely empty",
                detail=(
                    "Flagged because: every value in this column is NULL. "
                    "This may indicate a broken data source or unused column."
                ),
                current_value=100.0,
            )
        return None


class PrimaryKeyNullRule(Rule):
    """Flag primary key columns that contain nulls."""

    name = "pk_not_null"
    dimension = Dimension.COMPLETENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.is_primary_key
            and ctx.column.null_count > 0
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "pk_not_null",
            "column": ctx.column.name,
            "reason": "Primary key column should never contain NULL values.",
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None:
            return None
        if ctx.column.is_primary_key and ctx.column.null_count > 0:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.HIGH,
                category=FindingCategory.NULL_ANOMALY,
                message=(
                    f"Primary key has {ctx.column.null_count:,} null values "
                    f"({ctx.column.null_pct}%)"
                ),
                detail="Flagged because: primary key columns should never contain NULL values.",
                current_value=ctx.column.null_pct,
            )
        return None


class EmptyStringRule(Rule):
    """Flag text columns with high empty string rates.

    Columns full of '' look "not null" but are effectively missing data.
    """

    name = "empty_string_rate"
    dimension = Dimension.COMPLETENESS
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.empty_string_count > 0
            and ctx.column.row_count > 0
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "empty_string_rate",
            "column": ctx.column.name,
            "expect": "below",
            "value": 30,
            "unit": "percent",
            "current": ctx.column.empty_string_pct,
            "reason": (
                f"Column has {ctx.column.empty_string_pct}% empty strings "
                f"({ctx.column.empty_string_count:,} of "
                f"{ctx.column.non_null_count:,} non-null values). "
                f"Empty strings look non-null but carry no information."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.empty_string_count == 0:
            return None

        pct = ctx.column.empty_string_pct

        if pct >= 60:
            severity = Severity.HIGH
        elif pct >= 30:
            severity = Severity.MEDIUM
        else:
            return CheckResult(
                table=ctx.table.full_name,
                column=ctx.column.name,
                test_name="empty_string_rate",
                passed=True,
                message=f"empty string rate {pct}% (below threshold)",
            )

        return Finding(
            table=ctx.table.full_name,
            column=ctx.column.name,
            severity=severity,
            category=FindingCategory.NULL_ANOMALY,
            message=(
                f"{pct}% empty strings "
                f"({ctx.column.empty_string_count:,} values)"
            ),
            detail=(
                f"Flagged because: {pct}% of non-null values are empty strings. "
                f"These pass not-null checks but carry no information."
            ),
            current_value=pct,
        )
