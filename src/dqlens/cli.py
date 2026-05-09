"""DQLens CLI — the user-facing entry point.

Commands:
    dqlens init      — Initialize .dqlens/ directory with connection config
    dqlens profile   — Profile database tables and save baseline
    dqlens run       — Run tests against live database, show problems
    dqlens diff      — Compare two profiles to see what changed
    dqlens ignore    — Suppress a known finding
"""

from __future__ import annotations

import json
import sys

import click

from dqlens import __version__


@click.group()
@click.version_option(version=__version__, prog_name="dqlens")
def main():
    """DQLens — Auto-generated data quality testing.

    Find data problems automatically. No config, no test writing.
    """
    pass


@main.command()
@click.argument("connection_url")
@click.option("--schema", "-s", default="public", help="Database schema to profile.")
@click.option(
    "--tables", "-t", default=None,
    help="Comma-separated list of tables to include.",
)
@click.option(
    "--exclude", "-e", default=None,
    help="Comma-separated list of table patterns to exclude (supports globs).",
)
def init(connection_url: str, schema: str, tables: str | None, exclude: str | None):
    """Initialize DQLens in the current directory.

    Stores connection config in .dqlens/config.yaml so subsequent
    commands don't need the connection string repeated.

    Example:
        dqlens init postgres://localhost/mydb --schema public
    """
    from dqlens.config import init_dqlens_dir

    table_list = [t.strip() for t in tables.split(",")] if tables else []
    exclude_list = [t.strip() for t in exclude.split(",")] if exclude else []

    config = init_dqlens_dir(
        connection_url=connection_url,
        schema=schema,
        tables=table_list,
        exclude_tables=exclude_list,
    )

    click.echo(f"Initialized .dqlens/ directory.")
    click.echo(f"  Connection: {_mask_url(connection_url)}")
    click.echo(f"  Schema: {schema}")
    if table_list:
        click.echo(f"  Tables: {', '.join(table_list)}")
    if exclude_list:
        click.echo(f"  Exclude: {', '.join(exclude_list)}")
    click.echo()
    click.echo("Next: run 'dqlens profile' to profile your database.")


