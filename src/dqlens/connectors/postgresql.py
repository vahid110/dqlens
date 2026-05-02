"""PostgreSQL connector — uses system catalogs for fast profiling."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras

from dqlens.connectors.base import BaseConnector


class PostgreSQLConnector(BaseConnector):
    """PostgreSQL connector using pg_stats, pg_class, information_schema."""

    def __init__(self, connection_url: str):
        self.connection_url = connection_url

    @contextmanager
    def connect(self) -> Generator[Any, None, None]:
        conn = psycopg2.connect(self.connection_url)
        conn.set_session(readonly=True, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()

    def list_tables(self, conn: Any, schema: str) -> list[dict[str, Any]]:
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

    def get_columns(self, conn: Any, schema: str, table: str) -> list[dict[str, Any]]:
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

    def get_foreign_keys(self, conn: Any, schema: str) -> list[dict[str, str]]:
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

    def get_primary_keys(self, conn: Any, schema: str) -> dict[str, list[str]]:
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

    def get_unique_indexes(self, conn: Any, schema: str) -> dict[str, set[str]]:
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

    def get_exact_row_count(self, conn: Any, schema: str, table: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {psycopg2.extensions.quote_ident(schema, cur)}"
                f".{psycopg2.extensions.quote_ident(table, cur)}"
            )
            result = cur.fetchone()
            return result[0] if result else 0

    def get_column_details(
        self, conn: Any, schema: str, table: str, column: str, data_type: str,
    ) -> dict[str, Any]:
        sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
        tbl = psycopg2.extensions.quote_ident(table, conn.cursor())
        col = psycopg2.extensions.quote_ident(column, conn.cursor())

        result: dict[str, Any] = {}

        with conn.cursor() as cur:
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

            if self.is_numeric_type(data_type):
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

            elif self.is_temporal_type(data_type):
                cur.execute(
                    f"SELECT MIN({col}), MAX({col}) "
                    f"FROM {sch}.{tbl} WHERE {col} IS NOT NULL"
                )
                row = cur.fetchone()
                if row:
                    result["min_value"] = str(row[0]) if row[0] else None
                    result["max_value"] = str(row[1]) if row[1] else None

            elif self.is_text_type(data_type):
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

    def get_timestamp_columns(self, conn: Any, schema: str, table: str) -> list[str]:
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
        self, conn: Any, schema: str, table: str, column: str,
    ) -> str | None:
        sch = psycopg2.extensions.quote_ident(schema, conn.cursor())
        tbl = psycopg2.extensions.quote_ident(table, conn.cursor())
        col = psycopg2.extensions.quote_ident(column, conn.cursor())

        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX({col}) FROM {sch}.{tbl}")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None

    def check_fk_integrity(
        self, conn: Any, schema: str,
        source_table: str, source_column: str,
        target_table: str, target_column: str,
    ) -> dict[str, int]:
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
                return {"total": row[0], "non_null": row[1], "orphaned": row[2]}
            return {"total": 0, "non_null": 0, "orphaned": 0}

    def sample_text_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int = 1000,
    ) -> list[str]:
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


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
