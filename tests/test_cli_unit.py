"""Tests for the CLI commands (non-database tests only)."""

from click.testing import CliRunner

from dqlens.cli import main


class TestCLI:
    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Auto-generated data quality testing" in result.output

    def test_init_creates_config(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "init", "postgres://localhost/testdb", "--schema", "public"
            ])
            assert result.exit_code == 0
            assert "Initialized .dqlens/" in result.output
            assert "dqlens profile" in result.output

    def test_init_with_tables(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "init", "postgres://localhost/testdb",
                "--tables", "orders,customers",
                "--exclude", "tmp_*",
            ])
            assert result.exit_code == 0
            assert "orders, customers" in result.output
            assert "tmp_*" in result.output

    def test_run_without_init_fails(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["run"])
            assert result.exit_code != 0
            assert "dqlens init" in result.output

    def test_profile_without_init_fails(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["profile"])
            assert result.exit_code != 0

    def test_ignore_command(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            # Init first
            runner.invoke(main, ["init", "postgres://localhost/testdb"])
            # Then ignore
            result = runner.invoke(main, ["ignore", "orders.email.null_anomaly"])
            assert result.exit_code == 0
            assert "Ignored: orders.email.null_anomaly" in result.output

    def test_init_masks_password(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "init", "postgres://user:secret@localhost/db"
            ])
            assert result.exit_code == 0
            assert "secret" not in result.output
            assert "***" in result.output
