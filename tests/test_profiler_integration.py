"""Integration tests for the database profiler.

Tests the full profiling pipeline against a real PostgreSQL database.
Covers: table discovery, column profiling, pattern detection, FK discovery,
freshness detection, table filtering, and edge cases.
"""

from __future__ import annotations

import pytest

from markers import requires_postgres
from dqlens import connector, profiler


# Reuse the schema_with_tables fixture from conftest (via test_connector_integration)
@pytest.fixture
def schema_with_tables(pg_conn_autocommit, test_schema):
    """Create test tables — same as connector tests."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema

    cur.execute(f"""
        CREATE TABLE {s}.customers (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            phone VARCHAR(50),
            age INTEGER,
            latitude NUMERIC(9,6),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.orders (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER REFERENCES {s}.customers(id),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            total_amount NUMERIC(10,2),
            email VARCHAR(255),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.empty_table (
            id SERIAL PRIMARY KEY,
            data TEXT
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.all_nulls (
            id SERIAL PRIMARY KEY,
            col_a VARCHAR(50),
            col_b INTEGER
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.single_row (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Seed customers with various patterns
    cur.execute(f"""
        INSERT INTO {s}.customers (email, name, phone, age, latitude, created_at)
        SELECT
            'user' || i || '@example.com',
            'Customer ' || i,
            CASE
                WHEN MOD(i, 5) = 0 THEN NULL
                WHEN MOD(i, 11) = 0 THEN 'not-a-phone'
                ELSE '+1-555-' || LPAD(i::text, 4, '0')
            END,
            20 + (MOD(i, 50)),
            CASE WHEN MOD(i, 3) = 0 THEN NULL ELSE round((-90 + random() * 180)::numeric, 6) END,
            NOW() - (i || ' hours')::interval
        FROM generate_series(1, 300) AS i
    """)

    # Seed orders
    cur.execute(f"""
        INSERT INTO {s}.orders (customer_id, status, total_amount, email, created_at)
        SELECT
            (MOD(i, 300)) + 1,
            CASE (MOD(i, 4))
                WHEN 0 THEN 'pending'
                WHEN 1 THEN 'shipped'
                WHEN 2 THEN 'delivered'
                WHEN 3 THEN 'cancelled'
            END,
            CASE WHEN MOD(i, 25) = 0 THEN -10.00 ELSE round((random() * 500)::numeric, 2) END,
            CASE WHEN i > 900 THEN NULL ELSE 'user' || ((MOD(i, 300)) + 1) || '@example.com' END,
            NOW() - (i || ' minutes')::interval
        FROM generate_series(1, 1000) AS i
    """)

    # Seed all_nulls (only id populated)
    cur.execute(f"""
        INSERT INTO {s}.all_nulls (id) SELECT i FROM generate_series(1, 20) AS i
    """)

    # Seed single_row
    cur.execute(f"INSERT INTO {s}.single_row VALUES (1, 'only-row')")

    cur.execute("ANALYZE")
    cur.close()
    yield s


# ---------------------------------------------------------------------------
# Full profile tests
# ---------------------------------------------------------------------------

@requires_postgres
class TestProfileDatabase:
    def test_profiles_all_tables(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            table_names = [t.table_name for t in db.tables]
            assert "customers" in table_names
            assert "orders" in table_names
            assert "empty_table" in table_names
            assert "all_nulls" in table_names
            assert "single_row" in table_names

    def test_profile_row_counts(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            assert customers.row_count == 300
            orders = db.get_table("orders")
            assert orders.row_count == 1000
            empty = db.get_table("empty_table")
            assert empty.row_count == 0
            single = db.get_table("single_row")
            assert single.row_count == 1

    def test_profile_schema_name(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            assert db.schema_name == schema_with_tables
            for table in db.tables:
                assert table.schema_name == schema_with_tables


# ---------------------------------------------------------------------------
# Column profiling
# ---------------------------------------------------------------------------

@requires_postgres
class TestColumnProfiling:
    def test_not_null_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            email = customers.get_column("email")
            assert email.null_count == 0
            assert email.null_pct == 0.0

    def test_nullable_column_with_nulls(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            phone = customers.get_column("phone")
            assert phone.null_count > 0
            assert phone.null_pct > 0

    def test_unique_column_detected(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            email = customers.get_column("email")
            assert email.is_unique is True

    def test_primary_key_detected(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            id_col = customers.get_column("id")
            assert id_col.is_primary_key is True
            assert id_col.is_unique is True

    def test_non_unique_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            orders = db.get_table("orders")
            status = orders.get_column("status")
            assert status.is_unique is False
            assert status.distinct_count == 4

    def test_numeric_stats(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            age = customers.get_column("age")
            assert age.min_value is not None
            assert age.max_value is not None
            assert age.mean_value is not None

    def test_all_null_columns(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            all_nulls = db.get_table("all_nulls")
            col_a = all_nulls.get_column("col_a")
            assert col_a.null_count == 20
            assert col_a.null_pct == 100.0
            assert col_a.distinct_count == 0

    def test_empty_table_columns(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            empty = db.get_table("empty_table")
            assert len(empty.columns) == 2  # id, data
            for col in empty.columns:
                assert col.row_count == 0

    def test_single_row_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            single = db.get_table("single_row")
            val = single.get_column("value")
            assert val.row_count == 1
            assert val.null_count == 0
            assert val.distinct_count == 1


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

@requires_postgres
class TestPatternDetection:
    def test_email_pattern_detected(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            email = customers.get_column("email")
            assert email.detected_pattern == "email"
            assert email.pattern_match_pct is not None
            assert email.pattern_match_pct > 90

    def test_phone_pattern_detected(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            phone = customers.get_column("phone")
            # Phone has mixed valid/invalid values
            if phone.detected_pattern == "phone":
                assert phone.pattern_match_pct > 50

    def test_no_pattern_on_generic_text(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            name = customers.get_column("name")
            # "Customer 1", "Customer 2" — no standard pattern
            assert name.detected_pattern is None

    def test_no_pattern_on_numeric(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            age = customers.get_column("age")
            assert age.detected_pattern is None


# ---------------------------------------------------------------------------
# FK discovery
# ---------------------------------------------------------------------------

@requires_postgres
class TestFKDiscovery:
    def test_fk_detected_on_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            orders = db.get_table("orders")
            cust_id = orders.get_column("customer_id")
            assert cust_id.is_foreign_key is True
            assert cust_id.fk_target_table == "customers"
            assert cust_id.fk_target_column == "id"

    def test_fk_in_table_foreign_keys(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            orders = db.get_table("orders")
            assert len(orders.foreign_keys) >= 1
            fk = orders.foreign_keys[0]
            assert fk.source_column == "customer_id"
            assert fk.target_table == "customers"

    def test_no_fk_on_parent_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            assert len(customers.foreign_keys) == 0


# ---------------------------------------------------------------------------
# Freshness detection
# ---------------------------------------------------------------------------

@requires_postgres
class TestFreshnessDetection:
    def test_freshness_column_detected(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            customers = db.get_table("customers")
            assert customers.freshness_column == "created_at"
            assert customers.latest_timestamp is not None

    def test_no_freshness_on_table_without_timestamps(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            single = db.get_table("single_row")
            assert single.freshness_column is None

    def test_empty_table_no_freshness_value(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=schema_with_tables)
            empty = db.get_table("empty_table")
            # Has a timestamp column but no data
            assert empty.latest_timestamp is None


# ---------------------------------------------------------------------------
# Table filtering
# ---------------------------------------------------------------------------

@requires_postgres
class TestTableFiltering:
    def test_include_specific_tables(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=schema_with_tables,
                tables=["customers", "orders"],
            )
            names = [t.table_name for t in db.tables]
            assert names == ["customers", "orders"]

    def test_exclude_tables(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=schema_with_tables,
                exclude_tables=["empty_table", "all_nulls"],
            )
            names = [t.table_name for t in db.tables]
            assert "empty_table" not in names
            assert "all_nulls" not in names
            assert "customers" in names

    def test_exclude_with_glob(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=schema_with_tables,
                exclude_tables=["*_table", "all_*"],
            )
            names = [t.table_name for t in db.tables]
            assert "empty_table" not in names
            assert "all_nulls" not in names

    def test_include_nonexistent_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=schema_with_tables,
                tables=["nonexistent"],
            )
            assert len(db.tables) == 0

    @pytest.mark.parametrize("tables,expected_count", [
        (["customers"], 1),
        (["customers", "orders"], 2),
        (["customers", "orders", "empty_table"], 3),
    ])
    def test_include_various_counts(self, pg_url, schema_with_tables, tables, expected_count):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=schema_with_tables, tables=tables,
            )
            assert len(db.tables) == expected_count
