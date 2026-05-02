"""Unit tests for quick mode (sampled profiling)."""

from __future__ import annotations

import sqlite3
import tempfile

import pytest

from dqlens.connectors.sqlite import SQLiteConnector


@pytest.fixture
def large_sqlite_db(tmp_path):
    """Create a SQLite DB with enough data to test sampling."""
    db_path = str(tmp_path / "large.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE big_table (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Insert 5000 rows
    rows = []
    for i in range(1, 5001):
        rows.append((
            i,
            f"user{i}@test.com",
            round(10 + i * 0.1, 2),
            ["active", "inactive", "pending"][i % 3],
            f"2026-01-{(i % 28) + 1:02d}",
        ))
    cur.executemany(
        "INSERT INTO big_table VALUES (?, ?, ?, ?, ?)", rows
    )

    conn.commit()
    conn.close()
    return db_path


class TestSQLiteSampledColumnDetails:
    def test_returns_stats(self, large_sqlite_db):
        c = SQLiteConnector(large_sqlite_db)
        with c.connect() as conn:
            details = c.get_sampled_column_details(
                conn, "main", "big_table", "amount", "real", sample_size=500
            )
            assert "total" in details
            assert details["total"] <= 500
            assert details["total"] > 0
            assert details["null_count"] == 0
            assert details["min_value"] is not None
            assert details["max_value"] is not None

    def test_sample_smaller_than_table(self, large_sqlite_db):
        c = SQLiteConnector(large_sqlite_db)
        with c.connect() as conn:
            full = c.get_column_details(conn, "main", "big_table", "amount", "real")
            sampled = c.get_sampled_column_details(
                conn, "main", "big_table", "amount", "real", sample_size=100
            )
            assert sampled["total"] <= 100
            assert full["total"] == 5000

    def test_text_column_sampling(self, large_sqlite_db):
        c = SQLiteConnector(large_sqlite_db)
        with c.connect() as conn:
            details = c.get_sampled_column_details(
                conn, "main", "big_table", "email", "text", sample_size=200
            )
            assert details["total"] <= 200
            assert details["null_count"] == 0
            assert details["distinct_count"] > 0

    def test_sample_size_larger_than_table(self, large_sqlite_db):
        """If sample_size > table rows, should return all rows."""
        c = SQLiteConnector(large_sqlite_db)
        with c.connect() as conn:
            details = c.get_sampled_column_details(
                conn, "main", "big_table", "id", "integer", sample_size=99999
            )
            assert details["total"] == 5000


class TestQuickModeProfiler:
    def test_quick_profile_produces_results(self, large_sqlite_db):
        from dqlens import profiler_v2
        from dqlens.connectors.sqlite import SQLiteConnector

        db = SQLiteConnector(large_sqlite_db)
        with db.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db, conn=conn, schema="main", quick=True,
            )
            assert len(profile.tables) == 1
            table = profile.tables[0]
            assert table.table_name == "big_table"
            assert len(table.columns) == 5

    def test_quick_profile_has_column_stats(self, large_sqlite_db):
        from dqlens import profiler_v2
        from dqlens.connectors.sqlite import SQLiteConnector

        db = SQLiteConnector(large_sqlite_db)
        with db.connect() as conn:
            profile = profiler_v2.profile_database(
                db=db, conn=conn, schema="main", quick=True,
            )
            table = profile.tables[0]
            amount = table.get_column("amount")
            # Sampled stats should still have min/max
            assert amount is not None
            assert amount.min_value is not None
            assert amount.max_value is not None

    def test_quick_vs_full_both_work(self, large_sqlite_db):
        from dqlens import profiler_v2
        from dqlens.connectors.sqlite import SQLiteConnector

        db = SQLiteConnector(large_sqlite_db)
        with db.connect() as conn:
            full = profiler_v2.profile_database(
                db=db, conn=conn, schema="main", quick=False,
            )
            quick = profiler_v2.profile_database(
                db=db, conn=conn, schema="main", quick=True,
            )
            # Both should produce profiles with the same tables
            assert len(full.tables) == len(quick.tables)
            assert full.tables[0].table_name == quick.tables[0].table_name
            # Full should have exact row count
            assert full.tables[0].row_count == 5000


class TestQuickModeCLI:
    def test_quick_flag_accepted(self, large_sqlite_db):
        from click.testing import CliRunner

        from dqlens.cli import main

        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", large_sqlite_db, "--schema", "main"])
            result = runner.invoke(main, ["profile", "--quick"])
            assert result.exit_code == 0
            assert "quick mode" in result.output.lower()

    def test_quick_flag_short(self, large_sqlite_db):
        from click.testing import CliRunner

        from dqlens.cli import main

        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", large_sqlite_db, "--schema", "main"])
            result = runner.invoke(main, ["profile", "-q"])
            assert result.exit_code == 0
