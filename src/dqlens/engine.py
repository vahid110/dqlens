"""Rule engine runner — executes all registered rules against a database profile.

This replaces the old detector.py with the pluggable rule engine.
Each rule is a self-contained class that decides when to activate,
what to generate, and how to evaluate.
"""

from __future__ import annotations

import psycopg2.extensions

from dqlens.models import (CheckResult, DatabaseProfile, Finding,
                           FindingCategory, RunResult, Severity, TableResult)
from dqlens.rules.base import RuleContext
from dqlens.rules.registry import get_column_rules, get_table_rules


def run_checks(
    current: DatabaseProfile,
    baseline: DatabaseProfile | None = None,
    conn: psycopg2.extensions.connection | None = None,
    ignores: set[str] | None = None,
) -> RunResult:
    """Run all registered rules against the current profile.

    This is the main entry point — replaces detector.detect_problems().

    Args:
        current: The current database profile.
        baseline: The previous profile for drift comparison (None on first run).
        conn: Live database connection for FK integrity checks.
        ignores: Set of ignore keys to suppress.

    Returns:
        RunResult with findings and passed tests per table.
    """
    if ignores is None:
        ignores = set()

    table_rules = get_table_rules()
    column_rules = get_column_rules()

    table_results = []
    for table in current.tables:
        baseline_table = None
        if baseline:
            baseline_table = baseline.get_table(table.table_name)

        findings: list[Finding] = []
        passed: list[CheckResult] = []

        # Run table-scoped rules
        for rule in table_rules:
            ctx = RuleContext(
                table=table,
                baseline_table=baseline_table,
                conn=conn,
            )
            if not rule.applies_to(ctx):
                # Some rules always evaluate even if applies_to is False
                # (e.g., drift-only rules). Try evaluate anyway.
                pass
            _run_rule(rule, ctx, findings, passed)

        # Run column-scoped rules
        for col in table.columns:
            baseline_col = None
            if baseline_table:
                baseline_col = baseline_table.get_column(col.name)

            for rule in column_rules:
                ctx = RuleContext(
                    table=table,
                    column=col,
                    baseline_table=baseline_table,
                    baseline_column=baseline_col,
                    conn=conn,
                )
                _run_rule(rule, ctx, findings, passed)

        # Filter out ignored findings
        filtered_findings = []
        for f in findings:
            ignore_key = _make_ignore_key(table.table_name, f)
            if ignore_key not in ignores:
                filtered_findings.append(f)

        # Sort findings: HIGH first, then MEDIUM, then LOW
        severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        filtered_findings.sort(key=lambda f: severity_order[f.severity])

        table_results.append(TableResult(
            table_name=table.full_name,
            findings=filtered_findings,
            passed_tests=passed,
        ))

    return RunResult(tables=table_results)


def _run_rule(
    rule,
    ctx: RuleContext,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Execute a single rule and collect results."""
    try:
        result = rule.evaluate(ctx)
    except Exception:
        # Rule evaluation failed — skip silently
        return

    if result is None:
        return

    # Some rules (like FK integrity) return a list
    if isinstance(result, list):
        for item in result:
            _collect_result(item, rule, findings, passed)
    else:
        _collect_result(result, rule, findings, passed)


def _collect_result(
    result: Finding | CheckResult,
    rule,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Add a result to the appropriate list, tagging with rule metadata."""
    if isinstance(result, Finding):
        result.dimension = rule.dimension.value
        result.rule_name = rule.name
        findings.append(result)
    elif isinstance(result, CheckResult):
        result.dimension = rule.dimension.value
        result.rule_name = rule.name
        passed.append(result)


def _make_ignore_key(table_name: str, finding: Finding) -> str:
    """Create an ignore key for a finding."""
    parts = [table_name]
    if finding.column:
        parts.append(finding.column)
    parts.append(finding.category.value)
    return ".".join(parts)
