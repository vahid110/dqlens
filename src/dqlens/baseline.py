"""Baseline storage — save and load database profiles for drift comparison.

Profiles are stored as YAML files in .dqlens/baselines/ with timestamps.
Single-baseline comparison (current vs. last) is a core free-tier feature.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dqlens.config import get_baselines_dir
from dqlens.models import (
    ColumnProfile,
    DatabaseProfile,
    ForeignKeyInfo,
    TableProfile,
)


def save_profile(
    profile: DatabaseProfile,
    base_path: str | Path | None = None,
) -> Path:
    """Save a database profile to .dqlens/baselines/.

    Returns the path to the saved file.
    """
    baselines_dir = get_baselines_dir(base_path)
    baselines_dir.mkdir(parents=True, exist_ok=True)

    # Use current time for the filename to guarantee uniqueness even if
    # profile.profiled_at is the same across rapid successive saves.
    now = datetime.now(tz=None)
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    filename = f"profile_{timestamp}.yaml"
    filepath = baselines_dir / filename

    data = _profile_to_dict(profile)
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Also save as "latest" for easy access
    latest_path = baselines_dir / "latest.yaml"
    with open(latest_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return filepath


def load_latest_profile(
    base_path: str | Path | None = None,
) -> DatabaseProfile | None:
    """Load the most recent baseline profile.

    Returns None if no baseline exists.
    """
    baselines_dir = get_baselines_dir(base_path)
    latest_path = baselines_dir / "latest.yaml"

    if not latest_path.exists():
        return None

    with open(latest_path) as f:
        data = yaml.safe_load(f)

    if not data:
        return None

    return _dict_to_profile(data)


def load_previous_profile(
    base_path: str | Path | None = None,
) -> DatabaseProfile | None:
    """Load the second-most-recent baseline profile (for drift comparison).

    When we save a new profile, the previous 'latest' becomes the baseline
    to compare against. This function finds that previous profile.
    """
    baselines_dir = get_baselines_dir(base_path)
    if not baselines_dir.exists():
        return None

    # List all profile files (excluding latest.yaml symlink/copy)
    profile_files = sorted(
        [f for f in baselines_dir.glob("profile_*.yaml")],
        reverse=True,
    )

    # Need at least 2 profiles for comparison
    if len(profile_files) < 2:
        return None

    # The second file is the previous baseline
    with open(profile_files[1]) as f:
        data = yaml.safe_load(f)

    if not data:
        return None

    return _dict_to_profile(data)


def get_baseline_count(base_path: str | Path | None = None) -> int:
    """Count the number of saved baselines."""
    baselines_dir = get_baselines_dir(base_path)
    if not baselines_dir.exists():
        return 0
    return len(list(baselines_dir.glob("profile_*.yaml")))


def _profile_to_dict(profile: DatabaseProfile) -> dict[str, Any]:
    """Serialize a DatabaseProfile to a dict for YAML storage."""
    return {
        "schema": profile.schema_name,
        "profiled_at": profile.profiled_at.isoformat(),
        "tables": [_table_to_dict(t) for t in profile.tables],
    }


def _table_to_dict(table: TableProfile) -> dict[str, Any]:
    """Serialize a TableProfile to a dict."""
    d: dict[str, Any] = {
        "schema": table.schema_name,
        "table": table.table_name,
        "row_count": table.row_count,
        "profiled_at": table.profiled_at.isoformat(),
        "columns": [_column_to_dict(c) for c in table.columns],
    }
    if table.foreign_keys:
        d["foreign_keys"] = [
            {
                "source_table": fk.source_table,
                "source_column": fk.source_column,
                "target_table": fk.target_table,
                "target_column": fk.target_column,
            }
            for fk in table.foreign_keys
        ]
    if table.freshness_column:
        d["freshness_column"] = table.freshness_column
    if table.latest_timestamp:
        d["latest_timestamp"] = table.latest_timestamp.isoformat()
    return d


def _column_to_dict(col: ColumnProfile) -> dict[str, Any]:
    """Serialize a ColumnProfile to a dict."""
    d: dict[str, Any] = {
        "name": col.name,
        "data_type": col.data_type,
        "nullable": col.nullable,
        "row_count": col.row_count,
        "null_count": col.null_count,
        "null_pct": col.null_pct,
        "distinct_count": col.distinct_count,
        "distinct_pct": col.distinct_pct,
        "is_unique": col.is_unique,
    }
    if col.min_value is not None:
        d["min_value"] = col.min_value
    if col.max_value is not None:
        d["max_value"] = col.max_value
    if col.mean_value is not None:
        d["mean_value"] = col.mean_value
    if col.stddev is not None:
        d["stddev"] = col.stddev
    if col.detected_pattern:
        d["detected_pattern"] = col.detected_pattern
        d["pattern_match_pct"] = col.pattern_match_pct
    if col.is_primary_key:
        d["is_primary_key"] = True
    if col.is_foreign_key:
        d["is_foreign_key"] = True
        d["fk_target_table"] = col.fk_target_table
        d["fk_target_column"] = col.fk_target_column
    return d


def _dict_to_profile(data: dict[str, Any]) -> DatabaseProfile:
    """Deserialize a dict to a DatabaseProfile."""
    tables = []
    for t in data.get("tables", []):
        tables.append(_dict_to_table(t))

    profiled_at = datetime.fromisoformat(data["profiled_at"])

    return DatabaseProfile(
        connection_url="",
        schema_name=data["schema"],
        tables=tables,
        profiled_at=profiled_at,
    )


def _dict_to_table(data: dict[str, Any]) -> TableProfile:
    """Deserialize a dict to a TableProfile."""
    columns = [_dict_to_column(c) for c in data.get("columns", [])]
    foreign_keys = [
        ForeignKeyInfo(
            source_table=fk["source_table"],
            source_column=fk["source_column"],
            target_table=fk["target_table"],
            target_column=fk["target_column"],
        )
        for fk in data.get("foreign_keys", [])
    ]

    latest_timestamp = None
    if data.get("latest_timestamp"):
        try:
            latest_timestamp = datetime.fromisoformat(data["latest_timestamp"])
        except (ValueError, TypeError):
            pass

    profiled_at = datetime.fromisoformat(data.get("profiled_at", datetime.now(timezone.utc).isoformat()))

    return TableProfile(
        schema_name=data["schema"],
        table_name=data["table"],
        row_count=data["row_count"],
        columns=columns,
        foreign_keys=foreign_keys,
        freshness_column=data.get("freshness_column"),
        latest_timestamp=latest_timestamp,
        profiled_at=profiled_at,
    )


def _dict_to_column(data: dict[str, Any]) -> ColumnProfile:
    """Deserialize a dict to a ColumnProfile."""
    return ColumnProfile(
        name=data["name"],
        data_type=data["data_type"],
        nullable=data["nullable"],
        row_count=data["row_count"],
        null_count=data["null_count"],
        null_pct=data["null_pct"],
        distinct_count=data["distinct_count"],
        distinct_pct=data["distinct_pct"],
        is_unique=data["is_unique"],
        min_value=data.get("min_value"),
        max_value=data.get("max_value"),
        mean_value=data.get("mean_value"),
        stddev=data.get("stddev"),
        detected_pattern=data.get("detected_pattern"),
        pattern_match_pct=data.get("pattern_match_pct"),
        is_primary_key=data.get("is_primary_key", False),
        is_foreign_key=data.get("is_foreign_key", False),
        fk_target_table=data.get("fk_target_table"),
        fk_target_column=data.get("fk_target_column"),
    )
