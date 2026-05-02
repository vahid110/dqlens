"""SQLite connector — profiles local .db files."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from dqlens.connectors.base import BaseConnector


class SQLiteConnector(BaseConnector):
    """SQLite connector for local database files."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Generator[Any, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def list_tables(self, conn: Any, schema: str) -> list[dict[str, Any]]:
        # SQLite ignores schema — everything is in "main"
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = []
        for row in cur.fetchall():
            name = row[0]
            count_cur = conn.execute(f'SELECT COUNT(*) FROM "{name}"')
            count = count_cur.fetchone()[0]
            tables.append({
                "table_name": name,
                "estimated_rows": count,
                "total_bytes": 0,
            })
        return tables

    def get_columns(self, conn: Any, schema: str, table: str) -> list[dict[str, Any]]:
        cur = conn.execute(f'PRAGMA table_info("{table}")')
        columns = []
        for row in cur.fetchall():
            columns.append({
                "column_name": row[1],  # name
                "data_type": _normalize_sqlite_type(row[2]),  # type
                "is_nullable": "NO" if row[3] else "YES",  # notnull
                "column_default": row[4],  # dflt_value
                "ordinal_position": row[0] + 1,  # cid (0-based)
            })
        return columns

    def get_foreign_keys(self, conn: Any, schema: str) -> list[dict[str, str]]:
        fks = []
        tables = self.list_tables(conn, schema)
        for t in tables:
            table_name = t["table_name"]
            cur = conn.execute(f'PRAGMA foreign_key_list("{table_name}")')
            for row in cur.fetchall():
                fks.append({
                    "source_table": table_name,
                    "source_column": row[3],  # from
                    "target_table": row[2],    # table
                    "target_column": row[4],   # to
                    "constraint_name": None,
                })
        return fks

    def get_primary_keys(self, conn: Any, schema: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        tables = self.list_tables(conn, schema)
        for t in tables:
            table_name = t["table_name"]
            cur = conn.execute(f'PRAGMA table_info("{table_name}")')
            pk_cols = [row[1] for row in cur.fetchall() if row[5] > 0]  # pk > 0
            if pk_cols:
                result[table_name] = pk_cols
        return result

    def get_unique_indexes(self, conn: Any, schema: str) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        tables = self.list_tables(conn, schema)
        for t in tables:
            table_name = t["table_name"]
            cur = conn.execute(f'PRAGMA index_list("{table_name}")')
            for idx_row in cur.fetchall():
                if idx_row[2]:  # unique
                    info_cur = conn.execute(f'PRAGMA index_info("{idx_row[1]}")')
                    cols = [r[2] for r in info_cur.fetchall()]
                    if len(cols) == 1:
                        if table_name not in result:
                            result[table_name] = set()
                        result[table_name].add(cols[0])
        return result

    def get_exact_row_count(self, conn: Any, schema: str, table: str) -> int:
        cur = conn.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]

    def get_column_details(
        self, conn: Any, schema: str, table: str, column: str, data_type: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        col_q = f'"{column}"'

        cur = conn.execute(
            f'SELECT COUNT(*) AS total, '
            f'SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) AS nulls, '
            f'COUNT(DISTINCT {col_q}) AS distinct_count '
            f'FROM "{table}"'
        )
        row = cur.fetchone()
        if row:
            result["total"] = row[0]
            result["null_count"] = row[1] or 0
            result["distinct_count"] = row[2]

        if self.is_numeric_type(data_type):
            cur = conn.execute(
                f'SELECT MIN({col_q}), MAX({col_q}), AVG({col_q}) '
                f'FROM "{table}" WHERE {col_q} IS NOT NULL'
            )
            row = cur.fetchone()
            if row:
                result["min_value"] = _safe_float(row[0])
                result["max_value"] = _safe_float(row[1])
                result["mean_value"] = _safe_float(row[2])

        elif self.is_text_type(data_type):
            cur = conn.execute(
                f'SELECT MIN(LENGTH({col_q})), MAX(LENGTH({col_q})), AVG(LENGTH({col_q})) '
                f'FROM "{table}" WHERE {col_q} IS NOT NULL'
            )
            row = cur.fetchone()
            if row:
                result["min_length"] = row[0]
                result["max_length"] = row[1]
                result["avg_length"] = _safe_float(row[2])

        return result

    def get_timestamp_columns(self, conn: Any, schema: str, table: str) -> list[str]:
        # SQLite doesn't have native timestamp types — look for column names
        cur = conn.execute(f'PRAGMA table_info("{table}")')
        ts_cols = []
        for row in cur.fetchall():
            col_name = row[1].lower()
            col_type = (row[2] or "").lower()
            if any(kw in col_name for kw in ("date", "time", "created", "updated", "modified")):
                ts_cols.append(row[1])
            elif any(kw in col_type for kw in ("date", "time", "timestamp")):
                ts_cols.append(row[1])
        return ts_cols

    def get_latest_timestamp(
        self, conn: Any, schema: str, table: str, column: str,
    ) -> str | None:
        cur = conn.execute(f'SELECT MAX("{column}") FROM "{table}"')
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else None

    def check_fk_integrity(
        self, conn: Any, schema: str,
        source_table: str, source_column: str,
        target_table: str, target_column: str,
    ) -> dict[str, int]:
        cur = conn.execute(
            f'SELECT '
            f'  COUNT(*) AS total, '
            f'  SUM(CASE WHEN "{source_column}" IS NOT NULL THEN 1 ELSE 0 END) AS non_null, '
            f'  SUM(CASE WHEN "{source_column}" IS NOT NULL '
            f'    AND "{source_column}" NOT IN (SELECT "{target_column}" FROM "{target_table}") '
            f'    THEN 1 ELSE 0 END) AS orphaned '
            f'FROM "{source_table}"'
        )
        row = cur.fetchone()
        if row:
            return {
                "total": row[0],
                "non_null": row[1] or 0,
                "orphaned": row[2] or 0,
            }
        return {"total": 0, "non_null": 0, "orphaned": 0}

    def sample_text_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int = 1000,
    ) -> list[str]:
        cur = conn.execute(
            f'SELECT CAST("{column}" AS TEXT) FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT ?',
            (limit,),
        )
        return [row[0] for row in cur.fetchall()]

    def is_numeric_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        return dt in {"integer", "real", "numeric", "float", "double", "int", "bigint", "smallint"}

    def is_text_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        return dt in {"text", "varchar", "character varying", "char", "clob", ""}

    def is_temporal_type(self, data_type: str) -> bool:
        dt = data_type.lower()
        return dt in {"date", "datetime", "timestamp"}


def _normalize_sqlite_type(raw_type: str) -> str:
    """Normalize SQLite type affinity to a standard name."""
    if not raw_type:
        return "text"  # SQLite default
    t = raw_type.upper()
    if "INT" in t:
        return "integer"
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return "text"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "real"
    if "BLOB" in t:
        return "blob"
    return "numeric"


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
