"""Unit tests for the rule engine runner."""

from datetime import datetime, timedelta, timezone

from dqlens.engine import run_checks
from dqlens.models import (ColumnProfile, DatabaseProfile, FindingCategory,
                           Severity, TableProfile)


def _col(**kwargs):
    defaults = {
        "name": "id",
        "data_type": "integer",
        "nullable": False,
        "row_count": 1000,
        "null_count": 0,
        "null_pct": 0.0,
        "distinct_count": 1000,
        "distinct_pct": 100.0,
        "is_unique": True,
        "is_primary_key": True,
    }
    defaults.update(kwargs)
    return ColumnProfile(**defaults)


def _table(name="orders", row_count=1000, columns=None, **kwargs):
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


class TestRunChecks:
    def test_empty_database(self):
        result = run_checks(_db([]))
        assert result.total_tests == 0
        assert result.total_findings == 0

    def test_empty_table_detected(self):
        result = run_checks(_db([_table(row_count=0)]))
        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.ROW_COUNT_ANOMALY for f in findings)

    def test_positive_row_count_passes(self):
        result = run_checks(_db([_table(row_count=500, columns=[_col()])]))
        passed = result.tables[0].passed_tests
        assert any("row_count" in p.message for p in passed)

    def test_not_null_column_passes(self):
        col = _col(name="email", null_count=0, is_primary_key=False)
        result = run_checks(_db([_table(columns=[col])]))
        passed = result.tables[0].passed_tests
        assert any(p.column == "email" and p.test_name == "not_null" for p in passed)

    def test_unique_column_passes(self):
        col = _col(name="id", is_unique=True, distinct_count=1000, null_count=0)
        result = run_checks(_db([_table(columns=[col])]))
        passed = result.tables[0].passed_tests
        assert any(p.column == "id" and "unique" in p.test_name for p in passed)

    def test_negative_price_detected(self):
        col = _col(
            name="price", data_type="numeric",
            min_value=-5.0, max_value=100.0,
            is_unique=False, is_primary_key=False, distinct_count=50,
        )
        result = run_checks(_db([_table(columns=[col])]))
        findings = result.tables[0].findings
        assert any("negative" in f.message.lower() for f in findings)

    def test_pattern_match_passes(self):
        col = _col(
            name="email", data_type="character varying",
            detected_pattern="email", pattern_match_pct=99.5,
            is_unique=False, is_primary_key=False, distinct_count=900,
        )
        result = run_checks(_db([_table(columns=[col])]))
        passed = result.tables[0].passed_tests
        assert any("email pattern" in p.message for p in passed)

    def test_freshness_passes(self):
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
            columns=[_col()],
        )
        result = run_checks(_db([table]))
        passed = result.tables[0].passed_tests
        assert any(p.test_name == "freshness" for p in passed)

    def test_stale_data_flagged(self):
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(days=10),
            columns=[_col()],
        )
        result = run_checks(_db([table]))
        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.FRESHNESS for f in findings)


class TestDriftDetection:
    def test_row_count_growth(self):
        baseline = _db([_table(row_count=1000)])
        current = _db([_table(row_count=1500)])
        result = run_checks(current, baseline)
        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.ROW_COUNT_ANOMALY for f in findings)

    def test_null_rate_drift(self):
        baseline_col = _col(name="email", null_count=1, null_pct=0.1, is_unique=False, is_primary_key=False)
        current_col = _col(name="email", null_count=32, null_pct=3.2, is_unique=False, is_primary_key=False)
        baseline = _db([_table(columns=[baseline_col])])
        current = _db([_table(columns=[current_col])])
        result = run_checks(current, baseline)
        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.NULL_ANOMALY for f in findings)

    def test_no_drift_same_data(self):
        col = _col(name="id")
        baseline = _db([_table(row_count=1000, columns=[col])])
        current = _db([_table(row_count=1000, columns=[col])])
        result = run_checks(current, baseline)
        drift_findings = [
            f for t in result.tables for f in t.findings
            if f.baseline_value is not None
        ]
        assert len(drift_findings) == 0


class TestIgnoreFiltering:
    def test_ignored_finding_suppressed(self):
        current = _db([_table(row_count=0)])
        result_before = run_checks(current)
        result_after = run_checks(current, ignores={"orders.row_count_anomaly"})
        assert result_after.total_findings < result_before.total_findings

    def test_nonexistent_ignore_no_effect(self):
        col = _col(name="price", min_value=-5.0, max_value=100.0,
                   is_unique=False, is_primary_key=False, distinct_count=50)
        current = _db([_table(columns=[col])])
        r1 = run_checks(current)
        r2 = run_checks(current, ignores={"nonexistent.key"})
        assert r1.total_findings == r2.total_findings


class TestSeverityOrdering:
    def test_findings_sorted(self):
        cols = [
            _col(name="phone", data_type="character varying",
                 detected_pattern="phone", pattern_match_pct=80.0,
                 is_unique=False, is_primary_key=False, distinct_count=50),
            _col(name="id", is_primary_key=True, null_count=5, null_pct=0.5),
        ]
        result = run_checks(_db([_table(columns=cols)]))
        findings = result.tables[0].findings
        if len(findings) >= 2:
            severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
            severities = [f.severity for f in findings]
            assert severities == sorted(severities, key=lambda s: severity_order[s])


class TestDimensionTagging:
    def test_findings_have_dimension(self):
        current = _db([_table(row_count=0)])
        result = run_checks(current)
        for t in result.tables:
            for f in t.findings:
                assert f.dimension is not None, f"Finding missing dimension: {f.message}"

    def test_findings_have_rule_name(self):
        current = _db([_table(row_count=0)])
        result = run_checks(current)
        for t in result.tables:
            for f in t.findings:
                assert f.rule_name is not None, f"Finding missing rule_name: {f.message}"

    def test_passed_have_dimension(self):
        col = _col(name="id", null_count=0)
        result = run_checks(_db([_table(row_count=100, columns=[col])]))
        for t in result.tables:
            for p in t.passed_tests:
                assert p.dimension is not None, f"Passed test missing dimension: {p.message}"
