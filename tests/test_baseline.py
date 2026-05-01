"""Tests for baseline storage — save and load profiles."""

from datetime import datetime, timezone

from dqlens.baseline import (
    get_baseline_count,
    load_latest_profile,
    load_previous_profile,
    save_profile,
)
from dqlens.models import ColumnProfile, DatabaseProfile, ForeignKeyInfo, TableProfile


def _make_profile(schema="public", tables=None):
    if tables is None:
        tables = [
            TableProfile(
                schema_name=schema,
                table_name="orders",
                row_count=1000,
                columns=[
                    ColumnProfile(
                        name="id",
                        data_type="integer",
                        nullable=False,
                        row_count=1000,
                        null_count=0,
                        null_pct=0.0,
                        distinct_count=1000,
                        distinct_pct=100.0,
                        is_unique=True,
                        is_primary_key=True,
                        min_value=1,
                        max_value=1000,
                    ),
                    ColumnProfile(
                        name="email",
                        data_type="character varying",
                        nullable=True,
                        row_count=1000,
                        null_count=10,
                        null_pct=1.0,
                        distinct_count=990,
                        distinct_pct=99.0,
                        is_unique=False,
                        detected_pattern="email",
                        pattern_match_pct=98.5,
                    ),
                ],
                foreign_keys=[
                    ForeignKeyInfo(
                        source_table="orders",
                        source_column="customer_id",
                        target_table="customers",
                        target_column="id",
                    ),
                ],
                freshness_column="created_at",
                latest_timestamp=datetime(2026, 5, 1, 12, 0, 0),
            ),
        ]
    return DatabaseProfile(
        connection_url="",
        schema_name=schema,
        tables=tables,
        profiled_at=datetime.now(timezone.utc),
    )


class TestSaveAndLoad:
    def test_save_creates_files(self, tmp_path):
        profile = _make_profile()
        filepath = save_profile(profile, base_path=tmp_path)

        assert filepath.exists()
        assert (tmp_path / ".dqlens" / "baselines" / "latest.yaml").exists()

    def test_load_latest(self, tmp_path):
        profile = _make_profile()
        save_profile(profile, base_path=tmp_path)

        loaded = load_latest_profile(base_path=tmp_path)
        assert loaded is not None
        assert loaded.schema_name == "public"
        assert len(loaded.tables) == 1
        assert loaded.tables[0].table_name == "orders"
        assert loaded.tables[0].row_count == 1000

    def test_load_latest_no_baseline(self, tmp_path):
        loaded = load_latest_profile(base_path=tmp_path)
        assert loaded is None

    def test_column_roundtrip(self, tmp_path):
        profile = _make_profile()
        save_profile(profile, base_path=tmp_path)

        loaded = load_latest_profile(base_path=tmp_path)
        assert loaded is not None

        orders = loaded.get_table("orders")
        assert orders is not None

        id_col = orders.get_column("id")
        assert id_col is not None
        assert id_col.is_primary_key is True
        assert id_col.is_unique is True
        assert id_col.null_count == 0
        assert id_col.min_value == 1
        assert id_col.max_value == 1000

        email_col = orders.get_column("email")
        assert email_col is not None
        assert email_col.detected_pattern == "email"
        assert email_col.pattern_match_pct == 98.5
        assert email_col.null_pct == 1.0

    def test_fk_roundtrip(self, tmp_path):
        profile = _make_profile()
        save_profile(profile, base_path=tmp_path)

        loaded = load_latest_profile(base_path=tmp_path)
        orders = loaded.get_table("orders")
        assert len(orders.foreign_keys) == 1
        assert orders.foreign_keys[0].target_table == "customers"

    def test_freshness_roundtrip(self, tmp_path):
        profile = _make_profile()
        save_profile(profile, base_path=tmp_path)

        loaded = load_latest_profile(base_path=tmp_path)
        orders = loaded.get_table("orders")
        assert orders.freshness_column == "created_at"
        assert orders.latest_timestamp is not None


class TestBaselineComparison:
    def test_previous_profile_with_two_baselines(self, tmp_path):
        import time

        # Save first profile
        p1 = _make_profile()
        save_profile(p1, base_path=tmp_path)

        # Small delay to ensure different timestamp
        time.sleep(0.1)

        # Save second profile with different data
        p2 = _make_profile()
        p2.tables[0].row_count = 2000
        save_profile(p2, base_path=tmp_path)

        # Latest should be p2
        latest = load_latest_profile(base_path=tmp_path)
        assert latest is not None

        # Previous should be p1
        previous = load_previous_profile(base_path=tmp_path)
        assert previous is not None
        assert previous.tables[0].row_count == 1000

    def test_previous_profile_with_one_baseline(self, tmp_path):
        save_profile(_make_profile(), base_path=tmp_path)
        previous = load_previous_profile(base_path=tmp_path)
        assert previous is None

    def test_baseline_count(self, tmp_path):
        import time

        assert get_baseline_count(base_path=tmp_path) == 0

        save_profile(_make_profile(), base_path=tmp_path)
        assert get_baseline_count(base_path=tmp_path) == 1

        time.sleep(0.1)
        save_profile(_make_profile(), base_path=tmp_path)
        assert get_baseline_count(base_path=tmp_path) == 2
