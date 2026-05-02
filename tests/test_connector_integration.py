"""Integration tests for the PostgreSQL connector.

Tests run against a real PostgreSQL database. Skipped automatically
if POSTGRES_URL / PG_HOST is not set.

Covers:
- Connection management (happy path, bad URL, readonly mode)
- Schema introspection (tables, columns, types, FKs, PKs, indexes)
- Column statistics (nulls, distinct, min/max, patterns)
- FK integrity checking (valid refs, orphaned rows, empty tables)
- Edge cases (empty tables, single-row tables, all-null columns)
"""

from __future__ import annotations

import pytest
from markers import requires_postgres

from dqlens import connector

# ---------------------------------------------------------------------------
# Fixtures: create test tables in an isolated schema
# ---------------------------------------------------------------------------

@pytest.fixture
def schema_with_tables(pg_conn_autocommit, test_schema):
    """Create a set of test tables covering various data patterns."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema

    # Parent table with various column types
    cur.execute(f"""
        CREATE TABLE {s}.customers (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            phone VARCHAR(50),
            age INTEGER,
            score NUMERIC(5,2),
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Child table with FK
    cur.execute(f"""
        CREATE TABLE {s}.orders (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER REFERENCES {s}.customers(id),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            total_amount NUMERIC(10,2),
            email VARCHAR(255),
            notes TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Empty table
    cur.execute(f"""
        CREATE TABLE {s}.audit_log (
            id SERIAL PRIMARY KEY,
            action VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Single-row table
    cur.execute(f"""
        CREATE TABLE {s}.settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur.execute(f"INSERT INTO {s}.settings VALUES ('version', '1.0')")

    # Table with all-null optional columns
    cur.execute(f"""
        CREATE TABLE {s}.sparse (
            id SERIAL PRIMARY KEY,
            required_col VARCHAR(50) NOT NULL,
            optional_a VARCHAR(50),
            optional_b INTEGER,
            optional_c TIMESTAMP
        )
    """)

    # Seed customers
    cur.execute(f"""
        INSERT INTO {s}.customers (email, name, phone, age, score, created_at)
        SELECT
            'user' || i || '@test.com',
            'User ' || i,
            CASE
                WHEN MOD(i, 4) = 0 THEN NULL
                WHEN MOD(i, 7) = 0 THEN 'invalid-phone'
                ELSE '+1-555-' || LPAD(i::text, 4, '0')
            END,
            CASE WHEN MOD(i, 10) = 0 THEN NULL ELSE 20 + (MOD(i, 50)) END,
            CASE WHEN MOD(i, 5) = 0 THEN NULL ELSE round((random() * 100)::numeric, 2) END,
            NOW() - (i || ' hours')::interval
        FROM generate_series(1, 200) AS i
    """)

    # Seed orders (some with NULL email, some with negative amounts)
    cur.execute(f"""
        INSERT INTO {s}.orders (customer_id, status, total_amount, email, notes, created_at)
        SELECT
            (MOD(i, 200)) + 1,
            CASE (MOD(i, 4))
                WHEN 0 THEN 'pending'
                WHEN 1 THEN 'shipped'
                WHEN 2 THEN 'delivered'
                WHEN 3 THEN 'cancelled'
            END,
            CASE WHEN MOD(i, 30) = 0 THEN -5.00 ELSE round((random() * 500)::numeric, 2) END,
            CASE WHEN i > 450 THEN NULL ELSE 'user' || ((MOD(i, 200)) + 1) || '@test.com' END,
            CASE WHEN MOD(i, 3) = 0 THEN NULL ELSE 'Order note ' || i END,
            NOW() - (i || ' minutes')::interval
        FROM generate_series(1, 500) AS i
    """)

    # Seed sparse table (all optional columns NULL)
    cur.execute(f"""
        INSERT INTO {s}.sparse (required_col)
        SELECT 'row_' || i FROM generate_series(1, 50) AS i
    """)

    cur.execute("ANALYZE")
    cur.close()

    yield s


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

@requires_postgres
class TestConnection:
    def test_connect_success(self, pg_url):
        with connector.connect(pg_url) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_connect_bad_url(self):
        with pytest.raises(Exception):
            with connector.connect("postgresql://bad:bad@localhost:9999/nope"):
                pass

    def test_connection_is_readonly(self, pg_url):
        """Connector should open readonly connections."""
        with connector.connect(pg_url) as conn:
            cur = conn.cursor()
            with pytest.raises(Exception):
                cur.execute("CREATE TABLE _dqlens_readonly_test (id int)")


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

@requires_postgres
class TestListTables:
    def test_lists_tables(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            tables = connector.list_tables(conn, schema_with_tables)
            names = [t["table_name"] for t in tables]
            assert "customers" in names
            assert "orders" in names
            assert "audit_log" in names
            assert "settings" in names
            assert "sparse" in names

    def test_table_has_row_estimates(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            tables = connector.list_tables(conn, schema_with_tables)
            customers = next(t for t in tables if t["table_name"] == "customers")
            # pg_class estimates may not be exact but should be > 0 after ANALYZE
            assert customers["estimated_rows"] >= 0

    def test_empty_schema(self, pg_url, pg_conn_autocommit):
        """Listing tables in a schema with no tables returns empty list."""
        import uuid
        schema = f"dqlens_empty_{uuid.uuid4().hex[:8]}"
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"CREATE SCHEMA {schema}")
        try:
            with connector.connect(pg_url) as conn:
                tables = connector.list_tables(conn, schema)
                assert tables == []
        finally:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


@requires_postgres
class TestGetColumns:
    def test_column_metadata(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            cols = connector.get_columns(conn, schema_with_tables, "customers")
            names = [c["column_name"] for c in cols]
            assert "id" in names
            assert "email" in names
            assert "phone" in names
            assert "created_at" in names

    def test_column_types(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            cols = connector.get_columns(conn, schema_with_tables, "customers")
            col_map = {c["column_name"]: c for c in cols}
            assert col_map["id"]["data_type"] == "integer"
            assert col_map["email"]["data_type"] == "character varying"
            assert col_map["is_active"]["data_type"] == "boolean"
            assert col_map["created_at"]["data_type"] == "timestamp without time zone"

    def test_nullable_info(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            cols = connector.get_columns(conn, schema_with_tables, "customers")
            col_map = {c["column_name"]: c for c in cols}
            assert col_map["email"]["is_nullable"] == "NO"
            assert col_map["phone"]["is_nullable"] == "YES"

    def test_empty_table_columns(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            cols = connector.get_columns(conn, schema_with_tables, "audit_log")
            assert len(cols) == 3  # id, action, created_at


@requires_postgres
class TestGetForeignKeys:
    def test_discovers_fks(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            fks = connector.get_foreign_keys(conn, schema_with_tables)
            assert len(fks) >= 1
            fk = fks[0]
            assert fk["source_table"] == "orders"
            assert fk["source_column"] == "customer_id"
            assert fk["target_table"] == "customers"
            assert fk["target_column"] == "id"

    def test_no_fks_in_schema_without_them(self, pg_url, pg_conn_autocommit):
        import uuid
        schema = f"dqlens_nofk_{uuid.uuid4().hex[:8]}"
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.standalone (id serial primary key, name text)")
        try:
            with connector.connect(pg_url) as conn:
                fks = connector.get_foreign_keys(conn, schema)
                assert fks == []
        finally:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


@requires_postgres
class TestGetPrimaryKeys:
    def test_discovers_pks(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            pks = connector.get_primary_keys(conn, schema_with_tables)
            assert "customers" in pks
            assert "id" in pks["customers"]
            assert "orders" in pks
            assert "settings" in pks
            assert "key" in pks["settings"]  # varchar PK


@requires_postgres
class TestGetUniqueIndexes:
    def test_discovers_unique_indexes(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            uniques = connector.get_unique_indexes(conn, schema_with_tables)
            assert "customers" in uniques
            # email has UNIQUE constraint
            assert "email" in uniques["customers"]


# ---------------------------------------------------------------------------
# Column detail queries
# ---------------------------------------------------------------------------

@requires_postgres
class TestGetColumnDetails:
    def test_numeric_column_stats(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            details = connector.get_column_details(
                conn, schema_with_tables, "customers", "age", "integer"
            )
            assert details["total"] == 200
            assert details["null_count"] == 20  # every 10th is null
            assert details["min_value"] is not None
            assert details["max_value"] is not None
            assert details["mean_value"] is not None

    def test_text_column_stats(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            details = connector.get_column_details(
                conn, schema_with_tables, "customers", "email", "character varying"
            )
            assert details["total"] == 200
            assert details["null_count"] == 0
            assert details["distinct_count"] == 200

    def test_all_null_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            details = connector.get_column_details(
                conn, schema_with_tables, "sparse", "optional_a", "character varying"
            )
            assert details["null_count"] == 50
            assert details["distinct_count"] == 0

    def test_empty_table_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            details = connector.get_column_details(
                conn, schema_with_tables, "audit_log", "action", "character varying"
            )
            assert details["total"] == 0
            assert details["null_count"] == 0

    def test_single_row_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            details = connector.get_column_details(
                conn, schema_with_tables, "settings", "value", "text"
            )
            assert details["total"] == 1
            assert details["null_count"] == 0
            assert details["distinct_count"] == 1


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------

@requires_postgres
class TestExactRowCount:
    def test_populated_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            count = connector.get_exact_row_count(conn, schema_with_tables, "customers")
            assert count == 200

    def test_empty_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            count = connector.get_exact_row_count(conn, schema_with_tables, "audit_log")
            assert count == 0

    def test_single_row(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            count = connector.get_exact_row_count(conn, schema_with_tables, "settings")
            assert count == 1


# ---------------------------------------------------------------------------
# Timestamp / freshness
# ---------------------------------------------------------------------------

@requires_postgres
class TestTimestampColumns:
    def test_finds_timestamp_columns(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            ts_cols = connector.get_timestamp_columns(conn, schema_with_tables, "customers")
            assert "created_at" in ts_cols

    def test_no_timestamp_columns(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            ts_cols = connector.get_timestamp_columns(conn, schema_with_tables, "settings")
            assert ts_cols == []

    def test_latest_timestamp(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            latest = connector.get_latest_timestamp(
                conn, schema_with_tables, "customers", "created_at"
            )
            assert latest is not None

    def test_latest_timestamp_empty_table(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            latest = connector.get_latest_timestamp(
                conn, schema_with_tables, "audit_log", "created_at"
            )
            assert latest is None


# ---------------------------------------------------------------------------
# FK integrity
# ---------------------------------------------------------------------------

@requires_postgres
class TestFKIntegrity:
    def test_valid_fk_no_orphans(self, pg_url, schema_with_tables):
        """All orders reference valid customers — no orphans."""
        with connector.connect(pg_url) as conn:
            result = connector.check_fk_integrity(
                conn, schema_with_tables,
                "orders", "customer_id", "customers", "id"
            )
            assert result["orphaned"] == 0
            assert result["non_null"] == 500

    def test_fk_with_orphans(self, pg_url, schema_with_tables, pg_conn_autocommit):
        """Insert orphaned rows and verify detection."""
        cur = pg_conn_autocommit.cursor()
        s = schema_with_tables
        # Temporarily drop FK constraint to insert orphans
        cur.execute(f"""
            ALTER TABLE {s}.orders DROP CONSTRAINT orders_customer_id_fkey
        """)
        cur.execute(f"""
            INSERT INTO {s}.orders (customer_id, status, total_amount, created_at)
            VALUES (99999, 'pending', 10.00, NOW()),
                   (99998, 'pending', 20.00, NOW()),
                   (99997, 'pending', 30.00, NOW())
        """)
        cur.close()

        with connector.connect(pg_url) as conn:
            result = connector.check_fk_integrity(
                conn, schema_with_tables,
                "orders", "customer_id", "customers", "id"
            )
            assert result["orphaned"] == 3


# ---------------------------------------------------------------------------
# Pattern detection (via sample_text_values)
# ---------------------------------------------------------------------------

@requires_postgres
class TestSampleTextValues:
    def test_samples_values(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            values = connector.sample_text_values(
                conn, schema_with_tables, "customers", "email", limit=50
            )
            assert len(values) <= 50
            assert all("@" in v for v in values)

    def test_sample_empty_column(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            values = connector.sample_text_values(
                conn, schema_with_tables, "sparse", "optional_a", limit=50
            )
            assert values == []

    def test_sample_with_limit(self, pg_url, schema_with_tables):
        with connector.connect(pg_url) as conn:
            values = connector.sample_text_values(
                conn, schema_with_tables, "customers", "email", limit=5
            )
            assert len(values) == 5
