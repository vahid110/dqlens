"""Unit tests for the connector abstraction layer.

Tests the factory, base interface, and SQLite connector without
needing any external database.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from dqlens.connectors.base import BaseConnector
from dqlens.connectors.factory import get_connector
from dqlens.connectors.sqlite import SQLiteConnector

# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestGetConnector:
    def test_postgresql_url(self):
        c = get_connector("postgresql://user:pass@localhost:5432/db")
        assert c.__class__.__name__ == "PostgreSQLConnector"

    def test_postgres_url(self):
        c = get_connector("postgres://user:pass@localhost/db")
        assert c.__class__.__name__ == "PostgreSQLConnector"

    def test_sqlite_url(self):
        c = get_connector("sqlite:///path/to/db.sqlite")
        assert isinstance(c, SQLiteConnector)

    def test_sqlite_file_path_db(self):
        c = get_connector("/tmp/test.db")
        assert isinstance(c, SQLiteConnector)

    def test_sqlite_file_path_sqlite(self):
        c = get_connector("data.sqlite")
        assert isinstance(c, SQLiteConnector)

    def test_sqlite_file_path_sqlite3(self):
        c = get_connector("data.sqlite3")
        assert isinstance(c, SQLiteConnector)

    def test_unsupported_scheme(self):
        with pytest.raises(ValueError, match="Unsupported"):
            get_connector("mongodb://localhost/db")

    def test_mysql_url(self):
        c = get_connector("mysql://localhost/db")
        assert c.__class__.__name__ == "MySQLConnector"

    def test_whitespace_stripped(self):
        c = get_connector("  postgresql://localhost/db  ")
        assert c.__class__.__name__ == "PostgreSQLConnector"


# ---------------------------------------------------------------------------
# SQLite connector unit tests (uses temp files, no external DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_db(tmp_path):
    """Create a temporary SQLite database with test data."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            age INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("CREATE TABLE empty_table (id INTEGER PRIMARY KEY, data TEXT)")

    for i in range(1, 101):
        age = None if i % 10 == 0 else 20 + (i % 40)
        cur.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, datetime('now', ? || ' hours'))",
            (i, f"user{i}@test.com", f"User {i}", age, f"-{i}"),
        )

    for i in range(1, 301):
        amount = -5.0 if i % 50 == 0 else round(10 + i * 0.3, 2)
        status = ["pending", "shipped", "delivered", "cancelled"][i % 4]
        cur.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, datetime('now', ? || ' minutes'))",
            (i, (i % 100) + 1, amount, status, f"-{i}"),
        )

    conn.commit()
    conn.close()
    return db_path


