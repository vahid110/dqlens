"""Integration tests for test generation from real database profiles.

Verifies that tests.yaml generated from real data is correct, complete,
and roundtrips properly.
"""

from __future__ import annotations

import pytest
from markers import requires_postgres

from dqlens import connector, profiler
from dqlens.testgen import generate_tests, load_tests, save_tests


@pytest.fixture
def rich_schema(pg_conn_autocommit, test_schema):
    """Schema with diverse column types and patterns for test generation."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema

    cur.execute(f"""
        CREATE TABLE {s}.users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            username VARCHAR(100) UNIQUE NOT NULL,
            age INTEGER,
            score NUMERIC(5,2),
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            latitude NUMERIC(9,6),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES {s}.users(id),
            total_amount NUMERIC(10,2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.empty_log (
            id SERIAL PRIMARY KEY,
            message TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Seed users
    cur.execute(f"""
        INSERT INTO {s}.users (email, username, age, score, status, latitude, created_at)
        SELECT
            'user' || i || '@example.com',
            'user_' || i,
            20 + (MOD(i, 50)),
            CASE WHEN MOD(i, 5) = 0 THEN NULL ELSE round((random() * 100)::numeric, 2) END,
            CASE (MOD(i, 3)) WHEN 0 THEN 'active' WHEN 1 THEN 'inactive' ELSE 'banned' END,
            CASE WHEN MOD(i, 4) = 0 THEN NULL ELSE round((-90 + random() * 180)::numeric, 6) END,
            NOW() - (i || ' hours')::interval
        FROM generate_series(1, 200) AS i
    """)

    # Seed orders
    cur.execute(f"""
        INSERT INTO {s}.orders (user_id, total_amount, status, created_at)
        SELECT
            (MOD(i, 200)) + 1,
            round((random() * 500 + 1)::numeric, 2),
            CASE (MOD(i, 4)) WHEN 0 THEN 'pending' WHEN 1 THEN 'shipped' WHEN 2 THEN 'delivered' ELSE 'cancelled' END,
            NOW() - (i || ' minutes')::interval
        FROM generate_series(1, 800) AS i
    """)

    cur.execute("ANALYZE")
    cur.close()
    yield s


# ---------------------------------------------------------------------------
# Test generation from real profiles
# ---------------------------------------------------------------------------

@requires_postgres
class TestGenerateFromRealProfile:
    def test_generates_checks_for_all_tables(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        table_names = [t["table"] for t in tests["tables"]]
        assert any("users" in n for n in table_names)
        assert any("orders" in n for n in table_names)

    def test_generates_row_count_checks(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        for table_def in tests["tables"]:
            check_types = [c["check"] for c in table_def["checks"]]
            assert "row_count" in check_types

    def test_generates_not_null_for_required_columns(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_tests = next(t for t in tests["tables"] if "users" in t["table"])
        not_null_cols = [
            c["column"] for c in users_tests["checks"] if c["check"] == "not_null"
        ]
        assert "email" in not_null_cols
        assert "username" in not_null_cols

    def test_generates_unique_for_pk_and_unique(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_tests = next(t for t in tests["tables"] if "users" in t["table"])
        unique_cols = [
            c["column"] for c in users_tests["checks"] if c["check"] == "unique"
        ]
        assert "id" in unique_cols
        assert "email" in unique_cols

    def test_generates_pattern_for_email(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_tests = next(t for t in tests["tables"] if "users" in t["table"])
        pattern_checks = [
            c for c in users_tests["checks"]
            if c["check"] == "pattern" and c.get("column") == "email"
        ]
        assert len(pattern_checks) == 1
        assert pattern_checks[0]["pattern"] == "email"

    def test_generates_null_rate_for_nullable_columns(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_tests = next(t for t in tests["tables"] if "users" in t["table"])
        null_rate_cols = [
            c["column"] for c in users_tests["checks"] if c["check"] == "null_rate"
        ]
        # score has 20% nulls, latitude has ~25% nulls
        assert "score" in null_rate_cols or "latitude" in null_rate_cols

    def test_generates_fk_integrity_check(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        orders_tests = next(t for t in tests["tables"] if "orders" in t["table"])
        fk_checks = [c for c in orders_tests["checks"] if c["check"] == "fk_integrity"]
        assert len(fk_checks) >= 1
        assert fk_checks[0]["column"] == "user_id"

    def test_generates_freshness_check(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_tests = next(t for t in tests["tables"] if "users" in t["table"])
        freshness = [c for c in users_tests["checks"] if c["check"] == "freshness"]
        assert len(freshness) == 1
        assert freshness[0]["column"] == "created_at"

    def test_generates_positive_values_for_amount(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        orders_tests = next(t for t in tests["tables"] if "orders" in t["table"])
        positive = [
            c for c in orders_tests["checks"]
            if c["check"] == "positive_values" and c.get("column") == "total_amount"
        ]
        assert len(positive) == 1

    def test_every_check_has_reason(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        for table_def in tests["tables"]:
            for check in table_def["checks"]:
                assert "reason" in check, f"Check {check['check']} missing reason"
                assert len(check["reason"]) > 5

    def test_empty_table_minimal_checks(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        empty_tests = next(
            (t for t in tests["tables"] if "empty_log" in t["table"]), None
        )
        assert empty_tests is not None
        assert empty_tests["row_count_at_profile"] == 0
        # Should have at least a row_count check
        assert len(empty_tests["checks"]) >= 1


# ---------------------------------------------------------------------------
# Save and load roundtrip with real data
# ---------------------------------------------------------------------------

@requires_postgres
class TestSaveLoadRoundtrip:
    def test_full_roundtrip(self, pg_url, rich_schema, tmp_path):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        save_tests(tests, base_path=tmp_path)
        loaded = load_tests(base_path=tmp_path)

        assert loaded is not None
        assert loaded["version"] == tests["version"]
        assert loaded["schema"] == tests["schema"]
        assert len(loaded["tables"]) == len(tests["tables"])

        # Verify check counts match
        for orig, loaded_t in zip(tests["tables"], loaded["tables"]):
            assert len(orig["checks"]) == len(loaded_t["checks"])

    def test_check_values_survive_roundtrip(self, pg_url, rich_schema, tmp_path):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)
        save_tests(tests, base_path=tmp_path)
        loaded = load_tests(base_path=tmp_path)

        # Find a pattern check and verify its fields survived
        for table_def in loaded["tables"]:
            for check in table_def["checks"]:
                if check["check"] == "pattern":
                    assert "pattern" in check
                    assert "current_match" in check
                    assert "value" in check
                    return

    @pytest.mark.parametrize("table_filter", [
        ["users"],
        ["orders"],
        ["users", "orders"],
    ])
    def test_roundtrip_with_filtered_tables(self, pg_url, rich_schema, tmp_path, table_filter):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(
                conn, schema=rich_schema, tables=table_filter,
            )
        tests = generate_tests(db)
        save_tests(tests, base_path=tmp_path)
        loaded = load_tests(base_path=tmp_path)

        assert len(loaded["tables"]) == len(table_filter)


# ---------------------------------------------------------------------------
# Check count validation
# ---------------------------------------------------------------------------

@requires_postgres
class TestCheckCounts:
    def test_total_checks_reasonable(self, pg_url, rich_schema):
        """A schema with 3 tables and ~15 columns should generate 30-80 checks."""
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        total = sum(len(t["checks"]) for t in tests["tables"])
        assert 20 <= total <= 100, f"Generated {total} checks — seems off"

    def test_populated_table_has_more_checks_than_empty(self, pg_url, rich_schema):
        with connector.connect(pg_url) as conn:
            db = profiler.profile_database(conn, schema=rich_schema)
        tests = generate_tests(db)

        users_checks = next(t for t in tests["tables"] if "users" in t["table"])
        empty_checks = next(t for t in tests["tables"] if "empty_log" in t["table"])
        assert len(users_checks["checks"]) > len(empty_checks["checks"])
