"""Tests for DQLens data models."""

from datetime import datetime

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


def _make_column(**kwargs):
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


def _make_table(**kwargs):
    defaults = {
        "schema_name": "public",
        "table_name": "orders",
        "row_count": 100,
    }
    defaults.update(kwargs)
    return TableProfile(**defaults)


class TestColumnProfile:
    def test_non_null_count(self):
        col = _make_column(row_count=100, null_count=10)
        assert col.non_null_count == 90

    def test_non_null_count_zero_nulls(self):
        col = _make_column(row_count=100, null_count=0)
        assert col.non_null_count == 100


class TestTableProfile:
    def test_full_name(self):
        table = _make_table(schema_name="public", table_name="orders")
        assert table.full_name == "public.orders"

    def test_get_column_found(self):
        col = _make_column(name="email")
        table = _make_table(columns=[col])
        assert table.get_column("email") is col

    def test_get_column_not_found(self):
        table = _make_table(columns=[])
        assert table.get_column("missing") is None


class TestDatabaseProfile:
    def test_get_table_by_name(self):
        t = _make_table(table_name="orders")
        db = DatabaseProfile(connection_url="", schema_name="public", tables=[t])
        assert db.get_table("orders") is t

    def test_get_table_by_full_name(self):
        t = _make_table(schema_name="public", table_name="orders")
        db = DatabaseProfile(connection_url="", schema_name="public", tables=[t])
        assert db.get_table("public.orders") is t

    def test_get_table_not_found(self):
        db = DatabaseProfile(connection_url="", schema_name="public", tables=[])
        assert db.get_table("missing") is None


class TestFinding:
    def test_str_with_column(self):
        f = Finding(
            table="public.orders",
            column="email",
            severity=Severity.HIGH,
            category=FindingCategory.NULL_ANOMALY,
            message="3.2% null",
            detail="test",
        )
        s = str(f)
        assert "HIGH" in s
        assert "email" in s
        assert "3.2% null" in s

    def test_str_without_column(self):
        f = Finding(
            table="public.orders",
            column=None,
            severity=Severity.MEDIUM,
            category=FindingCategory.ROW_COUNT_ANOMALY,
            message="Row count grew 47%",
            detail="test",
        )
        s = str(f)
        assert "MEDIUM" in s
        assert "Row count grew 47%" in s


class TestRunResult:
    def test_totals(self):
        t1 = TableResult(
            table_name="public.orders",
            findings=[
                Finding(
                    table="public.orders",
                    column="email",
                    severity=Severity.HIGH,
                    category=FindingCategory.NULL_ANOMALY,
                    message="test",
                    detail="test",
                ),
            ],
            passed_tests=[
                CheckResult(
                    table="public.orders",
                    column="id",
                    test_name="unique",
                    passed=True,
                    message="unique",
                ),
                CheckResult(
                    table="public.orders",
                    column=None,
                    test_name="row_count",
                    passed=True,
                    message="row_count > 0",
                ),
            ],
        )
        t2 = TableResult(
            table_name="public.customers",
            findings=[],
            passed_tests=[
                CheckResult(
                    table="public.customers",
                    column="id",
                    test_name="unique",
                    passed=True,
                    message="unique",
                ),
            ],
        )
        result = RunResult(tables=[t1, t2])
        assert result.total_tests == 4
        assert result.total_findings == 1
        assert result.total_passed == 3
        assert result.has_problems is True

    def test_no_problems(self):
        t = TableResult(
            table_name="public.orders",
            findings=[],
            passed_tests=[
                CheckResult(
                    table="public.orders",
                    column="id",
                    test_name="unique",
                    passed=True,
                    message="unique",
                ),
            ],
        )
        result = RunResult(tables=[t])
        assert result.has_problems is False
        assert result.total_findings == 0

    def test_all_findings(self):
        f1 = Finding(
            table="t1", column="c1",
            severity=Severity.HIGH,
            category=FindingCategory.NULL_ANOMALY,
            message="m1", detail="d1",
        )
        f2 = Finding(
            table="t2", column="c2",
            severity=Severity.LOW,
            category=FindingCategory.PATTERN_VIOLATION,
            message="m2", detail="d2",
        )
        result = RunResult(tables=[
            TableResult(table_name="t1", findings=[f1]),
            TableResult(table_name="t2", findings=[f2]),
        ])
        assert len(result.all_findings) == 2