@main.command()
@click.option(
    "--connection", "-c", default=None,
    help="Override connection URL (default: from .dqlens/config.yaml).",
)
@click.option("--schema", "-s", default=None, help="Override schema.")
@click.option("--tables", "-t", default=None, help="Comma-separated tables to profile.")
@click.option("--exclude", "-e", default=None, help="Comma-separated table patterns to exclude.")
@click.option("--quick", "-q", is_flag=True, help="Quick mode: sample data instead of full scan.")
def profile(
    connection: str | None,
    schema: str | None,
    tables: str | None,
    exclude: str | None,
    quick: bool,
):
    """Profile database tables and save a baseline.

    Connects to your database, profiles every table (row counts, nulls,
    uniqueness, patterns, foreign keys, freshness), and saves the results
    as a baseline for future drift comparison.

    Example:
        dqlens profile
        dqlens profile --tables orders,customers
        dqlens profile --quick
    """
    from rich.console import Console

    from dqlens import profiler_v2
    from dqlens.baseline import get_baseline_count, save_profile
    from dqlens.config import load_config
    from dqlens.connectors import get_connector

    console = Console()

    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        if not connection:
            click.echo(
                "Error: No .dqlens directory found. Either run 'dqlens init' first "
                "or pass --connection.",
                err=True,
            )
            sys.exit(1)
        # Create a temporary config from CLI args
        from dqlens.config import DQLensConfig
        config = DQLensConfig(
            connection_url=connection,
            schema=schema or "public",
        )

    # Override config with CLI args
    conn_url = connection or config.connection_url
    schema_name = schema or config.schema
    table_list = (
        [t.strip() for t in tables.split(",")]
        if tables
        else config.tables or None
    )
    exclude_list = (
        [t.strip() for t in exclude.split(",")]
        if exclude
        else config.exclude_tables or None
    )

    mode_label = "[yellow]quick mode (sampled)[/yellow]" if quick else ""
    console.print(f"\n[bold]Profiling schema '{schema_name}'...[/bold] {mode_label}\n")

    def _progress(table_name: str, current: int, total: int) -> None:
        console.print(f"  Profiling [cyan]{table_name}[/cyan]... ({current}/{total})")

    try:
        db = get_connector(conn_url)
        with db.connect() as conn:
            db_profile = profiler_v2.profile_database(
                db=db,
                conn=conn,
                schema=schema_name,
                tables=table_list,
                exclude_tables=exclude_list,
                quick=quick,
                progress_callback=_progress,
            )
    except Exception as e:
        click.echo(f"Error connecting to database: {e}", err=True)
        sys.exit(1)

    # Save baseline
    filepath = save_profile(db_profile)
    baseline_count = get_baseline_count()

    # Generate inspectable tests.yaml
    from dqlens.testgen import generate_tests, save_tests

    tests = generate_tests(db_profile)
    tests_path = save_tests(tests)
    total_checks = sum(len(t["checks"]) for t in tests["tables"])

    # Print summary
    for table in db_profile.tables:
        table_checks = next(
            (t for t in tests["tables"]
             if t["table"] == table.full_name),
            None,
        )
        check_count = len(table_checks["checks"]) if table_checks else 0
        console.print(
            f"  [bold]{table.full_name}[/bold]  "
            f"{table.row_count:,} rows, {len(table.columns)} columns, "
            f"{check_count} checks"
        )

    console.print(
        f"\n[green]Profile saved.[/green] "
        f"({baseline_count} baseline{'s' if baseline_count != 1 else ''} stored)"
    )
    console.print(
        f"[green]Generated {total_checks} checks[/green] → "
        f"[bold].dqlens/tests.yaml[/bold]"
    )
    console.print(
        "[dim]Review and edit tests.yaml to customize checks. "
        "Delete any you don't need.[/dim]"
    )
    if baseline_count >= 2:
        console.print("[dim]Drift comparison available on next 'dqlens run'.[/dim]")
    console.print("\nNext: run [bold]dqlens run[/bold] to check for problems.\n")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show all tests including passing ones.")
@click.option(
    "--focus", "-f", type=click.Choice(["problems", "high"]), default=None,
    help="Filter findings: 'problems' (default) or 'high' (HIGH severity only).",
)
@click.option("--ci", is_flag=True, help="CI mode: exit code 1 on any failure.")
@click.option("--json-output", "json_out", is_flag=True, help="Output results as JSON.")
@click.option("--connection", "-c", default=None, help="Override connection URL.")
@click.option("--schema", "-s", default=None, help="Override schema.")
def run(
    verbose: bool,
    focus: str | None,
    ci: bool,
    json_out: bool,
    connection: str | None,
    schema: str | None,
):
    """Run data quality checks and show problems.

    Compares the current database state against the saved baseline
    to detect drift, anomalies, and structural problems.

    By default, only problems are shown. Use --verbose to see all checks.

    Examples:
        dqlens run
        dqlens run --verbose
        dqlens run --focus high
        dqlens run --ci --json-output
    """
    from rich.console import Console

    from dqlens import profiler_v2
    from dqlens.baseline import (load_latest_profile, load_previous_profile,
                                 save_profile)
    from dqlens.config import load_config, load_ignores
    from dqlens.connectors import get_connector
    from dqlens.engine import run_checks
    from dqlens.output import format_json_result, print_run_result

    console = Console()

    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        if not connection:
            click.echo(
                "Error: No .dqlens directory found. Run 'dqlens init' first.",
                err=True,
            )
            sys.exit(1)
        from dqlens.config import DQLensConfig
        config = DQLensConfig(connection_url=connection, schema=schema or "public")

    conn_url = connection or config.connection_url
    schema_name = schema or config.schema
    ignores = load_ignores()

    # Load baseline for comparison
    baseline = load_latest_profile()

    if not json_out:
        console.print(f"\n[bold]Running checks on schema '{schema_name}'...[/bold]")
        if baseline:
            console.print(
                f"[dim]Comparing against baseline from "
                f"{baseline.profiled_at.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
            )

    try:
        db = get_connector(conn_url)
        with db.connect() as conn:
            # Profile current state
            current = profiler_v2.profile_database(
                db=db,
                conn=conn,
                schema=schema_name,
                tables=config.tables or None,
                exclude_tables=config.exclude_tables or None,
            )

            # Run checks via rule engine
            result = run_checks(
                current=current,
                baseline=baseline,
                conn=conn,
                ignores=ignores,
            )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Save current profile as new baseline
    save_profile(current)

    # Output
    if json_out:
        print(json.dumps(format_json_result(result), indent=2))
    else:
        print_run_result(result, verbose=verbose, focus=focus)

    # CI exit code
    if ci and result.has_problems:
        sys.exit(1)


