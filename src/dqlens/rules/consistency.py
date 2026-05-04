"""Consistency rules — cross-table and referential integrity."""

from __future__ import annotations

from typing import Any

from dqlens import connector
from dqlens.models import CheckResult, Finding, FindingCategory, Severity
from dqlens.rules.base import Dimension, Rule, RuleContext


class ForeignKeyIntegrityRule(Rule):
    """Check foreign key integrity — find orphaned rows."""

    name = "fk_integrity"
    dimension = Dimension.CONSISTENCY
    scope = "table"  # Operates on table-level FK metadata

    def applies_to(self, ctx: RuleContext) -> bool:
        return len(ctx.table.foreign_keys) > 0

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        # Generate one check per FK — caller iterates
        checks = []
        for fk in ctx.table.foreign_keys:
            checks.append({
                "check": "fk_integrity",
                "column": fk.source_column,
                "references": f"{ctx.table.schema_name}.{fk.target_table}.{fk.target_column}",
                "expect": "no_orphans",
                "reason": (
                    f"Foreign key: {fk.source_column} → "
                    f"{fk.target_table}.{fk.target_column}. "
                    f"Every non-null value should exist in the target table."
                ),
            })
        return checks  # type: ignore[return-value]

    def evaluate(self, ctx: RuleContext) -> list[Finding | CheckResult]:
        """Evaluate all FKs for this table. Returns a list."""
        results: list[Finding | CheckResult] = []
        if ctx.conn is None:
            return results

        for fk in ctx.table.foreign_keys:
            try:
                result = connector.check_fk_integrity(
                    ctx.conn,
                    ctx.table.schema_name,
                    fk.source_table,
                    fk.source_column,
                    fk.target_table,
                    fk.target_column,
                )
            except Exception:
                continue

            orphaned = result["orphaned"]
            non_null = result["non_null"]

            if orphaned > 0:
                orphan_pct = orphaned / non_null * 100 if non_null > 0 else 0
                results.append(Finding(
                    table=ctx.table.full_name,
                    column=fk.source_column,
                    severity=Severity.HIGH,
                    category=FindingCategory.FK_MISMATCH,
                    message=(
                        f"{orphaned:,} rows reference non-existent "
                        f"{fk.target_table}.{fk.target_column} (FK mismatch)"
                    ),
                    detail=(
                        f"Flagged because: {orphaned:,} rows ({orphan_pct:.1f}%) "
                        f"in {fk.source_column} reference values that don't exist "
                        f"in {fk.target_table}.{fk.target_column}."
                    ),
                    current_value=orphaned,
                ))
            else:
                match_pct = 100.0 if non_null > 0 else 0.0
                results.append(CheckResult(
                    table=ctx.table.full_name,
                    column=fk.source_column,
                    test_name="fk_integrity",
                    passed=True,
                    message=(
                        f"references {ctx.table.schema_name}.{fk.target_table}"
                        f".{fk.target_column} ({match_pct:.0f}% match)"
                    ),
                ))

        return results


class SchemaDriftRule(Rule):
    """Detect schema changes between profiles: columns added, removed, or type changed."""

    name = "schema_drift"
    dimension = Dimension.CONSISTENCY
    scope = "table"

    def applies_to(self, ctx: RuleContext) -> bool:
        return ctx.baseline_table is not None and len(ctx.table.columns) > 0

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "schema_drift",
            "expect": "schema_stable",
            "column_count": len(ctx.table.columns),
            "reason": (
                f"Table has {len(ctx.table.columns)} columns. "
                f"Flag if columns are added, removed, or change type between profiles."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> list[Finding | CheckResult]:
        results: list[Finding | CheckResult] = []
        if ctx.baseline_table is None:
            return results

        current_cols = {c.name: c for c in ctx.table.columns}
        baseline_cols = {c.name: c for c in ctx.baseline_table.columns}

        current_names = set(current_cols.keys())
        baseline_names = set(baseline_cols.keys())

        added = current_names - baseline_names
        removed = baseline_names - current_names

        for col_name in added:
            results.append(Finding(
                table=ctx.table.full_name,
                column=col_name,
                severity=Severity.LOW,
                category=FindingCategory.SCHEMA_CHANGE,
                message=f"Column added since last profile (type: {current_cols[col_name].data_type})",
                detail=(
                    f"Flagged because: column '{col_name}' exists now but was not "
                    f"present in the previous profile. This may indicate a schema "
                    f"migration or upstream change."
                ),
            ))

        for col_name in removed:
            results.append(Finding(
                table=ctx.table.full_name,
                column=col_name,
                severity=Severity.HIGH,
                category=FindingCategory.SCHEMA_CHANGE,
                message=f"Column removed since last profile (was type: {baseline_cols[col_name].data_type})",
                detail=(
                    f"Flagged because: column '{col_name}' was present in the "
                    f"previous profile but is now missing. This may break "
                    f"downstream queries or dashboards."
                ),
            ))

        # Check type changes for columns that still exist
        for col_name in current_names & baseline_names:
            cur_type = current_cols[col_name].data_type
            bl_type = baseline_cols[col_name].data_type
            if cur_type != bl_type:
                results.append(Finding(
                    table=ctx.table.full_name,
                    column=col_name,
                    severity=Severity.HIGH,
                    category=FindingCategory.TYPE_MISMATCH,
                    message=f"Column type changed: {bl_type} -> {cur_type}",
                    detail=(
                        f"Flagged because: column '{col_name}' changed type from "
                        f"'{bl_type}' to '{cur_type}' since last profile. "
                        f"This may break downstream transformations."
                    ),
                    current_value=cur_type,
                    baseline_value=bl_type,
                ))

        if not results:
            results.append(CheckResult(
                table=ctx.table.full_name,
                column=None,
                test_name="schema_drift",
                passed=True,
                message=f"schema stable ({len(current_cols)} columns, no changes)",
            ))

        return results
