"""PostgreSQL database connector and schema introspection.

Uses PostgreSQL system catalogs (pg_stats, pg_class, information_schema)
for fast, accurate profiling without full table scans where possible.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Generator
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras


@contextmanager
def connect(connection_url: str) -> Generator[psycopg2.extensions.connection, None, None]:
    """Open a database connection from a URL."""
    conn = psycopg2.connect(connection_url)
    conn.set_session(readonly=True, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def list_tables(conn: psycopg2.extensions.connection, schema: str = "public") -> list[dict[str, Any]]:
    """List all tables in a schema with row count estimates from pg_class."""
    query = """
        SELECT
            c.relname AS table_name,
            c.reltuples::bigint AS estimated_rows,
            pg_total_relation_size(c.oid) AS total_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relkind = 'r'
        ORDER BY c.relname
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema,))
        return [dict(row) for row in cur.fetchall()]


def get_columns(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> list[dict[str, Any]]:
    """Get column metadata from information_schema."""
    query = """
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema, table))
        return [dict(row) for row in cur.fetchall()]


def get_column_stats(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> list[dict[str, Any]]:
    """Get pre-computed column statistics from pg_stats.

    pg_stats contains statistics collected by ANALYZE — this is fast because
    it reads cached stats, not the actual table data.
    """
    query = """
        SELECT
            attname AS column_name,
            null_frac,
            n_distinct,
            most_common_vals::text,
            most_common_freqs::text,
            correlation
        FROM pg_stats
        WHERE schemaname = %s AND tablename = %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema, table))
        return [dict(row) for row in cur.fetchall()]


def get_foreign_keys(
    conn: psycopg2.extensions.connection, schema: str
) -> list[dict[str, str]]:
    """Discover all foreign key relationships in a schema."""
    query = """
        SELECT
            tc.table_name AS source_table,
            kcu.column_name AS source_column,
            ccu.table_name AS target_table,
            ccu.column_name AS target_column,
            tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = %s
        ORDER BY tc.table_name, kcu.column_name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema,))
        return [dict(row) for row in cur.fetchall()]


def get_primary_keys(
    conn: psycopg2.extensions.connection, schema: str
) -> dict[str, list[str]]:
    """Get primary key columns for all tables in a schema.

    Returns a dict mapping table_name -> list of PK column names.
    """
    query = """
        SELECT
            tc.table_name,
            kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s
        ORDER BY tc.table_name, kcu.ordinal_position
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema,))
        result: dict[str, list[str]] = {}
        for row in cur.fetchall():
            table = row["table_name"]
            if table not in result:
                result[table] = []
            result[table].append(row["column_name"])
        return result


