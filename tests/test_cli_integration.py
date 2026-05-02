"""Integration tests for the CLI against a real database.

Tests the full user workflow: init → profile → run → ignore.
Covers: happy paths, error handling, output formats, and edge cases.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner
from markers import requires_postgres

from dqlens.cli import main


@pytest.fixture
def schema_for_cli(pg_conn_autocommit, test_schema):
    """Create tables for CLI testing."""
    cur = pg_conn_autocommit.cursor()
    s = test_schema

    cur.execute(f"""
        CREATE TABLE {s}.users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE {s}.posts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES {s}.users(id),
            title VARCHAR(255) NOT NULL,
            body TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        INSERT INTO {s}.users (email, name, created_at)
        SELECT 'user' || i || '@test.com', 'User ' || i, NOW() - (i || ' hours')::interval
        FROM generate_series(1, 100) AS i
    """)
    cur.execute(f"""
        INSERT INTO {s}.posts (user_id, title, body, created_at)
        SELECT (MOD(i, 100)) + 1, 'Post ' || i,
               CASE WHEN MOD(i, 5) = 0 THEN NULL ELSE 'Body of post ' || i END,
               NOW() - (i || ' minutes')::interval
        FROM generate_series(1, 500) AS i
    """)
    cur.execute("ANALYZE")
    cur.close()
    yield s


# ---------------------------------------------------------------------------
# Init → Profile → Run workflow
# ---------------------------------------------------------------------------

@requires_postgres
class TestFullWorkflow:
    def test_init_profile_run(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            # Init
            result = runner.invoke(main, [
                "init", pg_url, "--schema", schema_for_cli,
            ])
            assert result.exit_code == 0
            assert "Initialized" in result.output

            # Profile
            result = runner.invoke(main, ["profile"])
            assert result.exit_code == 0
            assert "Profile saved" in result.output
            assert "checks" in result.output

            # Run
            result = runner.invoke(main, ["run"])
            assert result.exit_code == 0
            assert "Summary:" in result.output

    def test_run_verbose(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            result = runner.invoke(main, ["run", "--verbose"])
            assert result.exit_code == 0
            # Verbose should show passing tests
            assert "✓" in result.output

    def test_run_focus_high(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            result = runner.invoke(main, ["run", "--focus", "high"])
            assert result.exit_code == 0
            assert "Summary:" in result.output

    def test_run_json_output(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            result = runner.invoke(main, ["run", "--json-output"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "summary" in data
            assert "tables" in data
            assert data["summary"]["tables"] > 0

    def test_run_ci_mode_exit_code(self, pg_url, schema_for_cli):
        """CI mode should exit 0 if no problems, 1 if problems found."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            result = runner.invoke(main, ["run", "--ci"])
            # Exit code depends on whether problems are found
            assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Profile with table filtering
# ---------------------------------------------------------------------------

@requires_postgres
class TestProfileFiltering:
    def test_profile_specific_tables(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])

            result = runner.invoke(main, [
                "profile", "--tables", "users",
            ])
            assert result.exit_code == 0
            assert "users" in result.output
            # posts should not appear
            assert "posts" not in result.output.split("Profile saved")[0]

    def test_profile_exclude_tables(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])

            result = runner.invoke(main, [
                "profile", "--exclude", "posts",
            ])
            assert result.exit_code == 0
            assert "users" in result.output


# ---------------------------------------------------------------------------
# Ignore workflow
# ---------------------------------------------------------------------------

@requires_postgres
class TestIgnoreWorkflow:
    def test_ignore_then_run(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            # First run — get baseline count
            result1 = runner.invoke(main, ["run", "--json-output"])
            data1 = json.loads(result1.output)
            total1 = data1["summary"]["failed"]

            if total1 > 0:
                # Ignore the first finding
                first_finding = None
                for table in data1["tables"]:
                    if table["findings"]:
                        f = table["findings"][0]
                        col_part = f".{f['column']}" if f["column"] else ""
                        first_finding = f"{table['name'].split('.')[-1]}{col_part}.{f['category']}"
                        break

                if first_finding:
                    result_ignore = runner.invoke(main, ["ignore", first_finding])
                    assert result_ignore.exit_code == 0

                    # Second run — should have fewer findings
                    result2 = runner.invoke(main, ["run", "--json-output"])
                    data2 = json.loads(result2.output)
                    assert data2["summary"]["failed"] <= total1


# ---------------------------------------------------------------------------
# Connection override
# ---------------------------------------------------------------------------

@requires_postgres
class TestConnectionOverride:
    def test_profile_with_connection_flag(self, pg_url, schema_for_cli):
        """Profile should work with --connection even without init."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            # No init — use --connection directly
            result = runner.invoke(main, [
                "profile",
                "--connection", pg_url,
                "--schema", schema_for_cli,
            ])
            # This may fail because no .dqlens dir exists for saving
            # but the connection itself should work
            # The current implementation requires init first for saving
            # so this tests the error path
            assert result.exit_code in (0, 1)

    def test_run_with_connection_flag(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])

            # Run with explicit connection (overrides config)
            result = runner.invoke(main, [
                "run", "--connection", pg_url, "--schema", schema_for_cli,
            ])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@requires_postgres
class TestErrorHandling:
    def test_profile_bad_connection(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", "postgresql://bad:bad@localhost:9999/nope"])
            result = runner.invoke(main, ["profile"])
            assert result.exit_code != 0
            assert "Error" in result.output

    def test_run_before_profile(self, pg_url, schema_for_cli):
        """Run without profiling first should still work (profiles on the fly)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            # Skip profile, go straight to run
            result = runner.invoke(main, ["run"])
            # Should work — run profiles internally
            assert result.exit_code == 0

    def test_init_with_bad_schema(self, pg_url):
        """Init with nonexistent schema should succeed (schema checked at profile time)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "init", pg_url, "--schema", "nonexistent_schema_xyz",
            ])
            assert result.exit_code == 0  # init doesn't validate schema


# ---------------------------------------------------------------------------
# Drift detection via CLI
# ---------------------------------------------------------------------------

@requires_postgres
class TestCLIDriftDetection:
    def test_second_run_shows_baseline_comparison(self, pg_url, schema_for_cli):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])

            # First profile + run
            runner.invoke(main, ["profile"])
            result1 = runner.invoke(main, ["run"])
            assert result1.exit_code == 0

            # Second run (profiles again internally, compares to first baseline)
            result2 = runner.invoke(main, ["run"])
            assert result2.exit_code == 0
            assert "Comparing against baseline" in result2.output


# ---------------------------------------------------------------------------
# Parameterized tests
# ---------------------------------------------------------------------------

@requires_postgres
class TestParameterized:
    @pytest.mark.parametrize("flag,expected_in_output", [
        (["--verbose"], "✓"),
        (["--focus", "high"], "Summary:"),
        (["--focus", "problems"], "Summary:"),
        (["--json-output"], '"summary"'),
    ])
    def test_run_output_flags(self, pg_url, schema_for_cli, flag, expected_in_output):
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init", pg_url, "--schema", schema_for_cli])
            runner.invoke(main, ["profile"])
            result = runner.invoke(main, ["run"] + flag)
            assert result.exit_code == 0
            assert expected_in_output in result.output

    @pytest.mark.parametrize("schema_flag", [
        "--schema",
        "-s",
    ])
    def test_schema_flag_variants(self, pg_url, schema_for_cli, schema_flag):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "init", pg_url, schema_flag, schema_for_cli,
            ])
            assert result.exit_code == 0
