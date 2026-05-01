"""Output formatting — signal-first display.

Design principle: problems first, passing tests hidden by default.
Users should see "I didn't know this" findings, not 20 green checkmarks.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dqlens.models import Finding, RunResult, Severity, TableResult


console = Console()


def print_run_result(
    result: RunResult,
    verbose: bool = False,
    focus: str | None = None,
) -> None:
    """Print run results to the terminal.

    Args:
        result: The run results.
        verbose: If True, show all tests including passing ones.
        focus: Filter mode — 'problems', 'high', or None (default = problems).
    """
    if not result.tables:
        console.print("\n[yellow]No tables found to check.[/yellow]\n")
        return

    console.print()

    for table_result in result.tables:
        _print_table_result(table_result, verbose, focus)

    # Summary
    console.print()
    _print_summary(result)
    console.print()


def _print_table_result(
    table: TableResult,
    verbose: bool,
    focus: str | None,
) -> None:
    """Print results for a single table."""
    # Header
    if table.problem_count > 0:
        header = Text()
        header.append(f"{table.table_name}: ", style="bold")
        header.append(f"{table.total_tests} tests, ", style="dim")
        header.append(f"{table.passed_count} passed, ", style="green")
        header.append(
            f"{table.problem_count} PROBLEM{'S' if table.problem_count != 1 else ''} FOUND",
            style="bold red",
        )
        console.print(header)
    else:
        header = Text()
        header.append(f"{table.table_name}: ", style="bold")
        header.append(f"{table.total_tests} tests, ", style="dim")
        header.append(f"{table.passed_count} passed", style="green")
        console.print(header)

    # Findings (always shown)
    if table.findings:
        console.print()
        findings_to_show = table.findings
        if focus == "high":
            findings_to_show = [f for f in table.findings if f.severity == Severity.HIGH]

        if findings_to_show:
            console.print("  [bold red]PROBLEMS:[/bold red]")
            for finding in findings_to_show:
                _print_finding(finding)
            console.print()

    # Passed tests (only in verbose mode)
    if verbose and table.passed_tests:
        if table.findings:
            console.print("  [dim]Passed:[/dim]")
        for test in table.passed_tests:
            icon = "[green]✓[/green]"
            if test.column:
                console.print(f"  {icon} {test.column}: {test.message}")
            else:
                console.print(f"  {icon} {test.message}")
        console.print()
    elif table.passed_tests and table.findings:
        console.print(
            f"  [dim]✓ {table.passed_count} checks passed "
            f"(use --verbose to see all)[/dim]"
        )
        console.print()


def _print_finding(finding: Finding) -> None:
    """Print a single finding with severity color."""
    severity_styles = {
        Severity.HIGH: "bold red",
        Severity.MEDIUM: "yellow",
        Severity.LOW: "dim yellow",
    }
    style = severity_styles.get(finding.severity, "white")
    severity_label = finding.severity.value.ljust(6)

    text = Text()
    text.append(f"  {severity_label} ", style=style)
    if finding.column:
        text.append(f"{finding.column}: ", style="bold")
    text.append(finding.message)

    console.print(text)


def _print_summary(result: RunResult) -> None:
    """Print the run summary."""
    table_count = len(result.tables)
    total = result.total_tests
    passed = result.total_passed
    problems = result.total_findings

    summary = Text()
    summary.append("Summary: ", style="bold")
    summary.append(f"{table_count} table{'s' if table_count != 1 else ''}, ")
    summary.append(f"{total} tests, ")
    summary.append(f"{passed} passed", style="green")

    if problems > 0:
        summary.append(", ")
        summary.append(f"{problems} failed", style="bold red")

        # Count by severity
        high = sum(
            1 for t in result.tables
            for f in t.findings if f.severity == Severity.HIGH
        )
        medium = sum(
            1 for t in result.tables
            for f in t.findings if f.severity == Severity.MEDIUM
        )
        low = sum(
            1 for t in result.tables
            for f in t.findings if f.severity == Severity.LOW
        )

        parts = []
        if high:
            parts.append(f"{high} HIGH")
        if medium:
            parts.append(f"{medium} MEDIUM")
        if low:
            parts.append(f"{low} LOW")
        if parts:
            summary.append(f" ({', '.join(parts)})")

    console.print(summary)


def print_profile_summary(
    tables: list[dict],
    schema: str,
) -> None:
    """Print a summary after profiling."""
    console.print()
    console.print(f"[bold]Profiled {len(tables)} tables in schema '{schema}':[/bold]")
    console.print()

    for t in tables:
        name = t.get("table_name", t.get("table", "unknown"))
        cols = t.get("column_count", "?")
        rows = t.get("row_count", 0)
        findings_count = t.get("findings_preview", 0)

        line = Text()
        line.append(f"  {schema}.{name}", style="bold")
        line.append(f"  {rows:,} rows, {cols} columns")
        if findings_count > 0:
            line.append(f"  ({findings_count} potential issues)", style="yellow")
        console.print(line)

    console.print()


def format_json_result(result: RunResult) -> dict:
    """Format run results as a JSON-serializable dict."""
    return {
        "ran_at": result.ran_at.isoformat(),
        "summary": {
            "tables": len(result.tables),
            "total_tests": result.total_tests,
            "passed": result.total_passed,
            "failed": result.total_findings,
        },
        "tables": [
            {
                "name": t.table_name,
                "total_tests": t.total_tests,
                "passed": t.passed_count,
                "problems": t.problem_count,
                "findings": [
                    {
                        "severity": f.severity.value,
                        "category": f.category.value,
                        "column": f.column,
                        "message": f.message,
                        "detail": f.detail,
                        "current_value": _safe_serialize(f.current_value),
                        "baseline_value": _safe_serialize(f.baseline_value),
                        "dimension": f.dimension,
                        "rule": f.rule_name,
                    }
                    for f in t.findings
                ],
                "passed_tests": [
                    {
                        "column": p.column,
                        "test": p.test_name,
                        "message": p.message,
                    }
                    for p in t.passed_tests
                ],
            }
            for t in result.tables
        ],
    }


def _safe_serialize(val):
    """Make a value JSON-serializable."""
    if val is None:
        return None
    if isinstance(val, (int, float, str, bool)):
        return val
    return str(val)
