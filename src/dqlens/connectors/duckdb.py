"""DuckDB connector — profiles local .duckdb files or in-memory databases."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

try:
    import duckdb
except ImportError:
    duckdb = None  # type: ignore[assignment]

from dqlens.connectors.base import BaseConnector


class DuckDBConnector(BaseConnector):
    """DuckDB connector for local database files."""

    def __init__(self, db_path: str):
        if duckdb is None:
            raise ImportError(
                "DuckDB is not installed. Install it with: pip install duckdb"
            )
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Generator[Any, None, None]:
        conn = duckdb.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def list_tables(self, conn: Any, schema: str) -> list[dict[str, Any]]:
        result = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ? AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            [schema],
        ).fetchall()
        tables = []
        for row in result:
            name = row[0]
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{schema}"."{name}"'
            ).fetchone()[0]
            tables.append({
                "table_name": name,
                "estimated_rows": count,
                "total_bytes": 0,
            })
        return tables

    def get_columns(self, conn: Any, schema: str, table: str) -> list[dict[str, Any]]:
        result = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "ordinal_position FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
        return [
            {
                "column_name": row[0],
                "data_type": row[1].lower(),
                "is_nullable": row[2],
                "column_default": row[3],
                "ordinal_position": row[4],
            }
            for row in result
        ]

    def get_foreign_keys(self, conn: Any, schema: str) -> list[dict[str, str]]:
        # DuckDB supports FK constraints via information_schema
        try:
            result = conn.execute(
                "SELECT tc.table_name, kcu.column_name, "
                "ccu.table_name AS target_table, ccu.column_name AS target_column, "
                "tc.constraint_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "AND tc.table_schema = ?",
                [schema],
            ).fetchall()
            return [
                {
                    "source_table": row[0],
                    "source_column": row[1],
                    "target_table": row[2],
                    "target_column": row[3],
                    "constraint_name": row[4],
                }
                for row in result
            ]
        except Exception:
            return []

    def get_primary_keys(self, conn: Any, schema: str) -> dict[str, list[str]]:
        try:
            result = conn.execute(
                "SELECT tc.table_name, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.constraint_type = 'PRIMARY KEY' "
                "AND tc.table_schema = ?",
                [schema],
            ).fetchall()
            pks: dict[str, list[str]] = {}
            for row in result:
                pks.setdefault(row[0], []).append(row[1])
            return pks
        except Exception:
            return {}

    def get_unique_indexes(self, conn: Any, schema: str) -> dict[str, set[str]]:
        try:
            result = conn.execute(
                "SELECT table_name, column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.constraint_type = 'UNIQUE' "
                "AND tc.table_schema = ?",
                [schema],
            ).fetchall()
            uniques: dict[str, set[str]] = {}
            for row in result:
                uniques.setdefault(row[0], set()).add(row[1])
            return uniques
        except Exception:
            return {}

    def get_exact_row_count(self, conn: Any, schema: str, table: str) -> int:
        return conn.execute(
            f'SELECT COUNT(*) FROM "{schema}"."{table}"'
        ).fetchone()[0]

    def get_column_details(
        self, conn: Any, schema: str, table: str, column: str, data_type: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        col_q = f'"{column}"'
        tbl_q = f'"{schema}"."{table}"'

        row = conn.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nulls, "
            f"COUNT(DISTINCT {col_q}) AS distinct_count "
            f"FROM {tbl_q}"
        ).fetchone()
        if row:
            result["total"] = row[0]
            result["null_count"] = row[1] or 0
            result["distinct_count"] = row[2]

        if self.is_numeric_type(data_type):
            row = conn.execute(
                f"SELECT MIN({col_q}), MAX({col_q}), AVG({col_q}), STDDEV({col_q}), "
                f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col_q}), "
                f"PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {col_q}), "
                f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col_q}), "
                f"PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {col_q}) "
                f"FROM {tbl_q} WHERE {col_q} IS NOT NULL"
            ).fetchone()
            if row:
                result["min_value"] = _safe_float(row[0])
                result["max_value"] = _safe_float(row[1])
                result["mean_value"] = _safe_float(row[2])
                result["stddev"] = _safe_float(row[3])
                result["p25"] = _safe_float(row[4])
                result["p50"] = _safe_float(row[5])
                result["p75"] = _safe_float(row[6])
                result["p95"] = _safe_float(row[7])

        elif self.is_temporal_type(data_type):
            row = conn.execute(
                f"SELECT MIN({col_q}), MAX({col_q}) "
                f"FROM {tbl_q} WHERE {col_q} IS NOT NULL"
            ).fetchone()
            if row:
                result["min_value"] = str(row[0]) if row[0] else None
                result["max_value"] = str(row[1]) if row[1] else None

        elif self.is_text_type(data_type):
            row = conn.execute(
                f"SELECT MIN(LENGTH({col_q})), MAX(LENGTH({col_q})), "
                f"AVG(LENGTH({col_q})), "
                f"SUM(CASE WHEN {col_q} = '' THEN 1 ELSE 0 END) "
                f"FROM {tbl_q} WHERE {col_q} IS NOT NULL"
            ).fetchone()
            if row:
                result["min_length"] = row[0]
                result["max_length"] = row[1]
                result["avg_length"] = _safe_float(row[2])
                result["empty_string_count"] = row[3] or 0

        return result

    def get_timestamp_columns(self, conn: Any, schema: str, table: str) -> list[str]:
        result = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? "
            "AND data_type IN ('timestamp', 'timestamp with time zone', "
            "'timestamp without time zone', 'date')",
            [schema, table],
        ).fetchall()
        return [row[0] for row in result]

    def get_latest_timestamp(
        self, conn: Any, schema: str, table: str, column: str,
    ) -> str | None:
        row = conn.execute(
            f'SELECT MAX("{column}") FROM "{schema}"."{table}"'
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def check_fk_integrity(
        self, conn: Any, schema: str,
        source_table: str, source_column: str,
        target_table: str, target_column: str,
    ) -> dict[str, int]:
        row = conn.execute(
            f'SELECT '
            f'  COUNT(*) AS total, '
            f'  SUM(CASE WHEN "{source_column}" IS NOT NULL THEN 1 ELSE 0 END) AS non_null, '
            f'  SUM(CASE WHEN "{source_column}" IS NOT NULL '
            f'    AND "{source_column}" NOT IN '
            f'    (SELECT "{target_column}" FROM "{schema}"."{target_table}") '
            f'    THEN 1 ELSE 0 END) AS orphaned '
            f'FROM "{schema}"."{source_table}"'
        ).fetchone()
        if row:
            return {"total": row[0], "non_null": row[1] or 0, "orphaned": row[2] or 0}
        return {"total": 0, "non_null": 0, "orphaned": 0}

    def sample_text_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int = 1000,
    ) -> list[str]:
        result = conn.execute(
            f'SELECT CAST("{column}" AS VARCHAR) FROM "{schema}"."{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {limit}'
        ).fetchall()
        return [row[0] for row in result]

    def get_most_common_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int = 20,
    ) -> list[tuple[Any, int]]:
        result = conn.execute(
            f'SELECT CAST("{column}" AS VARCHAR), COUNT(*) as cnt '
            f'FROM "{schema}"."{table}" '
            f'WHERE "{column}" IS NOT NULL '
            f'GROUP BY "{column}" ORDER BY cnt DESC LIMIT {limit}'
        ).fetchall()
        return [(row[0], row[1]) for row in result]

    def is_numeric_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        numeric = {
            "integer", "bigint", "smallint", "tinyint", "int",
            "float", "double", "real", "decimal", "numeric",
            "hugeint", "ubigint", "uinteger", "usmallint", "utinyint",
        }
        return dt in numeric

    def is_text_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        return dt in {"varchar", "text", "char", "bpchar", "string", "blob"}

    def is_temporal_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        return dt in {
            "timestamp", "timestamp with time zone",
            "timestamp without time zone", "date", "time",
            "timestamptz",
        }

    def get_sampled_column_details(
        self, conn: Any, schema: str, table: str, column: str,
        data_type: str, sample_size: int = 10000,
    ) -> dict[str, Any]:
        """DuckDB supports USING SAMPLE for fast sampling."""
        result: dict[str, Any] = {}
        col_q = f'"{column}"'
        tbl_q = f'"{schema}"."{table}"'
        # Use subquery to allow WHERE clause after sampling
        sample_subq = f'(SELECT * FROM {tbl_q} USING SAMPLE 10 PERCENT (bernoulli))'

        row = conn.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nulls, "
            f"COUNT(DISTINCT {col_q}) AS distinct_count "
            f"FROM {sample_subq}"
        ).fetchone()
        if row:
            result["total"] = row[0]
            result["null_count"] = row[1] or 0
            result["distinct_count"] = row[2]

        if self.is_numeric_type(data_type):
            row = conn.execute(
                f"SELECT MIN({col_q}), MAX({col_q}), AVG({col_q}), STDDEV({col_q}) "
                f"FROM {sample_subq} WHERE {col_q} IS NOT NULL"
            ).fetchone()
            if row:
                result["min_value"] = _safe_float(row[0])
                result["max_value"] = _safe_float(row[1])
                result["mean_value"] = _safe_float(row[2])
                result["stddev"] = _safe_float(row[3])

        elif self.is_text_type(data_type):
            row = conn.execute(
                f"SELECT MIN(LENGTH({col_q})), MAX(LENGTH({col_q})), AVG(LENGTH({col_q})) "
                f"FROM {sample_subq} WHERE {col_q} IS NOT NULL"
            ).fetchone()
            if row:
                result["min_length"] = row[0]
                result["max_length"] = row[1]
                result["avg_length"] = _safe_float(row[2])

        return result


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
