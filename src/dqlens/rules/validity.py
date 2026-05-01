"""Validity rules — format, pattern, range, and semantic conformance."""

from __future__ import annotations

from typing import Any

from dqlens.models import CheckResult, Finding, FindingCategory, Severity
from dqlens.rules.base import Dimension, Rule, RuleContext


class PatternMatchRule(Rule):
    """Verify text columns match detected patterns (email, UUID, etc.)."""

    name = "pattern"
    dimension = Dimension.VALIDITY
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.column is not None
            and ctx.column.detected_pattern is not None
            and ctx.column.pattern_match_pct is not None
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        threshold = round(max(ctx.column.pattern_match_pct - 5, 50), 1)
        return {
            "check": "pattern",
            "column": ctx.column.name,
            "pattern": ctx.column.detected_pattern,
            "expect": "match_above",
            "value": threshold,
            "unit": "percent",
            "current_match": ctx.column.pattern_match_pct,
            "reason": (
                f"Detected {ctx.column.detected_pattern} pattern with "
                f"{ctx.column.pattern_match_pct}% match rate. "
                f"Threshold set 5pp below current rate."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or not ctx.column.detected_pattern:
            return None

        pct = ctx.column.pattern_match_pct
        if pct is None:
            return None

        if pct >= 95:
            return CheckResult(
                table=ctx.table.full_name,
                column=ctx.column.name,
                test_name=f"pattern_{ctx.column.detected_pattern}",
                passed=True,
                message=f"matches {ctx.column.detected_pattern} pattern ({pct}%)",
            )

        violation_pct = 100 - pct
        severity = Severity.LOW if violation_pct <= 20 else Severity.MEDIUM
        return Finding(
            table=ctx.table.full_name,
            column=ctx.column.name,
            severity=severity,
            category=FindingCategory.PATTERN_VIOLATION,
            message=(
                f"{violation_pct:.1f}% of values don't match "
                f"{ctx.column.detected_pattern} pattern"
            ),
            detail=(
                f"Flagged because: column appears to contain "
                f"{ctx.column.detected_pattern} values but {violation_pct:.1f}% "
                f"don't match the expected pattern."
            ),
            current_value=pct,
        )


class PatternDriftRule(Rule):
    """Detect drops in pattern match rate compared to baseline."""

    name = "pattern_drift"
    dimension = Dimension.VALIDITY
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return False  # Runtime-only drift check

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {}

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.baseline_column is None:
            return None
        if (
            ctx.column.detected_pattern
            and ctx.baseline_column.detected_pattern == ctx.column.detected_pattern
            and ctx.baseline_column.pattern_match_pct
            and ctx.column.pattern_match_pct
        ):
            drop = ctx.baseline_column.pattern_match_pct - ctx.column.pattern_match_pct
            if drop > 3:
                return Finding(
                    table=ctx.table.full_name,
                    column=ctx.column.name,
                    severity=Severity.MEDIUM,
                    category=FindingCategory.PATTERN_VIOLATION,
                    message=(
                        f"{ctx.column.detected_pattern} pattern match dropped "
                        f"{drop:.1f}pp ({ctx.baseline_column.pattern_match_pct}% "
                        f"→ {ctx.column.pattern_match_pct}%)"
                    ),
                    detail=(
                        f"Flagged because: the percentage of values matching the "
                        f"{ctx.column.detected_pattern} pattern decreased since last profile."
                    ),
                    current_value=ctx.column.pattern_match_pct,
                    baseline_value=ctx.baseline_column.pattern_match_pct,
                )
        return None


class PositiveValuesRule(Rule):
    """Flag negative values in columns whose names suggest positive-only."""

    name = "positive_values"
    dimension = Dimension.VALIDITY
    scope = "column"

    POSITIVE_INDICATORS = {
        "price", "amount", "cost", "total", "quantity",
        "count", "size", "age", "weight", "revenue",
        "salary", "fee", "balance", "rate", "score",
    }

    def applies_to(self, ctx: RuleContext) -> bool:
        if ctx.column is None or ctx.column.min_value is None:
            return False
        col_lower = ctx.column.name.lower()
        return any(ind in col_lower for ind in self.POSITIVE_INDICATORS)

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "positive_values",
            "column": ctx.column.name,
            "expect": "min_above",
            "value": 0,
            "current_min": ctx.column.min_value,
            "current_max": ctx.column.max_value,
            "reason": (
                f"Column name '{ctx.column.name}' suggests positive values. "
                f"Current range: [{ctx.column.min_value}, {ctx.column.max_value}]."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.min_value is None:
            return None

        try:
            min_val = float(ctx.column.min_value)
        except (TypeError, ValueError):
            return None

        if min_val < 0:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.MEDIUM,
                category=FindingCategory.DISTRIBUTION_SHIFT,
                message=(
                    f"Contains negative values (min: {ctx.column.min_value}) "
                    f"but column name suggests positive-only"
                ),
                detail=(
                    f"Flagged because: column name '{ctx.column.name}' suggests "
                    f"values should be positive, but minimum value is {ctx.column.min_value}."
                ),
                current_value=ctx.column.min_value,
            )

        return CheckResult(
            table=ctx.table.full_name,
            column=ctx.column.name,
            test_name="positive_values",
            passed=True,
            message=f"always positive (min: {ctx.column.min_value}, max: {ctx.column.max_value})",
        )


class AllowedValuesRule(Rule):
    """Flag columns with low cardinality that gain unexpected new values.

    If a column has few distinct values (like status, category, type),
    it likely represents an enum. New values appearing may be errors.
    """

    name = "allowed_values"
    dimension = Dimension.VALIDITY
    scope = "column"

    MAX_CARDINALITY = 20  # Only apply to low-cardinality columns

    def applies_to(self, ctx: RuleContext) -> bool:
        if ctx.column is None or ctx.column.row_count == 0:
            return False
        return (
            0 < ctx.column.distinct_count <= self.MAX_CARDINALITY
            and not ctx.column.is_primary_key
            and not ctx.column.is_unique
            and ctx.column.distinct_pct < 5  # Low cardinality relative to rows
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "allowed_values",
            "column": ctx.column.name,
            "expect": "cardinality_stable",
            "baseline_distinct": ctx.column.distinct_count,
            "reason": (
                f"Column has only {ctx.column.distinct_count} distinct values "
                f"across {ctx.column.row_count:,} rows — likely an enum/category. "
                f"New values appearing may indicate data quality issues."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.baseline_column is None:
            return None
        if ctx.baseline_column.distinct_count == 0:
            return None

        new_values = ctx.column.distinct_count - ctx.baseline_column.distinct_count
        if new_values > 0 and ctx.baseline_column.distinct_count <= self.MAX_CARDINALITY:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.LOW,
                category=FindingCategory.DISTRIBUTION_SHIFT,
                message=(
                    f"{new_values} new distinct value(s) appeared "
                    f"({ctx.baseline_column.distinct_count} → {ctx.column.distinct_count})"
                ),
                detail=(
                    f"Flagged because: this low-cardinality column (likely an enum) "
                    f"gained new values since last profile."
                ),
                current_value=ctx.column.distinct_count,
                baseline_value=ctx.baseline_column.distinct_count,
            )
        return None


class ValueRangeDriftRule(Rule):
    """Detect significant expansion of numeric value ranges."""

    name = "value_range_drift"
    dimension = Dimension.VALIDITY
    scope = "column"

    def applies_to(self, ctx: RuleContext) -> bool:
        return False  # Runtime-only drift check

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {}

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.baseline_column is None:
            return None
        if ctx.column.min_value is None or ctx.baseline_column.min_value is None:
            return None

        try:
            bl_min = float(ctx.baseline_column.min_value)
            bl_max = float(ctx.baseline_column.max_value)
            cur_max = float(ctx.column.max_value)
        except (TypeError, ValueError):
            return None

        bl_range = bl_max - bl_min
        if bl_range > 0 and cur_max > bl_max:
            expansion = (cur_max - bl_max) / bl_range * 100
            if expansion > 50:
                return Finding(
                    table=ctx.table.full_name,
                    column=ctx.column.name,
                    severity=Severity.LOW,
                    category=FindingCategory.DISTRIBUTION_SHIFT,
                    message=f"Max value expanded significantly ({bl_max} → {cur_max})",
                    detail=(
                        f"Flagged because: maximum value increased by "
                        f"{expansion:.0f}% of the previous range."
                    ),
                    current_value=cur_max,
                    baseline_value=bl_max,
                )
        return None


class SemanticColumnRule(Rule):
    """Infer checks from column names — status should be constrained,
    percentage should be 0-100, etc.
    """

    name = "semantic_column"
    dimension = Dimension.VALIDITY
    scope = "column"

    # Column name patterns → expected constraints
    SEMANTIC_RULES: dict[str, dict[str, Any]] = {
        "percentage": {"min": 0, "max": 100, "label": "percentage (0-100)"},
        "pct": {"min": 0, "max": 100, "label": "percentage (0-100)"},
        "percent": {"min": 0, "max": 100, "label": "percentage (0-100)"},
        "ratio": {"min": 0, "max": 1, "label": "ratio (0-1)"},
        "latitude": {"min": -90, "max": 90, "label": "latitude (-90 to 90)"},
        "lat": {"min": -90, "max": 90, "label": "latitude (-90 to 90)"},
        "longitude": {"min": -180, "max": 180, "label": "longitude (-180 to 180)"},
        "lng": {"min": -180, "max": 180, "label": "longitude (-180 to 180)"},
        "lon": {"min": -180, "max": 180, "label": "longitude (-180 to 180)"},
        "rating": {"min": 0, "max": 5, "label": "rating (0-5)"},
        "stars": {"min": 0, "max": 5, "label": "star rating (0-5)"},
        "port": {"min": 1, "max": 65535, "label": "port number (1-65535)"},
    }

    def _matching_rule(self, col_name: str) -> dict[str, Any] | None:
        col_lower = col_name.lower()
        for keyword, rule in self.SEMANTIC_RULES.items():
            if keyword in col_lower:
                return rule
        return None

    def applies_to(self, ctx: RuleContext) -> bool:
        if ctx.column is None or ctx.column.min_value is None:
            return False
        return self._matching_rule(ctx.column.name) is not None

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        rule = self._matching_rule(ctx.column.name)
        return {
            "check": "value_range",
            "column": ctx.column.name,
            "expect": "between",
            "min": rule["min"],
            "max": rule["max"],
            "current_min": ctx.column.min_value,
            "current_max": ctx.column.max_value,
            "reason": (
                f"Column name '{ctx.column.name}' suggests {rule['label']}. "
                f"Current range: [{ctx.column.min_value}, {ctx.column.max_value}]."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if ctx.column is None or ctx.column.min_value is None:
            return None

        rule = self._matching_rule(ctx.column.name)
        if rule is None:
            return None

        try:
            cur_min = float(ctx.column.min_value)
            cur_max = float(ctx.column.max_value)
        except (TypeError, ValueError):
            return None

        violations = []
        if cur_min < rule["min"]:
            violations.append(f"min {cur_min} < expected {rule['min']}")
        if cur_max > rule["max"]:
            violations.append(f"max {cur_max} > expected {rule['max']}")

        if violations:
            return Finding(
                table=ctx.table.full_name,
                column=ctx.column.name,
                severity=Severity.MEDIUM,
                category=FindingCategory.DISTRIBUTION_SHIFT,
                message=(
                    f"Values outside expected {rule['label']} range: "
                    f"{', '.join(violations)}"
                ),
                detail=(
                    f"Flagged because: column name '{ctx.column.name}' suggests "
                    f"{rule['label']} but values fall outside that range."
                ),
                current_value=f"[{cur_min}, {cur_max}]",
            )

        return CheckResult(
            table=ctx.table.full_name,
            column=ctx.column.name,
            test_name="value_range",
            passed=True,
            message=f"within expected {rule['label']} range",
        )