@main.command()
@click.option("--json-output", "json_out", is_flag=True, help="Output diff as JSON.")
def diff(json_out: bool):
    """Compare the two most recent profiles to see what changed.

    Shows schema changes (tables/columns added/removed), row count
    shifts, null rate drift, uniqueness changes, and value range shifts.

    Examples:
        dqlens diff
        dqlens diff --json-output
    """
    from dqlens.baseline import load_latest_profile, load_previous_profile
    from dqlens.diff import diff_profiles, format_diff_json, format_diff_text

    latest = load_latest_profile()
    previous = load_previous_profile()

    if latest is None:
        click.echo("Error: No profiles found. Run 'dqlens profile' first.", err=True)
        sys.exit(1)

    if previous is None:
        click.echo(
            "Error: Only one profile found. Run 'dqlens profile' again to "
            "create a second profile for comparison.",
            err=True,
        )
        sys.exit(1)

    result = diff_profiles(before=previous, after=latest)

    if json_out:
        print(json.dumps(format_diff_json(result), indent=2))
    else:
        click.echo(format_diff_text(result))


@main.command()
@click.argument("key")
def ignore(key: str):
    """Suppress a known finding.

    The key format is: table.column.category
    Example: orders.email.null_anomaly

    Ignored findings are tracked in .dqlens/ignores.yaml (not deleted).
    """
    from dqlens.config import add_ignore

    add_ignore(key)
    click.echo(f"Ignored: {key}")
    click.echo("This finding will be suppressed in future runs.")
    click.echo("To undo: dqlens unignore " + key)


@main.command("ignore-list")
def ignore_list():
    """Show all currently ignored findings."""
    from dqlens.config import load_ignores

    ignores = load_ignores()
    if not ignores:
        click.echo("No ignored findings.")
        return

    click.echo(f"{len(ignores)} ignored finding(s):\n")
    for key in sorted(ignores):
        click.echo(f"  {key}")
    click.echo(f"\nTo remove: dqlens unignore <key>")


@main.command()
@click.argument("key")
def unignore(key: str):
    """Remove a suppressed finding (re-enable it).

    Example: dqlens unignore orders.email.null_anomaly
    """
    from pathlib import Path

    import yaml

    from dqlens.config import IGNORES_FILE, get_dqlens_dir, load_ignores

    ignores = load_ignores()
    if key not in ignores:
        click.echo(f"Key '{key}' is not in the ignore list.", err=True)
        sys.exit(1)

    ignores.discard(key)
    ignores_path = get_dqlens_dir() / IGNORES_FILE
    with open(ignores_path, "w") as f:
        yaml.dump({"ignored": sorted(ignores)}, f, default_flow_style=False)

    click.echo(f"Removed: {key}")
    click.echo("This finding will appear again in future runs.")


def _mask_url(url: str) -> str:
    """Mask password in a connection URL for display."""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


if __name__ == "__main__":
    main()