class TestSQLiteConnectorConnect:
    def test_connect_success(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            cur = conn.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_connect_nonexistent_creates_file(self, tmp_path):
        """SQLite creates the file on connect — this is expected behavior."""
        db_path = str(tmp_path / "new.db")
        c = SQLiteConnector(db_path)
        with c.connect() as conn:
            conn.execute("SELECT 1")
        assert Path(db_path).exists()


class TestSQLiteListTables:
    def test_lists_tables(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            tables = c.list_tables(conn, "main")
            names = [t["table_name"] for t in tables]
            assert "users" in names
            assert "orders" in names
            assert "empty_table" in names

    def test_row_counts(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            tables = c.list_tables(conn, "main")
            users = next(t for t in tables if t["table_name"] == "users")
            assert users["estimated_rows"] == 100
            empty = next(t for t in tables if t["table_name"] == "empty_table")
            assert empty["estimated_rows"] == 0


class TestSQLiteGetColumns:
    def test_column_metadata(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            cols = c.get_columns(conn, "main", "users")
            names = [col["column_name"] for col in cols]
            assert "id" in names
            assert "email" in names
            assert "age" in names

    def test_nullable_detection(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            cols = c.get_columns(conn, "main", "users")
            col_map = {col["column_name"]: col for col in cols}
            assert col_map["email"]["is_nullable"] == "NO"
            assert col_map["age"]["is_nullable"] == "YES"

    def test_empty_table_columns(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            cols = c.get_columns(conn, "main", "empty_table")
            assert len(cols) == 2


class TestSQLitePrimaryKeys:
    def test_discovers_pks(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            pks = c.get_primary_keys(conn, "main")
            assert "users" in pks
            assert "id" in pks["users"]


class TestSQLiteForeignKeys:
    def test_discovers_fks(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            fks = c.get_foreign_keys(conn, "main")
            order_fks = [fk for fk in fks if fk["source_table"] == "orders"]
            assert len(order_fks) >= 1
            assert order_fks[0]["source_column"] == "user_id"
            assert order_fks[0]["target_table"] == "users"


class TestSQLiteColumnDetails:
    def test_numeric_stats(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            details = c.get_column_details(conn, "main", "users", "age", "integer")
            assert details["total"] == 100
            assert details["null_count"] == 10  # every 10th
            assert details["min_value"] is not None
            assert details["max_value"] is not None

    def test_text_stats(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            details = c.get_column_details(conn, "main", "users", "email", "text")
            assert details["total"] == 100
            assert details["null_count"] == 0
            assert details["distinct_count"] == 100

    def test_empty_table_stats(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            details = c.get_column_details(conn, "main", "empty_table", "data", "text")
            assert details["total"] == 0
            assert details["null_count"] == 0


class TestSQLiteRowCount:
    def test_exact_count(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            assert c.get_exact_row_count(conn, "main", "users") == 100
            assert c.get_exact_row_count(conn, "main", "orders") == 300
            assert c.get_exact_row_count(conn, "main", "empty_table") == 0


class TestSQLiteTimestamps:
    def test_finds_timestamp_columns(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            ts_cols = c.get_timestamp_columns(conn, "main", "users")
            assert "created_at" in ts_cols

    def test_latest_timestamp(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            latest = c.get_latest_timestamp(conn, "main", "users", "created_at")
            assert latest is not None

    def test_empty_table_timestamp(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            latest = c.get_latest_timestamp(conn, "main", "empty_table", "data")
            assert latest is None


class TestSQLiteFKIntegrity:
    def test_valid_fks(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            result = c.check_fk_integrity(conn, "main", "orders", "user_id", "users", "id")
            assert result["orphaned"] == 0
            assert result["non_null"] == 300

    def test_orphaned_rows(self, sqlite_db):
        """Insert orphaned rows and verify detection."""
        conn = sqlite3.connect(sqlite_db)
        conn.execute("INSERT INTO orders VALUES (999, 9999, 10.0, 'pending', '2026-01-01')")
        conn.commit()
        conn.close()

        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            result = c.check_fk_integrity(conn, "main", "orders", "user_id", "users", "id")
            assert result["orphaned"] == 1


class TestSQLiteSampleValues:
    def test_samples_text(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            values = c.sample_text_values(conn, "main", "users", "email", limit=10)
            assert len(values) == 10
            assert all("@" in v for v in values)

    def test_sample_empty(self, sqlite_db):
        c = SQLiteConnector(sqlite_db)
        with c.connect() as conn:
            values = c.sample_text_values(conn, "main", "empty_table", "data", limit=10)
            assert values == []


class TestSQLiteTypeDetection:
    def test_numeric_types(self):
        c = SQLiteConnector("")
        assert c.is_numeric_type("integer") is True
        assert c.is_numeric_type("real") is True
        assert c.is_numeric_type("text") is False

    def test_text_types(self):
        c = SQLiteConnector("")
        assert c.is_text_type("text") is True
        assert c.is_text_type("varchar") is True
        assert c.is_text_type("integer") is False

    def test_temporal_types(self):
        c = SQLiteConnector("")
        assert c.is_temporal_type("datetime") is True
        assert c.is_temporal_type("timestamp") is True
        assert c.is_temporal_type("text") is False
