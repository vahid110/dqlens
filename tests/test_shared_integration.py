"""Shared integration tests — same assertions, all databases.

Each test runs against PostgreSQL, SQLite, and MySQL (if available).
Tests that fail on one DB but pass on others indicate connector bugs.
"""

from __future__ import annotations

import pytest
from db_fixtures import DbEnv, db_env, mysql_db_env, pg_db_env, sqlite_db_env

# ---------------------------------------------------------------------------
# Table listing
# ---------------------------------------------------------------------------

class TestListTables:
    def test_finds_all_tables(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            tables = db_env.connector.list_tables(conn, db_env.schema)
            names = [t["table_name"] for t in tables]
            assert db_env.users_table in names
            assert db_env.orders_table in names
            assert db_env.empty_table in names

    def test_row_estimates_non_negative(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            tables = db_env.connector.list_tables(conn, db_env.schema)
            for t in tables:
                assert t["estimated_rows"] >= 0


# ---------------------------------------------------------------------------
# Column metadata
# ---------------------------------------------------------------------------

class TestGetColumns:
    def test_users_columns(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            cols = db_env.connector.get_columns(conn, db_env.schema, db_env.users_table)
            names = [c["column_name"] for c in cols]
            assert "id" in names
            assert "email" in names
            assert "name" in names
            assert "age" in names

    def test_email_not_nullable(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            cols = db_env.connector.get_columns(conn, db_env.schema, db_env.users_table)
            email = next(c for c in cols if c["column_name"] == "email")
            assert email["is_nullable"] == "NO"

    def test_age_nullable(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            cols = db_env.connector.get_columns(conn, db_env.schema, db_env.users_table)
            age = next(c for c in cols if c["column_name"] == "age")
            assert age["is_nullable"] == "YES"

    def test_empty_table_has_columns(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            cols = db_env.connector.get_columns(conn, db_env.schema, db_env.empty_table)
            assert len(cols) >= 2


# ---------------------------------------------------------------------------
# Primary keys
# ---------------------------------------------------------------------------

class TestPrimaryKeys:
    def test_users_pk(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            pks = db_env.connector.get_primary_keys(conn, db_env.schema)
            assert db_env.users_table in pks
            assert "id" in pks[db_env.users_table]


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

class TestForeignKeys:
    def test_orders_fk(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            fks = db_env.connector.get_foreign_keys(conn, db_env.schema)
            order_fks = [fk for fk in fks if fk["source_table"] == db_env.orders_table]
            assert len(order_fks) >= 1
            assert order_fks[0]["source_column"] == "user_id"
            assert order_fks[0]["target_table"] == db_env.users_table


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------

class TestRowCounts:
    def test_users_count(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            count = db_env.connector.get_exact_row_count(conn, db_env.schema, db_env.users_table)
            assert count == 100

    def test_orders_count(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            count = db_env.connector.get_exact_row_count(conn, db_env.schema, db_env.orders_table)
            assert count == 300

    def test_empty_count(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            count = db_env.connector.get_exact_row_count(conn, db_env.schema, db_env.empty_table)
            assert count == 0


# ---------------------------------------------------------------------------
# Column details
# ---------------------------------------------------------------------------

class TestColumnDetails:
    def test_numeric_column(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            dt = "integer" if db_env.db_type != "mysql" else "int"
            details = db_env.connector.get_column_details(
                conn, db_env.schema, db_env.users_table, "age", dt,
            )
            assert details["total"] == 100
            assert details["null_count"] == 10
            assert details["min_value"] is not None
            assert details["max_value"] is not None

    def test_text_column(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            dt = "character varying" if db_env.db_type == "postgresql" else (
                "varchar" if db_env.db_type == "mysql" else "text"
            )
            details = db_env.connector.get_column_details(
                conn, db_env.schema, db_env.users_table, "email", dt,
            )
            assert details["total"] == 100
            assert details["null_count"] == 0
            assert details["distinct_count"] == 100

    def test_empty_table_column(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            dt = "text"
            details = db_env.connector.get_column_details(
                conn, db_env.schema, db_env.empty_table, "data", dt,
            )
            assert details["total"] == 0
            assert details["null_count"] == 0


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

class TestTimestamps:
    def test_finds_timestamp_column(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            ts_cols = db_env.connector.get_timestamp_columns(
                conn, db_env.schema, db_env.users_table,
            )
            assert "created_at" in ts_cols

    def test_latest_timestamp_not_none(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            latest = db_env.connector.get_latest_timestamp(
                conn, db_env.schema, db_env.users_table, "created_at",
            )
            assert latest is not None

    def test_empty_table_no_timestamp(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            latest = db_env.connector.get_latest_timestamp(
                conn, db_env.schema, db_env.empty_table, "data",
            )
            assert latest is None


# ---------------------------------------------------------------------------
# FK integrity
# ---------------------------------------------------------------------------

class TestFKIntegrity:
    def test_no_orphans(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            result = db_env.connector.check_fk_integrity(
                conn, db_env.schema,
                db_env.orders_table, "user_id",
                db_env.users_table, "id",
            )
            assert result["orphaned"] == 0
            assert result["non_null"] == 300


# ---------------------------------------------------------------------------
# Text sampling
# ---------------------------------------------------------------------------

class TestSampleValues:
    def test_samples_emails(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            values = db_env.connector.sample_text_values(
                conn, db_env.schema, db_env.users_table, "email", limit=10,
            )
            assert len(values) == 10
            assert all("@" in v for v in values)

    def test_sample_empty_table(self, db_env: DbEnv):
        with db_env.connector.connect() as conn:
            values = db_env.connector.sample_text_values(
                conn, db_env.schema, db_env.empty_table, "data", limit=10,
            )
            assert values == []


# ---------------------------------------------------------------------------
# Full pipeline: profiler + engine
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_profile_all_tables(self, db_env: DbEnv):
        from dqlens import profiler_v2

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table, db_env.orders_table, db_env.empty_table],
            )
            assert len(profile.tables) == 3

    def test_correct_row_counts(self, db_env: DbEnv):
        from dqlens import profiler_v2

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table, db_env.orders_table, db_env.empty_table],
            )
            users = profile.get_table(db_env.users_table)
            assert users.row_count == 100
            orders = profile.get_table(db_env.orders_table)
            assert orders.row_count == 300
            empty = profile.get_table(db_env.empty_table)
            assert empty.row_count == 0

    def test_email_pattern_detected(self, db_env: DbEnv):
        from dqlens import profiler_v2

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table],
            )
            users = profile.get_table(db_env.users_table)
            email = users.get_column("email")
            assert email.detected_pattern == "email"

    def test_finds_problems(self, db_env: DbEnv):
        from dqlens import profiler_v2
        from dqlens.engine import run_checks

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table, db_env.orders_table, db_env.empty_table],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        assert result.total_tests > 0
        assert result.total_findings >= 1  # At least empty table + negative amounts

    def test_detects_empty_table(self, db_env: DbEnv):
        from dqlens import profiler_v2
        from dqlens.engine import run_checks

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.empty_table],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        findings = [f for t in result.tables for f in t.findings]
        assert any("empty" in f.message.lower() for f in findings)

    def test_detects_negative_amounts(self, db_env: DbEnv):
        from dqlens import profiler_v2
        from dqlens.engine import run_checks

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.orders_table],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        findings = [f for t in result.tables for f in t.findings]
        assert any("negative" in f.message.lower() for f in findings)

    def test_every_finding_has_detail(self, db_env: DbEnv):
        from dqlens import profiler_v2
        from dqlens.engine import run_checks

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table, db_env.orders_table, db_env.empty_table],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        for t in result.tables:
            for f in t.findings:
                assert f.detail, f"Finding missing detail: {f.message}"
                assert f.dimension is not None, f"Finding missing dimension: {f.message}"

    def test_summary_consistent(self, db_env: DbEnv):
        from dqlens import profiler_v2
        from dqlens.engine import run_checks

        with db_env.connector.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db_env.connector, conn=conn, schema=db_env.schema,
                tables=[db_env.users_table, db_env.orders_table, db_env.empty_table],
            )
            result = run_checks(current=profile, baseline=None, conn=conn)

        assert result.total_tests == result.total_findings + result.total_passed
