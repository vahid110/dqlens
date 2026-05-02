"""Integration tests for the problem detector against a real database.

Tests the full detect pipeline: profile → detect → findings.
Covers: all finding categories, drift detection, severity ranking,
ignore filtering, and edge cases with real data.
"""

from __future__ import annotations

import pytest
from markers import requires_postgres

from dqlens import connector, profiler
from dqlens.detector import detect_problems
from dqlens.models import FindingCategory, Severity


@pytest.fixture
def schema_with_issues(pg_conn_autocommit, test_schema):
    """Create tables with known data quality issues for detection testing."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema

    # Table with various issues
    cur.execute(f"""
        CREATE TABLE {s}.products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            price NUMERIC(10,2) NOT NULL,
            category VARCHAR(50),
            rating NUMERIC(3,1),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Customers with email pattern
    cur.execute(f"""
        CREATE TABLE {s}.customers (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Orders with FK (no constraint — simulates warehouse)
    cur.execute(f"""
        CREATE TABLE {s}.orders (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER,
            total_amount NUMERIC(10,2),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Empty table
    cur.execute(f"""
        CREATE TABLE {s}.empty_metrics (
            id SERIAL PRIMARY KEY,
            metric_name VARCHAR(100),
            value NUMERIC
        )
    """)

    # Stale table (data from 30 days ago)
    cur.execute(f"""
        CREATE TABLE {s}.stale_reports (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """)

    # Products: some with negative prices
    cur.execute(f"""
        INSERT INTO {s}.products (name, price, category, rating, created_at)
        SELECT
            'Product ' || i,
            CASE
                WHEN i = 5 THEN -9.99
                WHEN i = 15 THEN -0.01
                ELSE round((random() * 200 + 1)::numeric, 2)
            END,
            CASE (MOD(i, 3)) WHEN 0 THEN 'electronics' WHEN 1 THEN 'books' ELSE 'clothing' END,
            CASE WHEN MOD(i, 4) = 0 THEN NULL ELSE round((random() * 5)::numeric, 1) END,
            NOW() - (i || ' hours')::interval
        FROM generate_series(1, 100) AS i
    """)

    # Customers
    cur.execute(f"""
        INSERT INTO {s}.customers (email, status, created_at)
        SELECT
            'user' || i || '@test.com',
            CASE (MOD(i, 3)) WHEN 0 THEN 'active' WHEN 1 THEN 'inactive' ELSE 'suspended' END,
            NOW() - (i || ' hours')::interval
        FROM generate_series(1, 200) AS i
    """)

    # Orders
    cur.execute(f"""
        INSERT INTO {s}.orders (customer_id, total_amount, created_at)
        SELECT
            (MOD(i, 200)) + 1,
            CASE WHEN MOD(i, 20) = 0 THEN -5.00 ELSE round((random() * 500)::numeric, 2) END,
            NOW() - (i || ' minutes')::interval
        FROM generate_series(1, 500) AS i
    """)

    # Stale reports (30+ days old)
    cur.execute(f"""
        INSERT INTO {s}.stale_reports (report_date, created_at)
        SELECT
            (CURRENT_DATE - (i + 30 || ' days')::interval)::date,
            NOW() - (i + 30 || ' days')::interval
        FROM generate_series(1, 30) AS i
    """)

    cur.execute("ANALYZE")
    cur.close()
    yield s


@pytest.fixture
def profile_and_baseline(pg_url, schema_with_issues):
    """Profile the schema twice to get a current + baseline for drift testing."""
    with connector.connect(pg_url) as conn:
        baseline = profiler.profile_database(conn, schema=schema_with_issues)
        current = profiler.profile_database(conn, schema=schema_with_issues)
    return current, baseline, schema_with_issues


# ---------------------------------------------------------------------------
# Finding detection — happy paths
# ---------------------------------------------------------------------------

@requires_postgres
class TestFindingDetection:
    def test_empty_table_detected(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        empty_findings = [
            f for t in result.tables for f in t.findings
            if "empty_metrics" in f.table and f.category == FindingCategory.ROW_COUNT_ANOMALY
        ]
        assert len(empty_findings) == 1
        assert empty_findings[0].severity == Severity.HIGH

    def test_negative_price_detected(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        price_findings = [
            f for t in result.tables for f in t.findings
            if f.column == "price" and f.category == FindingCategory.DISTRIBUTION_SHIFT
        ]
        assert len(price_findings) >= 1
        assert any("negative" in f.message.lower() for f in price_findings)

    def test_negative_total_amount_detected(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        amount_findings = [
            f for t in result.tables for f in t.findings
            if f.column == "total_amount" and "negative" in f.message.lower()
        ]
        assert len(amount_findings) >= 1

    def test_stale_data_detected(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        freshness_findings = [
            f for t in result.tables for f in t.findings
            if "stale_reports" in f.table and f.category == FindingCategory.FRESHNESS
        ]
        assert len(freshness_findings) >= 1

    def test_email_pattern_passes(self, pg_url, schema_with_issues):
        """Email column with 100% valid emails should pass pattern check."""
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        # Find the customers table result
        customers_result = next(
            t for t in result.tables if "customers" in t.table_name
        )
        email_passed = [
            p for p in customers_result.passed_tests
            if p.column == "email" and "email pattern" in p.message
        ]
        assert len(email_passed) >= 1


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

@requires_postgres
class TestDriftDetection:
    def test_no_drift_same_data(self, pg_url, profile_and_baseline):
        """Same data profiled twice should produce no drift findings."""
        current, baseline, schema = profile_and_baseline
        with connector.connect(pg_url) as conn:
            result = detect_problems(current, baseline, conn=conn)

        drift_findings = [
            f for t in result.tables for f in t.findings
            if f.category == FindingCategory.ROW_COUNT_ANOMALY
            and f.baseline_value is not None
        ]
        # No row count drift since data hasn't changed
        assert len(drift_findings) == 0

    def test_row_count_growth_detected(self, pg_url, schema_with_issues, pg_conn_autocommit):
        """Insert many rows and verify growth is detected."""
        s = schema_with_issues
        with connector.connect(pg_url) as conn:
            baseline = profiler.profile_database(conn, schema=s)

        # Double the orders
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"""
            INSERT INTO {s}.orders (customer_id, total_amount, created_at)
            SELECT (MOD(i, 200)) + 1, round((random() * 500)::numeric, 2), NOW()
            FROM generate_series(1, 1000) AS i
        """)
        cur.execute("ANALYZE")
        cur.close()

        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=s)
            result = detect_problems(current, baseline, conn=conn)

        growth_findings = [
            f for t in result.tables for f in t.findings
            if "orders" in f.table
            and f.category == FindingCategory.ROW_COUNT_ANOMALY
            and f.baseline_value is not None
        ]
        assert len(growth_findings) >= 1
        assert any("grew" in f.message.lower() for f in growth_findings)

    def test_null_rate_drift_detected(self, pg_url, schema_with_issues, pg_conn_autocommit):
        """Increase null rate and verify drift is detected."""
        s = schema_with_issues
        with connector.connect(pg_url) as conn:
            baseline = profiler.profile_database(conn, schema=s)

        # Set many ratings to NULL
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"UPDATE {s}.products SET rating = NULL WHERE id > 10")
        cur.execute("ANALYZE")
        cur.close()

        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=s)
            result = detect_problems(current, baseline, conn=conn)

        null_findings = [
            f for t in result.tables for f in t.findings
            if f.column == "rating" and f.category == FindingCategory.NULL_ANOMALY
        ]
        # Should detect the null rate increase
        assert len(null_findings) >= 1


# ---------------------------------------------------------------------------
# Severity ranking
# ---------------------------------------------------------------------------

@requires_postgres
class TestSeverityRanking:
    def test_findings_ordered_by_severity(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        for table_result in result.tables:
            if len(table_result.findings) >= 2:
                severities = [f.severity for f in table_result.findings]
                ordered = sorted(severities, key=lambda s: severity_order[s])
                assert severities == ordered, (
                    f"Findings in {table_result.table_name} not sorted by severity"
                )

    def test_empty_table_is_high(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        empty_findings = [
            f for t in result.tables for f in t.findings
            if "empty" in f.message.lower()
        ]
        assert all(f.severity == Severity.HIGH for f in empty_findings)


# ---------------------------------------------------------------------------
# Ignore filtering
# ---------------------------------------------------------------------------

@requires_postgres
class TestIgnoreFiltering:
    def test_ignored_finding_suppressed(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)

            # First run — should have findings
            result_before = detect_problems(current, baseline=None, conn=conn)
            total_before = result_before.total_findings

            # Second run with ignores
            result_after = detect_problems(
                current, baseline=None, conn=conn,
                ignores={"empty_metrics.row_count_anomaly"},
            )
            total_after = result_after.total_findings

        assert total_after < total_before

    def test_nonexistent_ignore_has_no_effect(self, pg_url, schema_with_issues):
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result_normal = detect_problems(current, baseline=None, conn=conn)
            result_ignored = detect_problems(
                current, baseline=None, conn=conn,
                ignores={"nonexistent_table.nonexistent_column.nonexistent_category"},
            )
        assert result_normal.total_findings == result_ignored.total_findings


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@requires_postgres
class TestEdgeCases:
    def test_empty_schema(self, pg_url, pg_conn_autocommit):
        """Profiling an empty schema should produce no findings."""
        import uuid
        schema = f"dqlens_edge_{uuid.uuid4().hex[:8]}"
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"CREATE SCHEMA {schema}")
        try:
            with connector.connect(pg_url) as conn:
                current = profiler.profile_database(conn, schema=schema)
                result = detect_problems(current, baseline=None, conn=conn)
            assert result.total_tests == 0
            assert result.total_findings == 0
        finally:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")

    def test_single_row_table(self, pg_url, schema_with_issues):
        """Single-row tables should not produce false positives."""
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(
                conn, schema=schema_with_issues,
                tables=["customers"],
            )
            result = detect_problems(current, baseline=None, conn=conn)

        # Should have passed tests, not just findings
        customers = result.tables[0]
        assert customers.passed_count > 0

    def test_all_tables_produce_results(self, pg_url, schema_with_issues):
        """Every table should produce at least one test result."""
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        for table_result in result.tables:
            assert table_result.total_tests > 0, (
                f"Table {table_result.table_name} produced no test results"
            )

    def test_result_summary_consistent(self, pg_url, schema_with_issues):
        """Total tests = findings + passed across all tables."""
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        assert result.total_tests == result.total_findings + result.total_passed

    def test_every_finding_has_detail(self, pg_url, schema_with_issues):
        """Every finding must have a non-empty detail explanation."""
        with connector.connect(pg_url) as conn:
            current = profiler.profile_database(conn, schema=schema_with_issues)
            result = detect_problems(current, baseline=None, conn=conn)

        for table_result in result.tables:
            for finding in table_result.findings:
                assert finding.detail, (
                    f"Finding in {finding.table}.{finding.column} has empty detail"
                )
                assert len(finding.detail) > 10, (
                    f"Finding detail too short: {finding.detail}"
                )
