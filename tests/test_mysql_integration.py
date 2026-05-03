"""Integration tests for the MySQL connector against a real MySQL database.

Skipped automatically if MYSQL_HOST is not set or MySQL is unreachable.
"""

from __future__ import annotations

import os
import uuid

import pytest
from markers import requires_postgres  # reuse env loading


# Build MySQL skip marker
def _mysql_available() -> bool:
    host = os.environ.get("MYSQL_HOST")
    if not host:
        return False
    try:
        import pymysql
        conn = pymysql.connect(
            host=host,
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            database=os.environ.get("MYSQL_DATABASE", "dev"),
            user=os.environ.get("MYSQL_USER", "root"),
            password=os.environ.get("MYSQL_PASS", ""),
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return True
    except Exception:
        return False


requires_mysql = pytest.mark.skipif(
    not _mysql_available(),
    reason="MySQL not available (set MYSQL_HOST, MYSQL_PASS env vars)",
)


def _mysql_url() -> str:
    h = os.environ["MYSQL_HOST"]
    p = os.environ.get("MYSQL_PORT", "3306")
    d = os.environ.get("MYSQL_DATABASE", "dev")
    u = os.environ.get("MYSQL_USER", "root")
    pw = os.environ.get("MYSQL_PASS", "")
    return f"mysql://{u}:{pw}@{h}:{p}/{d}"


@pytest.fixture
def mysql_conn():
    import pymysql
    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ.get("MYSQL_DATABASE", "dev"),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASS", ""),
        connect_timeout=10,
        autocommit=True,
    )
    yield conn
    conn.close()


