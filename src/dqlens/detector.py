"""Problem detector — the core value engine of DQLens.

Compares current profile against baseline to find real problems.
Generates findings ranked by severity (HIGH > MEDIUM > LOW).

Design principle: Signal Over Coverage.
- Every finding must explain WHY it was flagged.
- Drift-based detection (was X, now Y) over absolute thresholds.
- First run on any real database should find something interesting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extensions

from dqlens import connector
from dqlens.models import (
    ColumnProfile,
    DatabaseProfile,
    Finding,
    FindingCategory,
    RunResult,
    Severity,
    TableProfile,
    TableResult,
    CheckResult,
)


def detect_problems(
    current: DatabaseProfile,
    baseline: DatabaseProfile | None,
    conn: psycopg2.extensions.connection | None = None,
    ignores: set[str] | None = None,
) -> RunResult:
    """Run all detectors against the current profile.

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

    table_results = []
    for table in current.tables:
        baseline_table = None
        if baseline:
            baseline_table = baseline.get_table(table.table_name)

        result = _check_table(table, baseline_table, conn, ignores)
        table_results.append(result)

    return RunResult(tables=table_results)


def _check_table(
    table: TableProfile,
    baseline_table: TableProfile | None,
    conn: psycopg2.extensions.connection | None,
    ignores: set[str],
) -> TableResult:
    """Run all checks on a single table."""
    findings: list[Finding] = []
    passed: list[CheckResult] = []

    # 1. Row count checks
    _check_row_count(table, baseline_table, findings, passed)

    # 2. Column-level checks
    for col in table.columns:
        baseline_col = None
        if baseline_table:
            baseline_col = baseline_table.get_column(col.name)

        _check_column_nulls(table, col, baseline_col, findings, passed)
        _check_column_uniqueness(table, col, baseline_col, findings, passed)
        _check_column_patterns(table, col, baseline_col, findings, passed)
        _check_column_values(table, col, baseline_col, findings, passed)

    # 3. Foreign key integrity
    if conn and table.foreign_keys:
        _check_fk_integrity(table, conn, findings, passed)

    # 4. Freshness
    _check_freshness(table, baseline_table, findings, passed)

    # Filter out ignored findings
    filtered_findings = []
    for f in findings:
        ignore_key = _make_ignore_key(table.table_name, f)
        if ignore_key not in ignores:
            filtered_findings.append(f)

    # Sort findings: HIGH first, then MEDIUM, then LOW
    severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    filtered_findings.sort(key=lambda f: severity_order[f.severity])

    return TableResult(
        table_name=table.full_name,
        findings=filtered_findings,
        passed_tests=passed,
    )


def _make_ignore_key(table_name: str, finding: Finding) -> str:
    """Create an ignore key for a finding."""
    parts = [table_name]
    if finding.column:
        parts.append(finding.column)
    parts.append(finding.category.value)
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Row count checks
# ---------------------------------------------------------------------------

