"""Profile diff — compare two database profiles to see what changed.

Use cases:
- Schema drift detection (columns added/removed/type changed)
- Data migration validation (row counts, null rates before/after)
- Pre/post pipeline checks (did the pipeline break anything?)
- Comparing environments (staging vs production)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dqlens.models import ColumnProfile, DatabaseProfile, TableProfile


@dataclass
class ColumnDiff:
    """Changes detected in a single column."""

    column: str
    changes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0


@dataclass
class TableDiff:
    """Changes detected in a single table."""

    table: str
    status: str  # "unchanged", "modified", "added", "removed"
    row_count_before: int | None = None
    row_count_after: int | None = None
    columns_added: list[str] = field(default_factory=list)
    columns_removed: list[str] = field(default_factory=list)
    column_diffs: list[ColumnDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return (
            self.status != "unchanged"
            or bool(self.columns_added)
            or bool(self.columns_removed)
            or any(cd.has_changes for cd in self.column_diffs)
        )

    @property
    def row_count_change(self) -> int | None:
        if self.row_count_before is not None and self.row_count_after is not None:
            return self.row_count_after - self.row_count_before
        return None

    @property
    def row_count_change_pct(self) -> float | None:
        if (
            self.row_count_before is not None
            and self.row_count_after is not None
            and self.row_count_before > 0
        ):
            return (self.row_count_after - self.row_count_before) / self.row_count_before * 100
        return None


@dataclass
class ProfileDiff:
    """Full diff between two database profiles."""

    before_timestamp: str
    after_timestamp: str
    schema: str
    tables: list[TableDiff] = field(default_factory=list)

    @property
    def tables_added(self) -> list[TableDiff]:
        return [t for t in self.tables if t.status == "added"]

    @property
    def tables_removed(self) -> list[TableDiff]:
        return [t for t in self.tables if t.status == "removed"]

    @property
    def tables_modified(self) -> list[TableDiff]:
        return [t for t in self.tables if t.has_changes and t.status not in ("added", "removed")]

    @property
    def tables_unchanged(self) -> list[TableDiff]:
        return [t for t in self.tables if not t.has_changes]

    @property
    def has_changes(self) -> bool:
        return any(t.has_changes for t in self.tables)


def diff_profiles(
    before: DatabaseProfile,
    after: DatabaseProfile,
) -> ProfileDiff:
    """Compare two profiles and return the differences.

    Args:
        before: The older profile.
        after: The newer profile.

    Returns:
        ProfileDiff with all detected changes.
    """
    before_tables = {t.table_name: t for t in before.tables}
    after_tables = {t.table_name: t for t in after.tables}

    all_table_names = sorted(set(before_tables.keys()) | set(after_tables.keys()))

    table_diffs = []
    for name in all_table_names:
        b = before_tables.get(name)
        a = after_tables.get(name)

        if b is None and a is not None:
            table_diffs.append(TableDiff(
                table=a.full_name,
                status="added",
                row_count_after=a.row_count,
            ))
        elif b is not None and a is None:
            table_diffs.append(TableDiff(
                table=b.full_name,
                status="removed",
                row_count_before=b.row_count,
            ))
        elif b is not None and a is not None:
            table_diffs.append(_diff_tables(b, a))

    return ProfileDiff(
        before_timestamp=before.profiled_at.isoformat(),
        after_timestamp=after.profiled_at.isoformat(),
        schema=after.schema_name,
        tables=table_diffs,
    )


def _diff_tables(before: TableProfile, after: TableProfile) -> TableDiff:
    """Compare two versions of the same table."""
    before_cols = {c.name: c for c in before.columns}
    after_cols = {c.name: c for c in after.columns}

    columns_added = sorted(set(after_cols.keys()) - set(before_cols.keys()))
    columns_removed = sorted(set(before_cols.keys()) - set(after_cols.keys()))

    column_diffs = []
    for col_name in sorted(set(before_cols.keys()) & set(after_cols.keys())):
        cd = _diff_columns(before_cols[col_name], after_cols[col_name])
        if cd.has_changes:
            column_diffs.append(cd)

    has_row_change = before.row_count != after.row_count
    status = "modified" if (
        has_row_change or columns_added or columns_removed or column_diffs
    ) else "unchanged"

    return TableDiff(
        table=after.full_name,
        status=status,
        row_count_before=before.row_count,
        row_count_after=after.row_count,
        columns_added=columns_added,
        columns_removed=columns_removed,
        column_diffs=column_diffs,
    )


def _diff_columns(before: ColumnProfile, after: ColumnProfile) -> ColumnDiff:
    """Compare two versions of the same column."""
    changes: list[dict[str, Any]] = []

    # Type change
    if before.data_type != after.data_type:
        changes.append({
            "field": "data_type",
            "before": before.data_type,
            "after": after.data_type,
        })

    # Nullable change
    if before.nullable != after.nullable:
        changes.append({
            "field": "nullable",
            "before": before.nullable,
            "after": after.nullable,
        })

    # Null rate change (significant = >2pp)
    if abs(after.null_pct - before.null_pct) > 2:
        changes.append({
            "field": "null_pct",
            "before": before.null_pct,
            "after": after.null_pct,
            "delta": round(after.null_pct - before.null_pct, 2),
        })

    # Uniqueness change
    if before.is_unique != after.is_unique:
        changes.append({
            "field": "is_unique",
            "before": before.is_unique,
            "after": after.is_unique,
        })

    # Distinct count change (significant = >10%)
    if before.distinct_count > 0:
        distinct_change_pct = abs(
            after.distinct_count - before.distinct_count
        ) / before.distinct_count * 100
        if distinct_change_pct > 10:
            changes.append({
                "field": "distinct_count",
                "before": before.distinct_count,
                "after": after.distinct_count,
                "delta_pct": round(distinct_change_pct, 1),
            })

    # Min/max shift
    if before.min_value is not None and after.min_value is not None:
        try:
            if float(before.min_value) != float(after.min_value):
                changes.append({
                    "field": "min_value",
                    "before": before.min_value,
                    "after": after.min_value,
                })
        except (TypeError, ValueError):
            if str(before.min_value) != str(after.min_value):
                changes.append({
                    "field": "min_value",
                    "before": before.min_value,
                    "after": after.min_value,
                })

    if before.max_value is not None and after.max_value is not None:
        try:
            if float(before.max_value) != float(after.max_value):
                changes.append({
                    "field": "max_value",
                    "before": before.max_value,
                    "after": after.max_value,
                })
        except (TypeError, ValueError):
            if str(before.max_value) != str(after.max_value):
                changes.append({
                    "field": "max_value",
                    "before": before.max_value,
                    "after": after.max_value,
                })

    # Pattern change
    if before.detected_pattern != after.detected_pattern:
        changes.append({
            "field": "detected_pattern",
            "before": before.detected_pattern,
            "after": after.detected_pattern,
        })

    return ColumnDiff(column=after.name, changes=changes)


def format_diff_text(diff: ProfileDiff) -> str:
    """Format a ProfileDiff as human-readable text."""
    lines: list[str] = []

    if not diff.has_changes:
        lines.append("No changes detected between profiles.")
        return "\n".join(lines)

    # Added tables
    for t in diff.tables_added:
        lines.append(f"+ TABLE ADDED: {t.table} ({t.row_count_after:,} rows)")

    # Removed tables
    for t in diff.tables_removed:
        lines.append(f"- TABLE REMOVED: {t.table} ({t.row_count_before:,} rows)")

    # Modified tables
    for t in diff.tables_modified:
        lines.append(f"~ {t.table}:")

        if t.row_count_change and t.row_count_change != 0:
            pct = t.row_count_change_pct
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(
                f"    rows: {t.row_count_before:,} -> {t.row_count_after:,}"
                f" ({t.row_count_change:+,}){pct_str}"
            )

        for col in t.columns_added:
            lines.append(f"    + column added: {col}")
        for col in t.columns_removed:
            lines.append(f"    - column removed: {col}")

        for cd in t.column_diffs:
            for change in cd.changes:
                field = change["field"]
                before = change["before"]
                after = change["after"]
                lines.append(f"    {cd.column}.{field}: {before} -> {after}")

    # Summary
    n_added = len(diff.tables_added)
    n_removed = len(diff.tables_removed)
    n_modified = len(diff.tables_modified)
    n_unchanged = len(diff.tables_unchanged)
    parts = []
    if n_added:
        parts.append(f"{n_added} added")
    if n_removed:
        parts.append(f"{n_removed} removed")
    if n_modified:
        parts.append(f"{n_modified} modified")
    if n_unchanged:
        parts.append(f"{n_unchanged} unchanged")
    lines.append(f"\nSummary: {', '.join(parts)}")

    return "\n".join(lines)


def format_diff_json(diff: ProfileDiff) -> dict[str, Any]:
    """Format a ProfileDiff as a JSON-serializable dict."""
    return {
        "before": diff.before_timestamp,
        "after": diff.after_timestamp,
        "schema": diff.schema,
        "has_changes": diff.has_changes,
        "summary": {
            "tables_added": len(diff.tables_added),
            "tables_removed": len(diff.tables_removed),
            "tables_modified": len(diff.tables_modified),
            "tables_unchanged": len(diff.tables_unchanged),
        },
        "tables": [
            {
                "table": t.table,
                "status": t.status,
                "row_count_before": t.row_count_before,
                "row_count_after": t.row_count_after,
                "row_count_change": t.row_count_change,
                "columns_added": t.columns_added,
                "columns_removed": t.columns_removed,
                "column_changes": [
                    {
                        "column": cd.column,
                        "changes": cd.changes,
                    }
                    for cd in t.column_diffs
                ],
            }
            for t in diff.tables
            if t.has_changes
        ],
    }
