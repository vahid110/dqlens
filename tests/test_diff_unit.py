"""Unit tests for profile diff."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dqlens.diff import (ColumnDiff, ProfileDiff, TableDiff, diff_profiles,
                         format_diff_json, format_diff_text)
from dqlens.models import ColumnProfile, DatabaseProfile, TableProfile


def _col(**kwargs):
    defaults = {
        "name": "id",
        "data_type": "integer",
        "nullable": False,
        "row_count": 100,
        "null_count": 0,
        "null_pct": 0.0,
        "distinct_count": 100,
        "distinct_pct": 100.0,
        "is_unique": True,
    }
    defaults.update(kwargs)
    return ColumnProfile(**defaults)


def _table(name="orders", row_count=100, columns=None, **kwargs):
    return TableProfile(
        schema_name="public",
        table_name=name,
        row_count=row_count,
        columns=columns or [],
        **kwargs,
    )


def _db(tables=None):
    return DatabaseProfile(
        connection_url="",
        schema_name="public",
        tables=tables or [],
    )


class TestDiffProfiles:
    def test_identical_profiles_no_changes(self):
        col = _col()
        before = _db([_table(columns=[col])])
        after = _db([_table(columns=[col])])
        result = diff_profiles(before, after)
        assert not result.has_changes

    def test_table_added(self):
        before = _db([_table(name="users")])
        after = _db([_table(name="users"), _table(name="orders")])
        result = diff_profiles(before, after)
        assert len(result.tables_added) == 1
        assert result.tables_added[0].table == "public.orders"

    def test_table_removed(self):
        before = _db([_table(name="users"), _table(name="orders")])
        after = _db([_table(name="users")])
        result = diff_profiles(before, after)
        assert len(result.tables_removed) == 1
        assert result.tables_removed[0].table == "public.orders"

    def test_row_count_change(self):
        before = _db([_table(row_count=100)])
        after = _db([_table(row_count=200)])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        assert len(modified) == 1
        assert modified[0].row_count_change == 100
        assert modified[0].row_count_change_pct == pytest.approx(100.0)

    def test_row_count_decrease(self):
        before = _db([_table(row_count=200)])
        after = _db([_table(row_count=100)])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        assert modified[0].row_count_change == -100

    def test_column_added(self):
        before = _db([_table(columns=[_col(name="id")])])
        after = _db([_table(columns=[_col(name="id"), _col(name="email")])])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        assert "email" in modified[0].columns_added

    def test_column_removed(self):
        before = _db([_table(columns=[_col(name="id"), _col(name="email")])])
        after = _db([_table(columns=[_col(name="id")])])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        assert "email" in modified[0].columns_removed

    def test_column_type_change(self):
        before = _db([_table(columns=[_col(name="age", data_type="integer")])])
        after = _db([_table(columns=[_col(name="age", data_type="bigint")])])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        assert len(modified) == 1
        changes = modified[0].column_diffs[0].changes
        assert any(c["field"] == "data_type" for c in changes)

    def test_null_rate_drift(self):
        before = _db([_table(columns=[_col(name="email", null_pct=1.0, null_count=1)])])
        after = _db([_table(columns=[_col(name="email", null_pct=15.0, null_count=15)])])
        result = diff_profiles(before, after)
        modified = result.tables_modified
        changes = modified[0].column_diffs[0].changes
        null_change = next(c for c in changes if c["field"] == "null_pct")
        assert null_change["delta"] == 14.0

    def test_small_null_rate_change_ignored(self):
        """Changes < 2pp are not flagged."""
        before = _db([_table(columns=[_col(name="email", null_pct=1.0, null_count=1)])])
        after = _db([_table(columns=[_col(name="email", null_pct=2.5, null_count=2)])])
        result = diff_profiles(before, after)
        assert not result.has_changes

    def test_uniqueness_lost(self):
        before = _db([_table(columns=[_col(name="email", is_unique=True)])])
        after = _db([_table(columns=[_col(name="email", is_unique=False)])])
        result = diff_profiles(before, after)
        changes = result.tables_modified[0].column_diffs[0].changes
        assert any(c["field"] == "is_unique" for c in changes)

    def test_min_max_shift(self):
        before = _db([_table(columns=[_col(name="price", min_value=0.0, max_value=100.0)])])
        after = _db([_table(columns=[_col(name="price", min_value=-5.0, max_value=200.0)])])
        result = diff_profiles(before, after)
        changes = result.tables_modified[0].column_diffs[0].changes
        assert any(c["field"] == "min_value" for c in changes)
        assert any(c["field"] == "max_value" for c in changes)

    def test_pattern_change(self):
        before = _db([_table(columns=[_col(name="data", detected_pattern="email")])])
        after = _db([_table(columns=[_col(name="data", detected_pattern="uuid")])])
        result = diff_profiles(before, after)
        changes = result.tables_modified[0].column_diffs[0].changes
        assert any(c["field"] == "detected_pattern" for c in changes)

    def test_empty_profiles(self):
        result = diff_profiles(_db([]), _db([]))
        assert not result.has_changes
        assert len(result.tables) == 0

    def test_multiple_tables_mixed(self):
        before = _db([
            _table(name="users", row_count=100),
            _table(name="old_table", row_count=50),
        ])
        after = _db([
            _table(name="users", row_count=200),
            _table(name="new_table", row_count=30),
        ])
        result = diff_profiles(before, after)
        assert len(result.tables_added) == 1
        assert len(result.tables_removed) == 1
        assert len(result.tables_modified) == 1


class TestTableDiff:
    def test_row_count_change_pct_zero_before(self):
        td = TableDiff(table="t", status="modified", row_count_before=0, row_count_after=100)
        assert td.row_count_change_pct is None  # Can't compute % from 0

    def test_row_count_change_none(self):
        td = TableDiff(table="t", status="added", row_count_after=100)
        assert td.row_count_change is None


class TestFormatDiffText:
    def test_no_changes(self):
        diff = ProfileDiff(
            before_timestamp="2026-01-01", after_timestamp="2026-01-02",
            schema="public", tables=[],
        )
        text = format_diff_text(diff)
        assert "No changes" in text

    def test_added_table_in_text(self):
        before = _db([])
        after = _db([_table(name="users", row_count=100)])
        diff = diff_profiles(before, after)
        text = format_diff_text(diff)
        assert "TABLE ADDED" in text
        assert "users" in text

    def test_removed_table_in_text(self):
        before = _db([_table(name="users", row_count=100)])
        after = _db([])
        diff = diff_profiles(before, after)
        text = format_diff_text(diff)
        assert "TABLE REMOVED" in text

    def test_summary_in_text(self):
        before = _db([_table(row_count=100)])
        after = _db([_table(row_count=200)])
        diff = diff_profiles(before, after)
        text = format_diff_text(diff)
        assert "Summary:" in text


class TestFormatDiffJson:
    def test_json_structure(self):
        before = _db([_table(row_count=100)])
        after = _db([_table(row_count=200)])
        diff = diff_profiles(before, after)
        data = format_diff_json(diff)
        assert "has_changes" in data
        assert data["has_changes"] is True
        assert "summary" in data
        assert "tables" in data

    def test_json_no_changes(self):
        before = _db([_table()])
        after = _db([_table()])
        diff = diff_profiles(before, after)
        data = format_diff_json(diff)
        assert data["has_changes"] is False
        assert len(data["tables"]) == 0  # Only changed tables in output

    def test_json_added_table(self):
        before = _db([])
        after = _db([_table(name="users")])
        diff = diff_profiles(before, after)
        data = format_diff_json(diff)
        assert data["summary"]["tables_added"] == 1
        assert data["tables"][0]["status"] == "added"