@pytest.fixture
def mysql_tables(mysql_conn):
    """Create test tables in MySQL, yield schema name, drop on cleanup."""
    cur = mysql_conn.cursor()
    prefix = f"dqt_{uuid.uuid4().hex[:6]}"

    users = f"{prefix}_users"
    orders = f"{prefix}_orders"
    empty = f"{prefix}_empty"

    cur.execute(f"""
        CREATE TABLE {users} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            age INT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(f"""
        CREATE TABLE {orders} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            amount DECIMAL(10,2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES {users}(id)
        )
    """)
    cur.execute(f"CREATE TABLE {empty} (id INT AUTO_INCREMENT PRIMARY KEY, data TEXT)")

    # Seed users
    user_rows = [
        (f"user{i}@test.com", f"User {i}", None if i % 10 == 0 else 20 + (i % 40))
        for i in range(1, 101)
    ]
    cur.executemany(f"INSERT INTO {users} (email, name, age) VALUES (%s, %s, %s)", user_rows)

    # Seed orders (some negative amounts)
    order_rows = [
        ((i % 100) + 1, -5.0 if i % 50 == 0 else round(10 + i * 0.3, 2),
         ["pending", "shipped", "delivered", "cancelled"][i % 4])
        for i in range(1, 301)
    ]
    cur.executemany(
        f"INSERT INTO {orders} (user_id, amount, status) VALUES (%s, %s, %s)",
        order_rows,
    )

    yield {
        "users": users,
        "orders": orders,
        "empty": empty,
        "schema": os.environ.get("MYSQL_DATABASE", "dev"),
    }

    # Cleanup
    cur.execute(f"DROP TABLE IF EXISTS {orders}")
    cur.execute(f"DROP TABLE IF EXISTS {users}")
    cur.execute(f"DROP TABLE IF EXISTS {empty}")


# ---------------------------------------------------------------------------
# Connector tests
# ---------------------------------------------------------------------------

@requires_mysql
class TestMySQLConnection:
    def test_connect_success(self):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 AS val")
            assert cur.fetchone()["val"] == 1


@requires_mysql
class TestMySQLListTables:
    def test_lists_tables(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            tables = c.list_tables(conn, mysql_tables["schema"])
            names = [t["table_name"] for t in tables]
            assert mysql_tables["users"] in names
            assert mysql_tables["orders"] in names
            assert mysql_tables["empty"] in names

    def test_row_estimates(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            tables = c.list_tables(conn, mysql_tables["schema"])
            users = next(t for t in tables if t["table_name"] == mysql_tables["users"])
            assert users["estimated_rows"] >= 0


@requires_mysql
class TestMySQLColumns:
    def test_column_metadata(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            cols = c.get_columns(conn, mysql_tables["schema"], mysql_tables["users"])
            names = [col["column_name"] for col in cols]
            assert "id" in names
            assert "email" in names
            assert "age" in names

    def test_nullable(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            cols = c.get_columns(conn, mysql_tables["schema"], mysql_tables["users"])
            col_map = {col["column_name"]: col for col in cols}
            assert col_map["email"]["is_nullable"] == "NO"
            assert col_map["age"]["is_nullable"] == "YES"


@requires_mysql
class TestMySQLPrimaryKeys:
    def test_discovers_pks(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            pks = c.get_primary_keys(conn, mysql_tables["schema"])
            assert mysql_tables["users"] in pks
            assert "id" in pks[mysql_tables["users"]]


@requires_mysql
class TestMySQLForeignKeys:
    def test_discovers_fks(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            fks = c.get_foreign_keys(conn, mysql_tables["schema"])
            order_fks = [fk for fk in fks if fk["source_table"] == mysql_tables["orders"]]
            assert len(order_fks) >= 1
            assert order_fks[0]["source_column"] == "user_id"
            assert order_fks[0]["target_table"] == mysql_tables["users"]


@requires_mysql
class TestMySQLColumnDetails:
    def test_numeric_stats(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            details = c.get_column_details(
                conn, mysql_tables["schema"], mysql_tables["users"], "age", "int"
            )
            assert details["total"] == 100
            assert details["null_count"] == 10
            assert details["min_value"] is not None
            assert details["max_value"] is not None

    def test_text_stats(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            details = c.get_column_details(
                conn, mysql_tables["schema"], mysql_tables["users"], "email", "varchar"
            )
            assert details["total"] == 100
            assert details["null_count"] == 0
            assert details["distinct_count"] == 100

    def test_empty_table(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            details = c.get_column_details(
                conn, mysql_tables["schema"], mysql_tables["empty"], "data", "text"
            )
            assert details["total"] == 0


@requires_mysql
class TestMySQLRowCount:
    def test_exact_counts(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            assert c.get_exact_row_count(conn, mysql_tables["schema"], mysql_tables["users"]) == 100
            assert c.get_exact_row_count(conn, mysql_tables["schema"], mysql_tables["orders"]) == 300
            assert c.get_exact_row_count(conn, mysql_tables["schema"], mysql_tables["empty"]) == 0


@requires_mysql
class TestMySQLTimestamps:
    def test_finds_timestamp_columns(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            ts_cols = c.get_timestamp_columns(conn, mysql_tables["schema"], mysql_tables["users"])
            assert "created_at" in ts_cols

    def test_latest_timestamp(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            latest = c.get_latest_timestamp(
                conn, mysql_tables["schema"], mysql_tables["users"], "created_at"
            )
            assert latest is not None


@requires_mysql
class TestMySQLFKIntegrity:
    def test_valid_fks(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            result = c.check_fk_integrity(
                conn, mysql_tables["schema"],
                mysql_tables["orders"], "user_id",
                mysql_tables["users"], "id",
            )
            assert result["orphaned"] == 0
            assert result["non_null"] == 300


@requires_mysql
class TestMySQLSampleValues:
    def test_samples_text(self, mysql_tables):
        from dqlens.connectors.mysql import MySQLConnector
        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            values = c.sample_text_values(
                conn, mysql_tables["schema"], mysql_tables["users"], "email", limit=10
            )
            assert len(values) == 10
            assert all("@" in v for v in values)


# ---------------------------------------------------------------------------
# Full pipeline: profiler + engine
# ---------------------------------------------------------------------------

@requires_mysql
class TestMySQLFullPipeline:
    def test_profile_and_run(self, mysql_tables):
        from dqlens import profiler_v2
        from dqlens.connectors.mysql import MySQLConnector
        from dqlens.engine import run_checks

        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            profile = profiler_v2.profile_database(
                db=c, conn=conn, schema=mysql_tables["schema"],
                tables=[mysql_tables["users"], mysql_tables["orders"], mysql_tables["empty"]],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        assert len(profile.tables) == 3
        assert result.total_tests > 0
        # Should find: empty table (HIGH), negative amounts (MEDIUM)
        assert result.total_findings >= 1

    def test_detects_negative_amounts(self, mysql_tables):
        from dqlens import profiler_v2
        from dqlens.connectors.mysql import MySQLConnector
        from dqlens.engine import run_checks

        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            profile = profiler_v2.profile_database(
                db=c, conn=conn, schema=mysql_tables["schema"],
                tables=[mysql_tables["orders"]],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        findings = [f for t in result.tables for f in t.findings]
        assert any("negative" in f.message.lower() for f in findings)

    def test_detects_empty_table(self, mysql_tables):
        from dqlens import profiler_v2
        from dqlens.connectors.mysql import MySQLConnector
        from dqlens.engine import run_checks

        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            profile = profiler_v2.profile_database(
                db=c, conn=conn, schema=mysql_tables["schema"],
                tables=[mysql_tables["empty"]],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        findings = [f for t in result.tables for f in t.findings]
        assert any("empty" in f.message.lower() for f in findings)

    def test_email_pattern_detected(self, mysql_tables):
        from dqlens import profiler_v2
        from dqlens.connectors.mysql import MySQLConnector

        c = MySQLConnector(_mysql_url())
        with c.connect() as conn:
            profile = profiler_v2.profile_database(
                db=c, conn=conn, schema=mysql_tables["schema"],
                tables=[mysql_tables["users"]],
            )

        users = profile.get_table(mysql_tables["users"])
        email = users.get_column("email")
        assert email.detected_pattern == "email"
