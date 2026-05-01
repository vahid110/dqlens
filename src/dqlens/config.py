"""Configuration management for DQLens."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DQLENS_DIR = ".dqlens"
CONFIG_FILE = "config.yaml"
BASELINES_DIR = "baselines"
IGNORES_FILE = "ignores.yaml"


@dataclass
class DQLensConfig:
    """DQLens project configuration."""

    connection_url: str
    schema: str = "public"
    tables: list[str] = field(default_factory=list)  # empty = all tables
    exclude_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "connection_url": self.connection_url,
            "schema": self.schema,
        }
        if self.tables:
            d["tables"] = self.tables
        if self.exclude_tables:
            d["exclude_tables"] = self.exclude_tables
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DQLensConfig:
        return cls(
            connection_url=data["connection_url"],
            schema=data.get("schema", "public"),
            tables=data.get("tables", []),
            exclude_tables=data.get("exclude_tables", []),
        )


def get_dqlens_dir(base_path: str | Path | None = None) -> Path:
    """Get the .dqlens directory path."""
    if base_path is None:
        base_path = Path.cwd()
    return Path(base_path) / DQLENS_DIR


def get_baselines_dir(base_path: str | Path | None = None) -> Path:
    """Get the baselines directory path."""
    return get_dqlens_dir(base_path) / BASELINES_DIR


def init_dqlens_dir(
    connection_url: str,
    schema: str = "public",
    tables: list[str] | None = None,
    exclude_tables: list[str] | None = None,
    base_path: str | Path | None = None,
) -> DQLensConfig:
    """Initialize the .dqlens directory and config file."""
    dqlens_dir = get_dqlens_dir(base_path)
    baselines_dir = get_baselines_dir(base_path)

    dqlens_dir.mkdir(exist_ok=True)
    baselines_dir.mkdir(exist_ok=True)

    config = DQLensConfig(
        connection_url=connection_url,
        schema=schema,
        tables=tables or [],
        exclude_tables=exclude_tables or [],
    )

    config_path = dqlens_dir / CONFIG_FILE
    with open(config_path, "w") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)

    # Create empty ignores file
    ignores_path = dqlens_dir / IGNORES_FILE
    if not ignores_path.exists():
        with open(ignores_path, "w") as f:
            yaml.dump({"ignored": []}, f, default_flow_style=False)

    return config


def load_config(base_path: str | Path | None = None) -> DQLensConfig:
    """Load config from .dqlens/config.yaml."""
    config_path = get_dqlens_dir(base_path) / CONFIG_FILE
    if not config_path.exists():
        raise FileNotFoundError(
            f"No .dqlens directory found. Run 'dqlens init' first.\n"
            f"Expected config at: {config_path}"
        )
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return DQLensConfig.from_dict(data)


def load_ignores(base_path: str | Path | None = None) -> set[str]:
    """Load ignored findings from .dqlens/ignores.yaml.

    Returns a set of ignore keys like 'orders.email.null_anomaly'.
    """
    ignores_path = get_dqlens_dir(base_path) / IGNORES_FILE
    if not ignores_path.exists():
        return set()
    with open(ignores_path) as f:
        data = yaml.safe_load(f) or {}
    return set(data.get("ignored", []))


def add_ignore(key: str, base_path: str | Path | None = None) -> None:
    """Add an ignore key to .dqlens/ignores.yaml."""
    ignores_path = get_dqlens_dir(base_path) / IGNORES_FILE
    ignores = load_ignores(base_path)
    ignores.add(key)
    with open(ignores_path, "w") as f:
        yaml.dump(
            {"ignored": sorted(ignores)}, f, default_flow_style=False
        )
