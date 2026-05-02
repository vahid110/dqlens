"""Shared test configuration — fixtures, env loading, skip markers.

Integration tests require a live PostgreSQL database. Set POSTGRES_URL
in .internal/.env or as an environment variable.

Run unit tests only:    pytest tests/ -k "unit"
Run integration tests:  pytest tests/ -k "integration"
Run all:                pytest tests/
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_env():
    """Load .internal/.env if it exists (no python-dotenv dependency)."""
    env_path = Path(__file__).parent.parent / ".internal" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not os.environ.get(key):
                os.environ[key] = value


_load_env()


def _get_postgres_url() -> str | None:
    """Build a PostgreSQL URL from environment variables."""
    # Direct URL takes precedence
    url = os.environ.get("POSTGRES_URL")
    if url:
        return url

    # Fall back to individual PG_ vars
    host = os.environ.get("PG_HOST")
    if not host:
        return None

    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DATABASE", "dev")
    user = os.environ.get("PG_USER", "postgres")
    pw = os.environ.get("PG_PASS", "")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def _postgres_available() -> bool:
    """Check if we can connect to PostgreSQL."""
    url = _get_postgres_url()
    if not url:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return True
    except Exception:
        return False


# Local PostgreSQL (Unix socket — for demo DB tests)
def _local_pg_available() -> bool:
    """Check if local PostgreSQL is available via Unix socket."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql:///dqlens_test?host=/tmp", connect_timeout=5
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available (set POSTGRES_URL or PG_HOST env vars)",
)

requires_local_pg = pytest.mark.skipif(
    not _local_pg_available(),
    reason="Local PostgreSQL dqlens_test database not available",
)


@pytest.fixture
def pg_url() -> str:
    """Returns the PostgreSQL connection URL."""
    url = _get_postgres_url()
    assert url, "POSTGRES_URL not set"
    return url


@pytest.fixture
def pg_conn():
    """Provides a live PostgreSQL connection. Auto-closes after test."""
    import psycopg2
    url = _get_postgres_url()
    assert url, "POSTGRES_URL not set"
    conn = psycopg2.connect(url, connect_timeout=10)
    yield conn
    conn.close()


@pytest.fixture
def pg_conn_autocommit():
    """PostgreSQL connection with autocommit for DDL operations."""
    import psycopg2
    url = _get_postgres_url()
    assert url, "POSTGRES_URL not set"
    conn = psycopg2.connect(url, connect_timeout=10)
    conn.set_session(autocommit=True)
    yield conn
    conn.close()


@pytest.fixture
def test_schema(pg_conn_autocommit):
    """Create a temporary schema for test isolation. Drops it after the test."""
    import uuid
    schema_name = f"dqlens_test_{uuid.uuid4().hex[:8]}"
    cur = pg_conn_autocommit.cursor()
    cur.execute(f"CREATE SCHEMA {schema_name}")
    yield schema_name
    cur.execute(f"DROP SCHEMA {schema_name} CASCADE")
    cur.close()


@pytest.fixture
def local_pg_url() -> str:
    """Returns the local PostgreSQL connection URL."""
    return "postgresql:///dqlens_test?host=/tmp"