def _check_row_count(
    table: TableProfile,
    baseline: TableProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check row count — empty table and anomalous growth/shrinkage."""
    if table.row_count == 0:
        findings.append(Finding(
            table=table.full_name,
            column=None,
            severity=Severity.HIGH,
            category=FindingCategory.ROW_COUNT_ANOMALY,
            message="Table is empty (0 rows)",
            detail="An empty table in a live database is usually a problem — "
                   "missing data load, truncated table, or broken pipeline.",
        ))
        return

    passed.append(CheckResult(
        table=table.full_name,
        column=None,
        test_name="row_count_positive",
        passed=True,
        message=f"row_count > 0 ({table.row_count:,} rows)",
    ))

    # Drift: compare against baseline
    if baseline and baseline.row_count > 0:
        change_pct = (
            (table.row_count - baseline.row_count) / baseline.row_count * 100
        )

        if abs(change_pct) > 30:
            direction = "grew" if change_pct > 0 else "shrank"
            findings.append(Finding(
                table=table.full_name,
                column=None,
                severity=Severity.MEDIUM,
                category=FindingCategory.ROW_COUNT_ANOMALY,
                message=(
                    f"Row count {direction} {abs(change_pct):.0f}% "
                    f"({baseline.row_count:,} → {table.row_count:,})"
                ),
                detail=(
                    f"Flagged because: row count changed by {change_pct:+.1f}% since last profile. "
                    f"Large changes may indicate duplicate ingestion, data loss, or pipeline issues."
                ),
                current_value=table.row_count,
                baseline_value=baseline.row_count,
            ))
        elif baseline.row_count > 0 and table.row_count < baseline.row_count:
            # Any shrinkage is worth noting even if < 30%
            if change_pct < -5:
                findings.append(Finding(
                    table=table.full_name,
                    column=None,
                    severity=Severity.LOW,
                    category=FindingCategory.ROW_COUNT_ANOMALY,
                    message=(
                        f"Row count decreased {abs(change_pct):.1f}% "
                        f"({baseline.row_count:,} → {table.row_count:,})"
                    ),
                    detail="Flagged because: tables usually grow. A decrease may indicate data deletion or filtering changes.",
                    current_value=table.row_count,
                    baseline_value=baseline.row_count,
                ))


# ---------------------------------------------------------------------------
# Null checks
# ---------------------------------------------------------------------------

def _check_column_nulls(
    table: TableProfile,
    col: ColumnProfile,
    baseline_col: ColumnProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check null rates — absolute and drift."""
    # Fully null column
    if col.null_count == col.row_count and col.row_count > 0:
        findings.append(Finding(
            table=table.full_name,
            column=col.name,
            severity=Severity.MEDIUM,
            category=FindingCategory.NULL_ANOMALY,
            message="100% null — column is entirely empty",
            detail="Flagged because: every value in this column is NULL. "
                   "This may indicate a broken data source or unused column.",
            current_value=100.0,
        ))
        return

    # Not-null column (good)
    if col.null_count == 0:
        passed.append(CheckResult(
            table=table.full_name,
            column=col.name,
            test_name="not_null",
            passed=True,
            message="not null",
        ))
    else:
        # Has some nulls — check if it's expected
        if col.is_primary_key and col.null_count > 0:
            findings.append(Finding(
                table=table.full_name,
                column=col.name,
                severity=Severity.HIGH,
                category=FindingCategory.NULL_ANOMALY,
                message=f"Primary key has {col.null_count:,} null values ({col.null_pct}%)",
                detail="Flagged because: primary key columns should never contain NULL values.",
                current_value=col.null_pct,
            ))

    # Drift: null rate change
    if baseline_col and baseline_col.row_count > 0 and col.row_count > 0:
        baseline_null_pct = baseline_col.null_pct
        current_null_pct = col.null_pct

        # Significant increase in null rate
        pct_increase = current_null_pct - baseline_null_pct
        if pct_increase > 1.0 and current_null_pct > 0:
            # Calculate multiplier for dramatic changes
            if baseline_null_pct > 0:
                multiplier = current_null_pct / baseline_null_pct
            else:
                multiplier = float("inf")

            new_nulls = col.null_count - baseline_col.null_count

            severity = Severity.MEDIUM
            if multiplier > 10 or pct_increase > 10:
                severity = Severity.HIGH

            detail_parts = [
                f"Flagged because: null rate increased from {baseline_null_pct}% to {current_null_pct}%"
            ]
            if multiplier != float("inf"):
                detail_parts.append(f"({multiplier:.0f}x increase)")
            if new_nulls > 0:
                detail_parts.append(f"— {new_nulls:,} new null values since last profile")

            findings.append(Finding(
                table=table.full_name,
                column=col.name,
                severity=severity,
                category=FindingCategory.NULL_ANOMALY,
                message=(
                    f"{current_null_pct}% null (was {baseline_null_pct}% in baseline)"
                ),
                detail=" ".join(detail_parts),
                current_value=current_null_pct,
                baseline_value=baseline_null_pct,
            ))


# ---------------------------------------------------------------------------
# Uniqueness checks
# ---------------------------------------------------------------------------

def _check_column_uniqueness(
    table: TableProfile,
    col: ColumnProfile,
    baseline_col: ColumnProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check uniqueness constraints."""
    if col.row_count == 0:
        return

    if col.is_primary_key or col.is_unique:
        if col.distinct_count == col.non_null_count:
            passed.append(CheckResult(
                table=table.full_name,
                column=col.name,
                test_name="unique",
                passed=True,
                message="unique" + (", not null" if col.null_count == 0 else ""),
            ))
        else:
            dup_count = col.non_null_count - col.distinct_count
            findings.append(Finding(
                table=table.full_name,
                column=col.name,
                severity=Severity.HIGH,
                category=FindingCategory.UNIQUENESS_VIOLATION,
                message=f"Expected unique but has {dup_count:,} duplicate values",
                detail=(
                    f"Flagged because: column is marked as "
                    f"{'primary key' if col.is_primary_key else 'unique'} "
                    f"but has {col.distinct_count:,} distinct values "
                    f"across {col.non_null_count:,} non-null rows."
                ),
                current_value=col.distinct_count,
            ))

    # Drift: uniqueness lost
    if baseline_col and baseline_col.is_unique and not col.is_unique:
        findings.append(Finding(
            table=table.full_name,
            column=col.name,
            severity=Severity.HIGH,
            category=FindingCategory.UNIQUENESS_VIOLATION,
            message="Was unique in baseline, no longer unique",
            detail="Flagged because: this column was fully unique in the previous profile "
                   "but now contains duplicate values. This may indicate a data quality regression.",
            current_value=col.distinct_count,
            baseline_value=baseline_col.distinct_count,
        ))


# ---------------------------------------------------------------------------
# Pattern checks
# ---------------------------------------------------------------------------

def _check_column_patterns(
    table: TableProfile,
    col: ColumnProfile,
    baseline_col: ColumnProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check detected patterns (email, UUID, etc.)."""
    if not col.detected_pattern:
        return

    if col.pattern_match_pct and col.pattern_match_pct >= 95:
        passed.append(CheckResult(
            table=table.full_name,
            column=col.name,
            test_name=f"pattern_{col.detected_pattern}",
            passed=True,
            message=f"matches {col.detected_pattern} pattern ({col.pattern_match_pct}%)",
        ))
    elif col.pattern_match_pct:
        violation_pct = 100 - col.pattern_match_pct
        severity = Severity.LOW
        if violation_pct > 20:
            severity = Severity.MEDIUM

        findings.append(Finding(
            table=table.full_name,
            column=col.name,
            severity=severity,
            category=FindingCategory.PATTERN_VIOLATION,
            message=(
                f"{violation_pct:.1f}% of values don't match "
                f"{col.detected_pattern} pattern"
            ),
            detail=(
                f"Flagged because: column appears to contain {col.detected_pattern} values "
                f"but {violation_pct:.1f}% don't match the expected pattern. "
                f"This may indicate data quality issues or mixed-format data."
            ),
            current_value=col.pattern_match_pct,
        ))

    # Drift: pattern match rate dropped
    if (
        baseline_col
        and baseline_col.detected_pattern == col.detected_pattern
        and baseline_col.pattern_match_pct
        and col.pattern_match_pct
    ):
        drop = baseline_col.pattern_match_pct - col.pattern_match_pct
        if drop > 3:
            findings.append(Finding(
                table=table.full_name,
                column=col.name,
                severity=Severity.MEDIUM,
                category=FindingCategory.PATTERN_VIOLATION,
                message=(
                    f"{col.detected_pattern} pattern match dropped "
                    f"{drop:.1f}pp ({baseline_col.pattern_match_pct}% → {col.pattern_match_pct}%)"
                ),
                detail=(
                    f"Flagged because: the percentage of values matching the "
                    f"{col.detected_pattern} pattern decreased since last profile."
                ),
                current_value=col.pattern_match_pct,
                baseline_value=baseline_col.pattern_match_pct,
            ))


# ---------------------------------------------------------------------------
# Value range checks
# ---------------------------------------------------------------------------

def _check_column_values(
    table: TableProfile,
    col: ColumnProfile,
    baseline_col: ColumnProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check value ranges and distributions."""
    if col.row_count == 0 or col.null_count == col.row_count:
        return

    # Numeric range checks
    if col.min_value is not None and col.max_value is not None:
        # Check for likely-positive columns with negative values
        positive_indicators = {"price", "amount", "cost", "total", "quantity", "count", "size", "age", "weight"}
        col_lower = col.name.lower()
        if any(ind in col_lower for ind in positive_indicators):
            if isinstance(col.min_value, (int, float)) and col.min_value < 0:
                findings.append(Finding(
                    table=table.full_name,
                    column=col.name,
                    severity=Severity.MEDIUM,
                    category=FindingCategory.DISTRIBUTION_SHIFT,
                    message=f"Contains negative values (min: {col.min_value}) but column name suggests positive-only",
                    detail=(
                        f"Flagged because: column name '{col.name}' suggests values should be positive, "
                        f"but minimum value is {col.min_value}."
                    ),
                    current_value=col.min_value,
                ))
            elif isinstance(col.min_value, (int, float)) and col.min_value >= 0:
                passed.append(CheckResult(
                    table=table.full_name,
                    column=col.name,
                    test_name="positive_values",
                    passed=True,
                    message=f"always positive (min: {col.min_value}, max: {col.max_value})",
                ))

    # Drift: value range shift
    if baseline_col and baseline_col.min_value is not None and col.min_value is not None:
        try:
            bl_min = float(baseline_col.min_value)
            bl_max = float(baseline_col.max_value)
            cur_min = float(col.min_value)
            cur_max = float(col.max_value)

            bl_range = bl_max - bl_min
            if bl_range > 0:
                # Check if max expanded significantly
                if cur_max > bl_max:
                    expansion = (cur_max - bl_max) / bl_range * 100
                    if expansion > 50:
                        findings.append(Finding(
                            table=table.full_name,
                            column=col.name,
                            severity=Severity.LOW,
                            category=FindingCategory.DISTRIBUTION_SHIFT,
                            message=(
                                f"Max value expanded significantly "
                                f"({bl_max} → {cur_max})"
                            ),
                            detail=(
                                f"Flagged because: maximum value increased by "
                                f"{expansion:.0f}% of the previous range. "
                                f"This may indicate outliers or data entry errors."
                            ),
                            current_value=cur_max,
                            baseline_value=bl_max,
                        ))
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Foreign key integrity
# ---------------------------------------------------------------------------

def _check_fk_integrity(
    table: TableProfile,
    conn: psycopg2.extensions.connection,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check foreign key integrity — find orphaned rows."""
    for fk in table.foreign_keys:
        try:
            result = connector.check_fk_integrity(
                conn,
                table.schema_name,
                fk.source_table,
                fk.source_column,
                fk.target_table,
                fk.target_column,
            )
        except Exception:
            # FK check failed (target table might not exist, etc.)
            continue

        orphaned = result["orphaned"]
        non_null = result["non_null"]

        if orphaned > 0:
            orphan_pct = orphaned / non_null * 100 if non_null > 0 else 0
            findings.append(Finding(
                table=table.full_name,
                column=fk.source_column,
                severity=Severity.HIGH,
                category=FindingCategory.FK_MISMATCH,
                message=(
                    f"{orphaned:,} rows reference non-existent "
                    f"{fk.target_table}.{fk.target_column} (FK mismatch)"
                ),
                detail=(
                    f"Flagged because: {orphaned:,} rows ({orphan_pct:.1f}%) in "
                    f"{fk.source_column} reference values that don't exist in "
                    f"{fk.target_table}.{fk.target_column}. "
                    f"This indicates referential integrity violations."
                ),
                current_value=orphaned,
            ))
        else:
            match_pct = 100.0 if non_null > 0 else 0.0
            passed.append(CheckResult(
                table=table.full_name,
                column=fk.source_column,
                test_name="fk_integrity",
                passed=True,
                message=(
                    f"references {table.schema_name}.{fk.target_table}.{fk.target_column} "
                    f"({match_pct:.0f}% match)"
                ),
            ))


# ---------------------------------------------------------------------------
# Freshness checks
# ---------------------------------------------------------------------------

def _check_freshness(
    table: TableProfile,
    baseline: TableProfile | None,
    findings: list[Finding],
    passed: list[CheckResult],
) -> None:
    """Check data freshness based on timestamp columns."""
    if not table.freshness_column or not table.latest_timestamp:
        return

    now = datetime.now(timezone.utc)
    # Handle both naive and aware datetimes
    latest = table.latest_timestamp
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    age = now - latest

    if age > timedelta(days=7):
        findings.append(Finding(
            table=table.full_name,
            column=table.freshness_column,
            severity=Severity.MEDIUM,
            category=FindingCategory.FRESHNESS,
            message=f"Last row is {_format_age(age)} old",
            detail=(
                f"Flagged because: the most recent value in {table.freshness_column} "
                f"is {table.latest_timestamp.isoformat()}, which is {_format_age(age)} ago. "
                f"This may indicate a stale data source."
            ),
            current_value=str(table.latest_timestamp),
        ))
    elif age > timedelta(days=1):
        findings.append(Finding(
            table=table.full_name,
            column=table.freshness_column,
            severity=Severity.LOW,
            category=FindingCategory.FRESHNESS,
            message=f"Last row is {_format_age(age)} old",
            detail=(
                f"Flagged because: the most recent value in {table.freshness_column} "
                f"is {table.latest_timestamp.isoformat()}."
            ),
            current_value=str(table.latest_timestamp),
        ))
    else:
        passed.append(CheckResult(
            table=table.full_name,
            column=table.freshness_column,
            test_name="freshness",
            passed=True,
            message=f"last row < {_format_age(age)} ago",
        ))


def _format_age(delta: timedelta) -> str:
    """Format a timedelta as a human-readable age string."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"
