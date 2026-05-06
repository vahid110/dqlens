"""Integration tests for DuckDB connector.

No external service needed. DuckDB is embedded, so these tests
create a local file, seed it, and run DQLens against it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

pytestmark = pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb not installed")


@pytest.fixture
def duckdb_path(tmp_path):
    """Create a seeded DuckDB database for testing."""
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)

    conn.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            email VARCHAR,
            phone VARCHAR,
            signup_source VARCHAR,
            created_at TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT INTO customers VALUES
        (1, 'Alice', 'alice@example.com', '555-0101', 'web', '2026-01-01'),
        (2, 'Bob', 'bob@example.com', '', 'mobile', '2026-01-02'),
        (3, 'Charlie', NULL, '555-0103', 'web', '2026-01-03'),
        (4, 'Dave', 'dave@example.com', '555-0104', 'referral', '2026-01-04'),
        (5, 'Eve', 'not-an-email', '', 'web', '2026-01-05')
    """)

    conn.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            amount DOUBLE NOT NULL,
            status VARCHAR NOT NULL,
            created_at TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT INTO orders VALUES
        (1, 1, 99.50, 'completed', '2026-01-10'),
        (2, 2, 150.00, 'completed', '2026-01-11'),
        (3, 3, 75.00, 'pending', '2026-01-12'),
        (4, 999, 200.00, 'completed', '2026-01-13'),
        (5, 1, 50.00, 'cancelled', '2026-01-14'),
        (6, 4, 300.00, 'completed', '2026-01-15'),
        (7, 2, 25.00, 'pending', '2026-01-16'),
        (8, 5, 1000.00, 'completed', '2026-01-17')
    """)

    conn.close()
    return db_path


class TestDuckDBConnector:
    def test_list_tables(self, duckdb_path):
        from dqlens.connectors.factory import get_connector

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            tables = connector.list_tables(conn, "main")

        names = [t["table_name"] for t in tables]
        assert "customers" in names
        assert "orders" in names

    def test_get_columns(self, duckdb_path):
        from dqlens.connectors.factory import get_connector

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            cols = connector.get_columns(conn, "main", "customers")

        col_names = [c["column_name"] for c in cols]
        assert "id" in col_names
        assert "email" in col_names
        assert "name" in col_names

    def test_column_details_numeric(self, duckdb_path):
        from dqlens.connectors.factory import get_connector

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            details = connector.get_column_details(conn, "main", "orders", "amount", "double")

        assert details["total"] == 8
        assert details["null_count"] == 0
        assert details["min_value"] == 25.0
        assert details["max_value"] == 1000.0
        assert details["p25"] is not None
        assert details["p75"] is not None

    def test_column_details_text_empty_strings(self, duckdb_path):
        from dqlens.connectors.factory import get_connector

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            details = connector.get_column_details(conn, "main", "customers", "phone", "varchar")

        assert details["empty_string_count"] == 2

    def test_full_profile(self, duckdb_path):
        from dqlens.connectors.factory import get_connector
        from dqlens.profiler_v2 import profile_database

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            profile = profile_database(db=connector, conn=conn, schema="main")

        assert len(profile.tables) == 2
        orders = next(t for t in profile.tables if t.table_name == "orders")
        assert orders.row_count == 8

        amount_col = orders.get_column("amount")
        assert amount_col is not None
        assert amount_col.p25 is not None
        assert amount_col.p75 is not None

    def test_fk_integrity(self, duckdb_path):
        from dqlens.connectors.factory import get_connector

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            result = connector.check_fk_integrity(
                conn, "main", "orders", "customer_id", "customers", "id"
            )

        assert result["orphaned"] == 1  # customer_id=999 doesn't exist

    def test_full_run_finds_problems(self, duckdb_path):
        from dqlens.connectors.factory import get_connector
        from dqlens.engine import run_checks
        from dqlens.profiler_v2 import profile_database

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            profile = profile_database(db=connector, conn=conn, schema="main")
            results = run_checks(profile, baseline=None, conn=conn)

        # Should find problems (pattern violations, null anomalies, etc.)
        assert results.total_findings > 0

    def test_quick_mode(self, duckdb_path):
        from dqlens.connectors.factory import get_connector
        from dqlens.profiler_v2 import profile_database

        connector = get_connector(duckdb_path)
        with connector.connect() as conn:
            profile = profile_database(db=connector, conn=conn, schema="main", quick=True)

        assert len(profile.tables) == 2
        # Quick mode should still produce results (small table, samples everything)
        assert profile.tables[0].row_count > 0
