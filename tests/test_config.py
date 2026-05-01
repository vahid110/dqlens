"""Tests for DQLens configuration management."""

import os
from pathlib import Path

import yaml

from dqlens.config import (
    DQLensConfig,
    add_ignore,
    init_dqlens_dir,
    load_config,
    load_ignores,
)


class TestDQLensConfig:
    def test_to_dict_minimal(self):
        config = DQLensConfig(connection_url="postgres://localhost/db", schema="public")
        d = config.to_dict()
        assert d["connection_url"] == "postgres://localhost/db"
        assert d["schema"] == "public"
        assert "tables" not in d
        assert "exclude_tables" not in d

    def test_to_dict_with_tables(self):
        config = DQLensConfig(
            connection_url="postgres://localhost/db",
            schema="myschema",
            tables=["orders", "customers"],
            exclude_tables=["tmp_*"],
        )
        d = config.to_dict()
        assert d["tables"] == ["orders", "customers"]
        assert d["exclude_tables"] == ["tmp_*"]

    def test_from_dict(self):
        data = {
            "connection_url": "postgres://localhost/db",
            "schema": "analytics",
            "tables": ["events"],
        }
        config = DQLensConfig.from_dict(data)
        assert config.connection_url == "postgres://localhost/db"
        assert config.schema == "analytics"
        assert config.tables == ["events"]
        assert config.exclude_tables == []

    def test_from_dict_defaults(self):
        data = {"connection_url": "postgres://localhost/db"}
        config = DQLensConfig.from_dict(data)
        assert config.schema == "public"
        assert config.tables == []


class TestInitAndLoad:
    def test_init_creates_directory(self, tmp_path):
        config = init_dqlens_dir(
            connection_url="postgres://localhost/testdb",
            schema="public",
            base_path=tmp_path,
        )
        dqlens_dir = tmp_path / ".dqlens"
        assert dqlens_dir.exists()
        assert (dqlens_dir / "config.yaml").exists()
        assert (dqlens_dir / "baselines").exists()
        assert (dqlens_dir / "ignores.yaml").exists()

    def test_load_config(self, tmp_path):
        init_dqlens_dir(
            connection_url="postgres://localhost/testdb",
            schema="myschema",
            tables=["orders"],
            base_path=tmp_path,
        )
        config = load_config(base_path=tmp_path)
        assert config.connection_url == "postgres://localhost/testdb"
        assert config.schema == "myschema"
        assert config.tables == ["orders"]

    def test_load_config_missing(self, tmp_path):
        import pytest
        with pytest.raises(FileNotFoundError, match="No .dqlens directory found"):
            load_config(base_path=tmp_path)


class TestIgnores:
    def test_load_ignores_empty(self, tmp_path):
        init_dqlens_dir("postgres://localhost/db", base_path=tmp_path)
        ignores = load_ignores(base_path=tmp_path)
        assert ignores == set()

    def test_add_and_load_ignore(self, tmp_path):
        init_dqlens_dir("postgres://localhost/db", base_path=tmp_path)
        add_ignore("orders.email.null_anomaly", base_path=tmp_path)
        add_ignore("customers.phone.pattern_violation", base_path=tmp_path)

        ignores = load_ignores(base_path=tmp_path)
        assert "orders.email.null_anomaly" in ignores
        assert "customers.phone.pattern_violation" in ignores

    def test_add_ignore_idempotent(self, tmp_path):
        init_dqlens_dir("postgres://localhost/db", base_path=tmp_path)
        add_ignore("orders.email.null_anomaly", base_path=tmp_path)
        add_ignore("orders.email.null_anomaly", base_path=tmp_path)

        ignores = load_ignores(base_path=tmp_path)
        assert len(ignores) == 1

    def test_load_ignores_no_file(self, tmp_path):
        # No .dqlens dir at all
        ignores = load_ignores(base_path=tmp_path)
        assert ignores == set()
