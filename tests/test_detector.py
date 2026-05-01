"""Tests for the problem detector — the core value engine."""

from datetime import datetime, timedelta, timezone

from dqlens.detector import detect_problems
from dqlens.models import (
    ColumnProfile,
    DatabaseProfile,
    FindingCategory,
    ForeignKeyInfo,
    Severity,
    TableProfile,
)


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


class TestRowCountChecks:
    def test_empty_table_is_high_severity(self):
        current = _db([_table(row_count=0)])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].category == FindingCategory.ROW_COUNT_ANOMALY
        assert "empty" in findings[0].message.lower()

    def test_positive_row_count_passes(self):
        current = _db([_table(row_count=500)])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any("row_count" in t.test_name for t in passed)

    def test_large_growth_detected(self):
        baseline = _db([_table(row_count=1000)])
        current = _db([_table(row_count=1500)])  # 50% growth
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.ROW_COUNT_ANOMALY for f in findings)
        assert any("50%" in f.message for f in findings)

    def test_large_shrinkage_detected(self):
        baseline = _db([_table(row_count=1000)])
        current = _db([_table(row_count=500)])  # 50% shrinkage
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.ROW_COUNT_ANOMALY for f in findings)

    def test_small_change_no_finding(self):
        baseline = _db([_table(row_count=1000)])
        current = _db([_table(row_count=1020)])  # 2% growth
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        row_count_findings = [
            f for f in findings if f.category == FindingCategory.ROW_COUNT_ANOMALY
        ]
        assert len(row_count_findings) == 0


class TestNullChecks:
    def test_fully_null_column(self):
        col = _col(name="notes", row_count=100, null_count=100, null_pct=100.0, is_unique=False)
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        null_findings = [f for f in findings if f.category == FindingCategory.NULL_ANOMALY]
        assert len(null_findings) == 1
        assert "100% null" in null_findings[0].message

    def test_not_null_passes(self):
        col = _col(name="id", null_count=0, null_pct=0.0)
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any(t.test_name == "not_null" and t.column == "id" for t in passed)

    def test_null_rate_drift_detected(self):
        baseline_col = _col(name="email", null_count=1, null_pct=0.1, is_unique=False)
        current_col = _col(name="email", null_count=32, null_pct=3.2, is_unique=False)

        baseline = _db([_table(columns=[baseline_col])])
        current = _db([_table(columns=[current_col])])
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        null_drift = [f for f in findings if f.category == FindingCategory.NULL_ANOMALY]
        assert len(null_drift) >= 1
        assert any("baseline" in f.message for f in null_drift)

    def test_pk_with_nulls_is_high(self):
        col = _col(
            name="id", is_primary_key=True,
            null_count=5, null_pct=0.5,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        pk_null = [f for f in findings if f.category == FindingCategory.NULL_ANOMALY]
        assert any(f.severity == Severity.HIGH for f in pk_null)


class TestUniquenessChecks:
    def test_unique_column_passes(self):
        col = _col(name="id", is_unique=True, distinct_count=1000, null_count=0)
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any("unique" in t.test_name for t in passed)

    def test_duplicate_pk_detected(self):
        col = _col(
            name="id", is_primary_key=True, is_unique=True,
            row_count=1000, null_count=0, distinct_count=990,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        dup_findings = [
            f for f in findings if f.category == FindingCategory.UNIQUENESS_VIOLATION
        ]
        assert len(dup_findings) == 1
        assert dup_findings[0].severity == Severity.HIGH

    def test_uniqueness_lost_drift(self):
        baseline_col = _col(name="email", is_unique=True, distinct_count=1000, null_count=0)
        current_col = _col(name="email", is_unique=False, distinct_count=950, null_count=0)

        baseline = _db([_table(columns=[baseline_col])])
        current = _db([_table(columns=[current_col])])
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.UNIQUENESS_VIOLATION for f in findings)


class TestPatternChecks:
    def test_high_pattern_match_passes(self):
        col = _col(
            name="email", data_type="character varying", is_unique=False,
            detected_pattern="email", pattern_match_pct=99.5,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any("email pattern" in t.message for t in passed)

    def test_low_pattern_match_flagged(self):
        col = _col(
            name="email", data_type="character varying", is_unique=False,
            detected_pattern="email", pattern_match_pct=75.0,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        pattern_findings = [
            f for f in findings if f.category == FindingCategory.PATTERN_VIOLATION
        ]
        assert len(pattern_findings) == 1

    def test_pattern_match_drift(self):
        baseline_col = _col(
            name="email", data_type="character varying", is_unique=False,
            detected_pattern="email", pattern_match_pct=99.0,
        )
        current_col = _col(
            name="email", data_type="character varying", is_unique=False,
            detected_pattern="email", pattern_match_pct=92.0,
        )
        baseline = _db([_table(columns=[baseline_col])])
        current = _db([_table(columns=[current_col])])
        result = detect_problems(current, baseline)

        findings = result.tables[0].findings
        assert any(
            f.category == FindingCategory.PATTERN_VIOLATION and "dropped" in f.message
            for f in findings
        )


class TestValueChecks:
    def test_negative_price_flagged(self):
        col = _col(
            name="price", data_type="numeric", is_unique=False,
            min_value=-5.0, max_value=100.0,
            distinct_count=50,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        assert any(
            f.category == FindingCategory.DISTRIBUTION_SHIFT and "negative" in f.message
            for f in findings
        )

    def test_positive_price_passes(self):
        col = _col(
            name="amount", data_type="numeric", is_unique=False,
            min_value=0.01, max_value=9999.99,
            distinct_count=500,
        )
        current = _db([_table(columns=[col])])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any("positive" in t.message for t in passed)


class TestFreshnessChecks:
    def test_fresh_data_passes(self):
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        current = _db([table])
        result = detect_problems(current, baseline=None)

        passed = result.tables[0].passed_tests
        assert any(t.test_name == "freshness" for t in passed)

    def test_stale_data_flagged(self):
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(days=10),
        )
        current = _db([table])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        assert any(f.category == FindingCategory.FRESHNESS for f in findings)


class TestIgnores:
    def test_ignored_finding_suppressed(self):
        col = _col(name="notes", row_count=100, null_count=100, null_pct=100.0, is_unique=False)
        current = _db([_table(columns=[col])])

        # Without ignore
        result = detect_problems(current, baseline=None)
        assert result.total_findings > 0

        # With ignore
        result = detect_problems(
            current, baseline=None,
            ignores={"orders.notes.null_anomaly"},
        )
        null_findings = [
            f for t in result.tables for f in t.findings
            if f.category == FindingCategory.NULL_ANOMALY and f.column == "notes"
        ]
        assert len(null_findings) == 0


class TestSeverityOrdering:
    def test_findings_sorted_by_severity(self):
        cols = [
            # LOW: pattern issue
            _col(
                name="phone", data_type="character varying", is_unique=False,
                detected_pattern="phone", pattern_match_pct=80.0,
                distinct_count=50,
            ),
            # HIGH: PK with nulls
            _col(
                name="id", is_primary_key=True,
                null_count=5, null_pct=0.5,
            ),
        ]
        current = _db([_table(columns=cols)])
        result = detect_problems(current, baseline=None)

        findings = result.tables[0].findings
        if len(findings) >= 2:
            severities = [f.severity for f in findings]
            severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
            ordered = sorted(severities, key=lambda s: severity_order[s])
            assert severities == ordered
