"""MySQL connector — profiles MySQL and MariaDB databases."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import pymysql
import pymysql.cursors

from dqlens.connectors.base import BaseConnector


class MySQLConnector(BaseConnector):
    """MySQL/MariaDB connector using information_schema."""

    def __init__(self, connection_url: str):
        self.connection_url = connection_url
        self._params = _parse_mysql_url(connection_url)

    @contextmanager
    def connect(self) -> Generator[Any, None, None]:
        conn = pymysql.connect(
            host=self._params["host"],
            port=self._params["port"],
            database=self._params["database"],
            user=self._params["user"],
            password=self._params["password"],
            connect_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
            read_timeout=30,
        )
        try:
            yield conn
        finally:
            conn.close()

    def list_tables(self, conn: Any, schema: str) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME AS table_name, "
                "TABLE_ROWS AS estimated_rows, "
                "DATA_LENGTH + INDEX_LENGTH AS total_bytes "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_NAME",
                (schema,),
            )
            return [
                {
                    "table_name": row["table_name"],
                    "estimated_rows": row["estimated_rows"] or 0,
                    "total_bytes": row["total_bytes"] or 0,
                }
                for row in cur.fetchall()
            ]

    def get_columns(self, conn: Any, schema: str, table: str) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME AS column_name, "
                "DATA_TYPE AS data_type, "
                "IS_NULLABLE AS is_nullable, "
                "COLUMN_DEFAULT AS column_default, "
                "ORDINAL_POSITION AS ordinal_position "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION",
                (schema, table),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_foreign_keys(self, conn: Any, schema: str) -> list[dict[str, str]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT "
                "  kcu.TABLE_NAME AS source_table, "
                "  kcu.COLUMN_NAME AS source_column, "
                "  kcu.REFERENCED_TABLE_NAME AS target_table, "
                "  kcu.REFERENCED_COLUMN_NAME AS target_column, "
                "  kcu.CONSTRAINT_NAME AS constraint_name "
                "FROM information_schema.KEY_COLUMN_USAGE kcu "
                "WHERE kcu.TABLE_SCHEMA = %s "
                "  AND kcu.REFERENCED_TABLE_NAME IS NOT NULL "
                "ORDER BY kcu.TABLE_NAME, kcu.COLUMN_NAME",
                (schema,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_primary_keys(self, conn: Any, schema: str) -> dict[str, list[str]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = %s AND CONSTRAINT_NAME = 'PRIMARY' "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (schema,),
            )
            result: dict[str, list[str]] = {}
            for row in cur.fetchall():
                table = row["TABLE_NAME"]
                if table not in result:
                    result[table] = []
                result[table].append(row["COLUMN_NAME"])
            return result

    def get_unique_indexes(self, conn: Any, schema: str) -> dict[str, set[str]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME "
                "FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND NON_UNIQUE = 0 "
                "  AND INDEX_NAME != 'PRIMARY' "
                "ORDER BY TABLE_NAME",
                (schema,),
            )
            result: dict[str, set[str]] = {}
            for row in cur.fetchall():
                table = row["TABLE_NAME"]
                if table not in result:
                    result[table] = set()
                result[table].add(row["COLUMN_NAME"])
            return result

    def get_exact_row_count(self, conn: Any, schema: str, table: str) -> int:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM `{table}`")
            row = cur.fetchone()
            return row["cnt"] if row else 0

    def get_column_details(
        self, conn: Any, schema: str, table: str, column: str, data_type: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        col_q = f"`{column}`"

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nulls, "
                f"COUNT(DISTINCT {col_q}) AS distinct_count "
                f"FROM `{table}`"
            )
            row = cur.fetchone()
            if row:
                result["total"] = row["total"]
                result["null_count"] = row["nulls"] or 0
                result["distinct_count"] = row["distinct_count"]

            if self.is_numeric_type(data_type):
                cur.execute(
                    f"SELECT MIN({col_q}) AS mn, MAX({col_q}) AS mx, "
                    f"AVG({col_q}) AS av, STDDEV({col_q}) AS sd "
                    f"FROM `{table}` WHERE {col_q} IS NOT NULL"
                )
                row = cur.fetchone()
                if row:
                    result["min_value"] = _safe_float(row["mn"])
                    result["max_value"] = _safe_float(row["mx"])
                    result["mean_value"] = _safe_float(row["av"])
                    result["stddev"] = _safe_float(row["sd"])

            elif self.is_temporal_type(data_type):
                cur.execute(
                    f"SELECT MIN({col_q}) AS mn, MAX({col_q}) AS mx "
                    f"FROM `{table}` WHERE {col_q} IS NOT NULL"
                )
                row = cur.fetchone()
                if row:
                    result["min_value"] = str(row["mn"]) if row["mn"] else None
                    result["max_value"] = str(row["mx"]) if row["mx"] else None

            elif self.is_text_type(data_type):
                cur.execute(
                    f"SELECT MIN(CHAR_LENGTH({col_q})) AS mn, "
                    f"MAX(CHAR_LENGTH({col_q})) AS mx, "
                    f"AVG(CHAR_LENGTH({col_q})) AS av "
                    f"FROM `{table}` WHERE {col_q} IS NOT NULL"
                )
                row = cur.fetchone()
                if row:
                    result["min_length"] = row["mn"]
                    result["max_length"] = row["mx"]
                    result["avg_length"] = _safe_float(row["av"])

        return result

    def get_timestamp_columns(self, conn: Any, schema: str, table: str) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "  AND DATA_TYPE IN ('datetime', 'timestamp', 'date') "
                "ORDER BY ORDINAL_POSITION",
                (schema, table),
            )
            return [row["COLUMN_NAME"] for row in cur.fetchall()]

    def get_latest_timestamp(
        self, conn: Any, schema: str, table: str, column: str,
    ) -> str | None:
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{column}`) AS mx FROM `{table}`")
            row = cur.fetchone()
            return str(row["mx"]) if row and row["mx"] else None

    def check_fk_integrity(
        self, conn: Any, schema: str,
        source_table: str, source_column: str,
        target_table: str, target_column: str,
    ) -> dict[str, int]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT "
                f"  COUNT(*) AS total, "
                f"  SUM(CASE WHEN `{source_column}` IS NOT NULL THEN 1 ELSE 0 END) AS non_null, "
                f"  SUM(CASE WHEN `{source_column}` IS NOT NULL "
                f"    AND `{source_column}` NOT IN "
                f"    (SELECT `{target_column}` FROM `{target_table}`) "
                f"    THEN 1 ELSE 0 END) AS orphaned "
                f"FROM `{source_table}`"
            )
            row = cur.fetchone()
            if row:
                return {
                    "total": row["total"],
                    "non_null": row["non_null"] or 0,
                    "orphaned": row["orphaned"] or 0,
                }
            return {"total": 0, "non_null": 0, "orphaned": 0}

    def sample_text_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int = 1000,
    ) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT CAST(`{column}` AS CHAR) AS val "
                f"FROM `{table}` WHERE `{column}` IS NOT NULL LIMIT %s",
                (limit,),
            )
            return [row["val"] for row in cur.fetchall()]

    def get_sampled_column_details(
        self, conn: Any, schema: str, table: str, column: str,
        data_type: str, sample_size: int = 10000,
    ) -> dict[str, Any]:
        """MySQL doesn't have TABLESAMPLE. Use ORDER BY RAND() LIMIT."""
        result: dict[str, Any] = {}
        col_q = f"`{column}`"
        sample_q = f"(SELECT * FROM `{table}` ORDER BY RAND() LIMIT {sample_size}) AS sample"

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nulls, "
                f"COUNT(DISTINCT {col_q}) AS distinct_count "
                f"FROM {sample_q}"
            )
            row = cur.fetchone()
            if row:
                result["total"] = row["total"]
                result["null_count"] = row["nulls"] or 0
                result["distinct_count"] = row["distinct_count"]

            if self.is_numeric_type(data_type):
                cur.execute(
                    f"SELECT MIN({col_q}) AS mn, MAX({col_q}) AS mx, "
                    f"AVG({col_q}) AS av, STDDEV({col_q}) AS sd "
                    f"FROM {sample_q} WHERE {col_q} IS NOT NULL"
                )
                row = cur.fetchone()
                if row:
                    result["min_value"] = _safe_float(row["mn"])
                    result["max_value"] = _safe_float(row["mx"])
                    result["mean_value"] = _safe_float(row["av"])
                    result["stddev"] = _safe_float(row["sd"])

        return result

    def is_numeric_type(self, data_type: str) -> bool:
        return data_type.lower() in {
            "int", "integer", "bigint", "smallint", "tinyint", "mediumint",
            "decimal", "numeric", "float", "double", "real",
        }

    def is_temporal_type(self, data_type: str) -> bool:
        return data_type.lower() in {"datetime", "timestamp", "date", "time", "year"}

    def is_text_type(self, data_type: str) -> bool:
        return data_type.lower() in {
            "varchar", "char", "text", "tinytext", "mediumtext", "longtext",
            "enum", "set",
        }


def _parse_mysql_url(url: str) -> dict[str, Any]:
    """Parse a MySQL connection URL into components.

    Supports: mysql://user:pass@host:port/database
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "database": (parsed.path or "/").lstrip("/") or "mysql",
        "user": parsed.username or "root",
        "password": parsed.password or "",
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
