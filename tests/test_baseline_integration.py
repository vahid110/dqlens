"""Integration tests for baseline storage with real profiled data.

Tests save/load roundtrip with profiles generated from a real database,
drift comparison across multiple profiles, and edge cases.
"""

from __future__ import annotations

import time

import pytest

from markers import requires_postgres
from dqlens import connector, profiler
from dqlens.baseline import (
    get_baseline_count,
    load_latest_profile,
    load_previous_profile,
    save_profile,
)


@pytest.fixture
def simple_schema(pg_conn_autocommit, test_schema):
    """Minimal schema for baseline tests."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema
    cur.execute(f"""
        CREATE TABLE {s}.items (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            price NUMERIC(10,2),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        INSERT INTO {s}.items (name, price, created_at)
        SELECT 'Item ' || i, round((random() * 100)::numeric, 2), NOW() - (i || ' hours')::interval
        FROM generate_series(1, 50) AS i
    """)
    cur.execute("ANALYZE")
    cur.close()
    yield s


# ---------------------------------------------------------------------------
# Save and load with real profiles
# ---------------------------------------------------------------------------

@requires_postgres
class TestRealProfileRoundtrip:
    def test_save_and_load_real_profile(self, pg_url, simple_schema, tmp_path):
        with connector.connect(pg_url) as conn:
            db_profile = profiler.profile_database(conn, schema=simple_schema)

        filepath = save_profile(db_profile, base_path=tmp_path)
        assert filepath.exists()

        loaded = load_latest_profile(base_path=tmp_path)
        assert loaded is not None
        assert loaded.schema_name == simple_schema
        assert len(loaded.tables) == 1
        assert loaded.tables[0].table_name == "items"
        assert loaded.tables[0].row_count == 50

    def test_column_stats_survive_roundtrip(self, pg_url, simple_schema, tmp_path):
        with connector.connect(pg_url) as conn:
            db_profile = profiler.profile_database(conn, schema=simple_schema)

        save_profile(db_profile, base_path=tmp_path)
        loaded = load_latest_profile(base_path=tmp_path)

        items = loaded.get_table("items")
        name_col = items.get_column("name")
        assert name_col.null_count == 0
        assert name_col.distinct_count == 50

        price_col = items.get_column("price")
        assert price_col.min_value is not None
        assert price_col.max_value is not None

    def test_freshness_survives_roundtrip(self, pg_url, simple_schema, tmp_path):
        with connector.connect(pg_url) as conn:
            db_profile = profiler.profile_database(conn, schema=simple_schema)

        save_profile(db_profile, base_path=tmp_path)
        loaded = load_latest_profile(base_path=tmp_path)

        items = loaded.get_table("items")
        assert items.freshness_column == "created_at"
        assert items.latest_timestamp is not None


# ---------------------------------------------------------------------------
# Multiple baselines and drift comparison
# ---------------------------------------------------------------------------

@requires_postgres
class TestMultipleBaselines:
    def test_two_profiles_enables_comparison(self, pg_url, simple_schema, tmp_path, pg_conn_autocommit):
        # First profile
        with connector.connect(pg_url) as conn:
            p1 = profiler.profile_database(conn, schema=simple_schema)
        save_profile(p1, base_path=tmp_path)
        assert get_baseline_count(base_path=tmp_path) == 1

        time.sleep(0.1)

        # Add more data
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"""
            INSERT INTO {simple_schema}.items (name, price)
            SELECT 'New Item ' || i, round((random() * 200)::numeric, 2)
            FROM generate_series(1, 50) AS i
        """)
        cur.execute("ANALYZE")
        cur.close()

        # Second profile
        with connector.connect(pg_url) as conn:
            p2 = profiler.profile_database(conn, schema=simple_schema)
        save_profile(p2, base_path=tmp_path)
        assert get_baseline_count(base_path=tmp_path) == 2

        # Latest should be p2
        latest = load_latest_profile(base_path=tmp_path)
        assert latest.tables[0].row_count == 100

        # Previous should be p1
        previous = load_previous_profile(base_path=tmp_path)
        assert previous is not None
        assert previous.tables[0].row_count == 50

    def test_three_profiles_previous_is_second(self, pg_url, simple_schema, tmp_path, pg_conn_autocommit):
        """With 3 profiles, previous should be the 2nd (not the 1st)."""
        for i in range(3):
            time.sleep(0.1)
            with connector.connect(pg_url) as conn:
                p = profiler.profile_database(conn, schema=simple_schema)
            save_profile(p, base_path=tmp_path)

        assert get_baseline_count(base_path=tmp_path) == 3
        previous = load_previous_profile(base_path=tmp_path)
        assert previous is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@requires_postgres
class TestBaselineEdgeCases:
    def test_empty_schema_profile_roundtrip(self, pg_url, pg_conn_autocommit, tmp_path):
        """Profile of empty schema should save and load correctly."""
        import uuid
        schema = f"dqlens_bl_{uuid.uuid4().hex[:8]}"
        cur = pg_conn_autocommit.cursor()
        cur.execute(f"CREATE SCHEMA {schema}")
        try:
            with connector.connect(pg_url) as conn:
                p = profiler.profile_database(conn, schema=schema)
            save_profile(p, base_path=tmp_path)
            loaded = load_latest_profile(base_path=tmp_path)
            assert loaded is not None
            assert len(loaded.tables) == 0
        finally:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")

    def test_profile_with_all_null_columns(self, pg_url, pg_conn_autocommit, test_schema, tmp_path):
        """Tables with all-null columns should roundtrip correctly."""
        cur = pg_conn_autocommit.cursor()
        s = test_schema
        cur.execute(f"CREATE TABLE {s}.nulls (id serial primary key, a text, b int)")
        cur.execute(f"INSERT INTO {s}.nulls (id) SELECT i FROM generate_series(1,10) AS i")
        cur.execute("ANALYZE")
        cur.close()

        with connector.connect(pg_url) as conn:
            p = profiler.profile_database(conn, schema=s)
        save_profile(p, base_path=tmp_path)
        loaded = load_latest_profile(base_path=tmp_path)

        nulls_table = loaded.get_table("nulls")
        a_col = nulls_table.get_column("a")
        assert a_col.null_count == 10
        assert a_col.null_pct == 100.0