def get_unique_indexes(
    conn: psycopg2.extensions.connection, schema: str
) -> dict[str, set[str]]:
    """Get columns with unique indexes.

    Returns a dict mapping table_name -> set of column names with unique indexes.
    """
    query = """
        SELECT
            t.relname AS table_name,
            a.attname AS column_name
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)
        WHERE i.indisunique = true
          AND n.nspname = %s
          AND array_length(i.indkey, 1) = 1
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (schema,))
        result: dict[str, set[str]] = {}
        for row in cur.fetchall():
            table = row["table_name"]
            if table not in result:
                result[table] = set()
            result[table].add(row["column_name"])
        return result


def get_exact_row_count(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> int:
    """Get exact row count (requires table scan)."""
    # Use identifier quoting to prevent SQL injection
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {psycopg2.extensions.quote_ident(schema, cur)}"
            f".{psycopg2.extensions.quote_ident(table, cur)}"
        )
        result = cur.fetchone()
        return result[0] if result else 0


def get_column_details(
    conn: psycopg2.extensions.connection,
    schema: str,
    table: str,
    column: str,
    data_type: str,
) -> dict[str, Any]:
    """Get detailed statistics for a single column via targeted queries.

    This runs actual queries against the table for stats that pg_stats
    doesn't provide (exact null counts, min/max for non-numeric types, etc.).
    """
    sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
    tbl = psycopg2.extensions.quote_ident(table, conn.cursor())
    col = psycopg2.extensions.quote_ident(column, conn.cursor())

    result: dict[str, Any] = {}

    with conn.cursor() as cur:
        # Null count and total count
        cur.execute(
            f"SELECT COUNT(*) AS total, "
            f"COUNT(*) FILTER (WHERE {col} IS NULL) AS nulls, "
            f"COUNT(DISTINCT {col}) AS distinct_count "
            f"FROM {sch}.{tbl}"
        )
        row = cur.fetchone()
        if row:
            result["total"] = row[0]
            result["null_count"] = row[1]
            result["distinct_count"] = row[2]

        # Min/max/mean for numeric types
        if _is_numeric_type(data_type):
            cur.execute(
                f"SELECT MIN({col}), MAX({col}), AVG({col}), STDDEV({col}) "
                f"FROM {sch}.{tbl} WHERE {col} IS NOT NULL"
            )
            row = cur.fetchone()
            if row:
                result["min_value"] = _safe_float(row[0])
                result["max_value"] = _safe_float(row[1])
                result["mean_value"] = _safe_float(row[2])
                result["stddev"] = _safe_float(row[3])

        # Min/max for date/timestamp types
        elif _is_temporal_type(data_type):
            cur.execute(
                f"SELECT MIN({col}), MAX({col}) "
                f"FROM {sch}.{tbl} WHERE {col} IS NOT NULL"
            )
            row = cur.fetchone()
            if row:
                result["min_value"] = str(row[0]) if row[0] else None
                result["max_value"] = str(row[1]) if row[1] else None

        # Min/max length for text types
        elif _is_text_type(data_type):
            cur.execute(
                f"SELECT MIN(LENGTH({col})), MAX(LENGTH({col})), AVG(LENGTH({col})) "
                f"FROM {sch}.{tbl} WHERE {col} IS NOT NULL"
            )
            row = cur.fetchone()
            if row:
                result["min_length"] = row[0]
                result["max_length"] = row[1]
                result["avg_length"] = _safe_float(row[2])

    return result


def get_timestamp_columns(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> list[str]:
    """Find timestamp/date columns in a table for freshness detection."""
    query = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
          AND data_type IN (
              'timestamp without time zone',
              'timestamp with time zone',
              'date'
          )
        ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema, table))
        return [row[0] for row in cur.fetchall()]


def get_latest_timestamp(
    conn: psycopg2.extensions.connection,
    schema: str,
    table: str,
    column: str,
) -> str | None:
    """Get the most recent timestamp value from a column."""
    sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
    tbl = psycopg2.extensions.quote_ident(table, conn.cursor())
    col = psycopg2.extensions.quote_ident(column, conn.cursor())

    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX({col}) FROM {sch}.{tbl}")
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else None


def check_fk_integrity(
    conn: psycopg2.extensions.connection,
    schema: str,
    source_table: str,
    source_column: str,
    target_table: str,
    target_column: str,
) -> dict[str, int]:
    """Check foreign key integrity — find orphaned rows.

    Returns dict with 'total', 'matched', 'orphaned' counts.
    """
    sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
    src_tbl = psycopg2.extensions.quote_ident(source_table, conn.cursor())
    src_col = psycopg2.extensions.quote_ident(source_column, conn.cursor())
    tgt_tbl = psycopg2.extensions.quote_ident(target_table, conn.cursor())
    tgt_col = psycopg2.extensions.quote_ident(target_column, conn.cursor())

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT "
            f"  COUNT(*) AS total, "
            f"  COUNT(*) FILTER (WHERE {src_col} IS NOT NULL) AS non_null, "
            f"  COUNT(*) FILTER (WHERE {src_col} IS NOT NULL AND {src_col} NOT IN "
            f"    (SELECT {tgt_col} FROM {sch}.{tgt_tbl})) AS orphaned "
            f"FROM {sch}.{src_tbl}"
        )
        row = cur.fetchone()
        if row:
            return {
                "total": row[0],
                "non_null": row[1],
                "orphaned": row[2],
            }
        return {"total": 0, "non_null": 0, "orphaned": 0}


def sample_text_values(
    conn: psycopg2.extensions.connection,
    schema: str,
    table: str,
    column: str,
    limit: int = 1000,
) -> list[str]:
    """Sample non-null text values from a column for pattern detection."""
    sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
    tbl = psycopg2.extensions.quote_ident(table, conn.cursor())
    col = psycopg2.extensions.quote_ident(column, conn.cursor())

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {col}::text FROM {sch}.{tbl} "
            f"WHERE {col} IS NOT NULL LIMIT %s",
            (limit,),
        )
        return [row[0] for row in cur.fetchall()]


def _is_numeric_type(data_type: str) -> bool:
    numeric_types = {
        "integer", "bigint", "smallint", "numeric", "decimal",
        "real", "double precision", "float", "int", "int4", "int8",
        "float4", "float8", "serial", "bigserial",
    }
    return data_type.lower() in numeric_types


def _is_temporal_type(data_type: str) -> bool:
    temporal_types = {
        "timestamp without time zone", "timestamp with time zone",
        "date", "time without time zone", "time with time zone",
        "timestamp", "timestamptz",
    }
    return data_type.lower() in temporal_types


def _is_text_type(data_type: str) -> bool:
    text_types = {
        "character varying", "varchar", "character", "char", "text",
        "name", "citext",
    }
    return data_type.lower() in text_types


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
