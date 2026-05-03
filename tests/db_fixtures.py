"""Shared database fixtures for parameterized cross-DB integration tests.

Each database provides a setup/teardown that creates the same test schema:
- users: 100 rows, email (unique), name, age (10% null), created_at
- orders: 300 rows, user_id (FK), amount (some negative), status, created_at
- empty_table: 0 rows

Tests get a `db_env` dict with: connector, schema, table names, url.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass
class DbEnv:
    """Everything a cross-DB test needs."""

    connector: Any  # BaseConnector instance
    schema: str
    users_table: str
    orders_table: str
    empty_table: str
    url: str
    db_type: str  # "postgresql", "sqlite", "mysql"


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _pg_available() -> bool:
    url = _pg_url()
    if not url:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=5)
        conn.cursor().execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _pg_url() -> str | None:
    url = os.environ.get("POSTGRES_URL")
    if url:
        return url
    host = os.environ.get("PG_HOST")
    if not host:
        return None
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DATABASE", "dev")
    user = os.environ.get("PG_USER", "postgres")
    pw = os.environ.get("PG_PASS", "")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


@pytest.fixture
def pg_db_env():
    """PostgreSQL test environment with isolated schema."""
    url = _pg_url()
    if not url:
        pytest.skip("PostgreSQL not available")

    import psycopg2

    from dqlens.connectors.postgresql import PostgreSQLConnector

    schema = f"dqlens_shared_{uuid.uuid4().hex[:8]}"
    conn = psycopg2.connect(url, connect_timeout=10)
    conn.set_session(autocommit=True)
    cur = conn.cursor()

    cur.execute(f"CREATE SCHEMA {schema}")
    cur.execute(f"""
        CREATE TABLE {schema}.users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            age INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {schema}.orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES {schema}.users(id),
            amount NUMERIC(10,2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"CREATE TABLE {schema}.empty_table (id SERIAL PRIMARY KEY, data TEXT)")

    cur.execute(f"""
        INSERT INTO {schema}.users (email, name, age)
        SELECT 'user' || i || '@test.com', 'User ' || i,
               CASE WHEN MOD(i, 10) = 0 THEN NULL ELSE 20 + MOD(i, 40) END
        FROM generate_series(1, 100) AS i
    """)
    cur.execute(f"""
        INSERT INTO {schema}.orders (user_id, amount, status)
        SELECT MOD(i, 100) + 1,
               CASE WHEN MOD(i, 50) = 0 THEN -5.00 ELSE round((10 + i * 0.3)::numeric, 2) END,
               (ARRAY['pending','shipped','delivered','cancelled'])[MOD(i, 4) + 1]
        FROM generate_series(1, 300) AS i
    """)
    cur.execute("ANALYZE")

    env = DbEnv(
        connector=PostgreSQLConnector(url),
        schema=schema,
        users_table="users",
        orders_table="orders",
        empty_table="empty_table",
        url=url,
        db_type="postgresql",
    )
    yield env

    cur.execute(f"DROP SCHEMA {schema} CASCADE")
    conn.close()


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_db_env(tmp_path):
    """SQLite test environment with temp file."""
    from dqlens.connectors.sqlite import SQLiteConnector

    db_path = str(tmp_path / "shared_test.db")
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

    users = [
        (i, f"user{i}@test.com", f"User {i}",
         None if i % 10 == 0 else 20 + (i % 40),
         f"2026-05-01 {i % 24:02d}:00:00")
        for i in range(1, 101)
    ]
    cur.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", users)

    orders = [
        (i, (i % 100) + 1,
         -5.0 if i % 50 == 0 else round(10 + i * 0.3, 2),
         ["pending", "shipped", "delivered", "cancelled"][i % 4],
         f"2026-05-01 {i % 24:02d}:{i % 60:02d}:00")
        for i in range(1, 301)
    ]
    cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)

    conn.commit()
    conn.close()

    yield DbEnv(
        connector=SQLiteConnector(db_path),
        schema="main",
        users_table="users",
        orders_table="orders",
        empty_table="empty_table",
        url=db_path,
        db_type="sqlite",
    )


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

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
        conn.cursor().execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture
def mysql_db_env():
    """MySQL test environment with prefixed tables."""
    if not _mysql_available():
        pytest.skip("MySQL not available")

    import pymysql

    from dqlens.connectors.mysql import MySQLConnector

    h = os.environ["MYSQL_HOST"]
    p = int(os.environ.get("MYSQL_PORT", "3306"))
    d = os.environ.get("MYSQL_DATABASE", "dev")
    u = os.environ.get("MYSQL_USER", "root")
    pw = os.environ.get("MYSQL_PASS", "")
    url = f"mysql://{u}:{pw}@{h}:{p}/{d}"

    prefix = f"dqt_{uuid.uuid4().hex[:6]}"
    users = f"{prefix}_users"
    orders = f"{prefix}_orders"
    empty = f"{prefix}_empty"

    conn = pymysql.connect(host=h, port=p, database=d, user=u, password=pw, autocommit=True)
    cur = conn.cursor()

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

    user_rows = [
        (f"user{i}@test.com", f"User {i}", None if i % 10 == 0 else 20 + (i % 40))
        for i in range(1, 101)
    ]
    cur.executemany(f"INSERT INTO {users} (email, name, age) VALUES (%s, %s, %s)", user_rows)

    order_rows = [
        ((i % 100) + 1, -5.0 if i % 50 == 0 else round(10 + i * 0.3, 2),
         ["pending", "shipped", "delivered", "cancelled"][i % 4])
        for i in range(1, 301)
    ]
    cur.executemany(f"INSERT INTO {orders} (user_id, amount, status) VALUES (%s, %s, %s)", order_rows)

    env = DbEnv(
        connector=MySQLConnector(url),
        schema=d,
        users_table=users,
        orders_table=orders,
        empty_table=empty,
        url=url,
        db_type="mysql",
    )
    yield env

    cur.execute(f"DROP TABLE IF EXISTS {orders}")
    cur.execute(f"DROP TABLE IF EXISTS {users}")
    cur.execute(f"DROP TABLE IF EXISTS {empty}")
    conn.close()


# ---------------------------------------------------------------------------
# Parameterized fixture: all available databases
# ---------------------------------------------------------------------------

@pytest.fixture(params=["postgresql", "sqlite", "mysql"])
def db_env(request, pg_db_env, sqlite_db_env, mysql_db_env):
    """Parameterized fixture that yields a DbEnv for each available database."""
    if request.param == "postgresql":
        return pg_db_env
    elif request.param == "sqlite":
        return sqlite_db_env
    elif request.param == "mysql":
        return mysql_db_env
