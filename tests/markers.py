"""Shared test markers for conditional skipping."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_env():
    """Load .internal/.env if it exists."""
    env_path = Path(__file__).parent.parent / ".internal" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _get_postgres_url() -> str | None:
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


def _postgres_available() -> bool:
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


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not available (set POSTGRES_URL or PG_HOST env vars)",
)

POSTGRES_URL = _get_postgres_url()
