"""Database profiler v2 — uses the connector abstraction layer.

Replaces the original profiler.py which imported from connector.py directly.
This version accepts a BaseConnector instance, making it database-agnostic.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timezone
from typing import Any

from dqlens.connectors.base import BaseConnector
from dqlens.models import (ColumnProfile, DatabaseProfile, ForeignKeyInfo,
                           TableProfile)

# Pattern detection regexes
PATTERNS = {
    "email": re.compile(
        r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    ),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    "url": re.compile(
        r"^https?://[^\s/$.?#].[^\s]*$"
    ),
    "phone": re.compile(
        r"^[\+]?[(]?[0-9]{1,4}[)]?[-\s\./0-9]{7,15}$"
    ),
    "ipv4": re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    ),
}


def profile_database(
    db: BaseConnector,
    conn: Any,
    schema: str = "public",
    tables: list[str] | None = None,
    exclude_tables: list[str] | None = None,
) -> DatabaseProfile:
    """Profile all tables in a database schema.

    Args:
        db: The database connector instance.
        conn: An open database connection from db.connect().
        schema: Schema to profile.
        tables: If provided, only profile these tables.
        exclude_tables: If provided, exclude these tables (supports glob patterns).

    Returns:
        DatabaseProfile with all table profiles.
    """
    all_tables = db.list_tables(conn, schema)
    foreign_keys = db.get_foreign_keys(conn, schema)
    primary_keys = db.get_primary_keys(conn, schema)
    unique_indexes = db.get_unique_indexes(conn, schema)

    # Build FK lookup: source_table -> list of ForeignKeyInfo
    fk_lookup: dict[str, list[ForeignKeyInfo]] = {}
    for fk in foreign_keys:
        fk_info = ForeignKeyInfo(
            source_table=fk["source_table"],
            source_column=fk["source_column"],
            target_table=fk["target_table"],
            target_column=fk["target_column"],
            constraint_name=fk.get("constraint_name"),
        )
        fk_lookup.setdefault(fk["source_table"], []).append(fk_info)

    # Filter tables
    table_names = [t["table_name"] for t in all_tables]
    if tables:
        table_names = [t for t in table_names if t in tables]
    if exclude_tables:
        table_names = [
            t for t in table_names
            if not any(fnmatch.fnmatch(t, pat) for pat in exclude_tables)
        ]

    row_estimates = {t["table_name"]: t["estimated_rows"] for t in all_tables}

    table_profiles = []
    for table_name in table_names:
        profile = _profile_table(
            db=db,
            conn=conn,
            schema=schema,
            table_name=table_name,
            estimated_rows=row_estimates.get(table_name, 0),
            foreign_keys=fk_lookup.get(table_name, []),
            primary_key_columns=primary_keys.get(table_name, []),
            unique_columns=unique_indexes.get(table_name, set()),
        )
        table_profiles.append(profile)

    return DatabaseProfile(
        connection_url="",
        schema_name=schema,
        tables=table_profiles,
        profiled_at=datetime.now(timezone.utc),
    )


def _profile_table(
    db: BaseConnector,
    conn: Any,
    schema: str,
    table_name: str,
    estimated_rows: int,
    foreign_keys: list[ForeignKeyInfo],
    primary_key_columns: list[str],
    unique_columns: set[str],
) -> TableProfile:
    """Profile a single table."""
    if estimated_rows < 100_000:
        row_count = db.get_exact_row_count(conn, schema, table_name)
    else:
        row_count = max(estimated_rows, 0)

    columns_meta = db.get_columns(conn, schema, table_name)
    fk_by_column = {fk.source_column: fk for fk in foreign_keys}

    column_profiles = []
    for col_meta in columns_meta:
        col_name = col_meta["column_name"]
        data_type = col_meta["data_type"]
        nullable = col_meta["is_nullable"] == "YES"

        details = db.get_column_details(conn, schema, table_name, col_name, data_type)

        total = details.get("total", row_count)
        null_count = details.get("null_count", 0)
        distinct_count = details.get("distinct_count", 0)

        null_pct = (null_count / total * 100) if total > 0 else 0.0
        distinct_pct = (distinct_count / total * 100) if total > 0 else 0.0
        is_unique = distinct_count == (total - null_count) and total > 0

        is_pk = col_name in primary_key_columns
        has_unique_idx = col_name in unique_columns
        fk = fk_by_column.get(col_name)

        detected_pattern = None
        pattern_match_pct = None
        if db.is_text_type(data_type) and total > 0 and null_count < total:
            detected_pattern, pattern_match_pct = _detect_pattern(
                db, conn, schema, table_name, col_name
            )

        col_profile = ColumnProfile(
            name=col_name,
            data_type=data_type,
            nullable=nullable,
            row_count=total,
            null_count=null_count,
            null_pct=round(null_pct, 2),
            distinct_count=distinct_count,
            distinct_pct=round(distinct_pct, 2),
            is_unique=is_unique or is_pk or has_unique_idx,
            min_value=details.get("min_value"),
            max_value=details.get("max_value"),
            mean_value=details.get("mean_value"),
            stddev=details.get("stddev"),
            detected_pattern=detected_pattern,
            pattern_match_pct=round(pattern_match_pct, 1) if pattern_match_pct else None,
            is_primary_key=is_pk,
            is_foreign_key=fk is not None,
            fk_target_table=fk.target_table if fk else None,
            fk_target_column=fk.target_column if fk else None,
        )
        column_profiles.append(col_profile)

    # Freshness detection
    ts_columns = db.get_timestamp_columns(conn, schema, table_name)
    freshness_column = None
    latest_timestamp = None
    if ts_columns:
        preferred = ["updated_at", "modified_at", "created_at", "timestamp", "date"]
        freshness_column = ts_columns[0]
        for pref in preferred:
            for ts_col in ts_columns:
                if pref in ts_col.lower():
                    freshness_column = ts_col
                    break

        latest_str = db.get_latest_timestamp(conn, schema, table_name, freshness_column)
        if latest_str:
            try:
                latest_timestamp = datetime.fromisoformat(latest_str)
            except (ValueError, TypeError):
                pass

    return TableProfile(
        schema_name=schema,
        table_name=table_name,
        row_count=row_count,
        columns=column_profiles,
        foreign_keys=foreign_keys,
        freshness_column=freshness_column,
        latest_timestamp=latest_timestamp,
        profiled_at=datetime.now(timezone.utc),
    )


def _detect_pattern(
    db: BaseConnector,
    conn: Any,
    schema: str,
    table: str,
    column: str,
    sample_size: int = 1000,
) -> tuple[str | None, float | None]:
    """Detect common patterns in text column values."""
    values = db.sample_text_values(conn, schema, table, column, sample_size)
    if not values:
        return None, None

    best_pattern = None
    best_pct = 0.0

    for pattern_name, regex in PATTERNS.items():
        matches = sum(1 for v in values if regex.match(v.strip()))
        pct = matches / len(values) * 100
        if pct > 50 and pct > best_pct:
            best_pattern = pattern_name
            best_pct = pct

    if best_pattern:
        return best_pattern, best_pct
    return None, None
