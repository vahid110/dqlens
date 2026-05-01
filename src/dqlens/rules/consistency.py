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
