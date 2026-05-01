"""Tests for the test generator — inspectable, editable test definitions."""

from datetime import datetime, timezone

from dqlens.models import (ColumnProfile, DatabaseProfile, ForeignKeyInfo,
                           TableProfile)
from dqlens.testgen import generate_tests, load_tests, save_tests


def _col(**kwargs):
    defaults = {
        "name": "id",
        "data_type": "integer",
        "nullable": False,
        "row_count": 1000,
        "null_count": 0,
        "null_pct": 0.0,
        "distinct_count": 1000,
        "distinct_pct": 100.0,
        "is_unique": True,
        "is_primary_key": True,
    }
    defaults.update(kwargs)
    return ColumnProfile(**defaults)


def _table(name="orders", row_count=1000, columns=None, **kwargs):
    return TableProfile(
        schema_name="public",
        table_name=name,
        row_count=row_count,
        columns=columns or [],
        **kwargs,
    )


def _db(tables=None):
    return DatabaseProfile(
        connection_url="",
        schema_name="public",
        tables=tables or [],
    )


class TestGenerateTests:
    def test_generates_row_count_check(self):
        profile = _db([_table(row_count=500, columns=[_col()])])
        tests = generate_tests(profile)

        table_tests = tests["tables"][0]
        check_types = [c["check"] for c in table_tests["checks"]]
        assert "row_count" in check_types

    def test_generates_row_count_drift_check(self):
        profile = _db([_table(row_count=500, columns=[_col()])])
        tests = generate_tests(profile)

        table_tests = tests["tables"][0]
        check_types = [c["check"] for c in table_tests["checks"]]
        assert "row_count_drift" in check_types

    def test_generates_not_null_check(self):
        col = _col(name="email", null_count=0, null_pct=0.0, is_primary_key=False)
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        not_null = [c for c in checks if c["check"] == "not_null" and c["column"] == "email"]
        assert len(not_null) == 1

    def test_generates_null_rate_check_for_partial_nulls(self):
        col = _col(
            name="phone", null_count=100, null_pct=10.0,
            is_unique=False, is_primary_key=False, distinct_count=900,
        )
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        null_rate = [c for c in checks if c["check"] == "null_rate" and c["column"] == "phone"]
        assert len(null_rate) == 1
        assert null_rate[0]["baseline"] == 10.0
        assert null_rate[0]["value"] == 20.0  # 2x baseline

    def test_generates_all_null_check(self):
        col = _col(
            name="notes", null_count=1000, null_pct=100.0,
            is_unique=False, is_primary_key=False, distinct_count=0,
        )
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        all_null = [c for c in checks if c["check"] == "all_null"]
        assert len(all_null) == 1

    def test_generates_unique_check(self):
        col = _col(name="id", is_unique=True, is_primary_key=True)
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        unique = [c for c in checks if c["check"] == "unique" and c["column"] == "id"]
        assert len(unique) == 1
        assert "primary key" in unique[0]["reason"]

    def test_generates_pattern_check(self):
        col = _col(
            name="email", data_type="character varying",
            is_unique=False, is_primary_key=False,
            detected_pattern="email", pattern_match_pct=98.5,
            distinct_count=900,
        )
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        pattern = [c for c in checks if c["check"] == "pattern"]
        assert len(pattern) == 1
        assert pattern[0]["pattern"] == "email"
        assert pattern[0]["current_match"] == 98.5
        assert pattern[0]["value"] == 93.5  # 5pp below current

    def test_generates_positive_values_check(self):
        col = _col(
            name="price", data_type="numeric",
            is_unique=False, is_primary_key=False,
            min_value=0.01, max_value=999.99,
            distinct_count=500,
        )
        profile = _db([_table(columns=[col])])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        positive = [c for c in checks if c["check"] == "positive_values"]
        assert len(positive) == 1
        assert positive[0]["column"] == "price"

    def test_generates_fk_integrity_check(self):
        fk = ForeignKeyInfo(
            source_table="orders",
            source_column="customer_id",
            target_table="customers",
            target_column="id",
        )
        table = _table(columns=[_col()], foreign_keys=[fk])
        profile = _db([table])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        fk_checks = [c for c in checks if c["check"] == "fk_integrity"]
        assert len(fk_checks) == 1
        assert fk_checks[0]["column"] == "customer_id"
        assert "customers.id" in fk_checks[0]["references"]

    def test_generates_freshness_check(self):
        table = _table(
            columns=[_col()],
            freshness_column="created_at",
            latest_timestamp=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        profile = _db([table])
        tests = generate_tests(profile)

        checks = tests["tables"][0]["checks"]
        freshness = [c for c in checks if c["check"] == "freshness"]
        assert len(freshness) == 1
        assert freshness[0]["column"] == "created_at"
        assert freshness[0]["value"] == 24  # hours

    def test_every_check_has_reason(self):
        """Every generated check must explain why it exists."""
        col = _col(
            name="email", data_type="character varying",
            detected_pattern="email", pattern_match_pct=99.0,
            is_primary_key=False, is_unique=False, distinct_count=900,
        )
        fk = ForeignKeyInfo(
            source_table="orders", source_column="customer_id",
            target_table="customers", target_column="id",
        )
        table = _table(
            columns=[_col(), col],
            foreign_keys=[fk],
            freshness_column="created_at",
            latest_timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        profile = _db([table])
        tests = generate_tests(profile)

        for table_def in tests["tables"]:
            for check in table_def["checks"]:
                assert "reason" in check, f"Check {check['check']} missing 'reason'"
                assert len(check["reason"]) > 10, f"Check {check['check']} has empty reason"

    def test_metadata_fields(self):
        profile = _db([_table(columns=[_col()])])
        tests = generate_tests(profile)

        assert tests["version"] == "1"
        assert "generated_from" in tests
        assert tests["schema"] == "public"
        assert "description" in tests
        assert "Edit freely" in tests["description"]


class TestSaveAndLoad:
    def test_roundtrip(self, tmp_path):
        profile = _db([_table(columns=[_col()])])
        tests = generate_tests(profile)

        save_tests(tests, base_path=tmp_path)
        loaded = load_tests(base_path=tmp_path)

        assert loaded is not None
        assert loaded["version"] == "1"
        assert len(loaded["tables"]) == 1

    def test_load_missing(self, tmp_path):
        loaded = load_tests(base_path=tmp_path)
        assert loaded is None
