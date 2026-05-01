"""Tests for the rule engine — scalable, pluggable checks."""

from datetime import datetime, timedelta, timezone

from dqlens.models import (ColumnProfile, DatabaseProfile, ForeignKeyInfo,
                           TableProfile)
from dqlens.rules.base import Dimension, RuleContext
from dqlens.rules.completeness import (EmptyTableRule, NotNullRule,
                                       NullRateDriftRule)
from dqlens.rules.registry import (get_all_rules, get_column_rules,
                                   get_table_rules)
from dqlens.rules.timeliness import FreshnessRule
from dqlens.rules.uniqueness import UniqueColumnRule, UniquenessLostRule
from dqlens.rules.validity import (AllowedValuesRule, PatternMatchRule,
                                   PositiveValuesRule, SemanticColumnRule)


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


class TestRegistry:
    def test_all_rules_have_names(self):
        for rule in get_all_rules():
            assert rule.name, f"Rule {rule.__class__.__name__} has no name"

    def test_all_rules_have_dimensions(self):
        for rule in get_all_rules():
            assert isinstance(rule.dimension, Dimension)

    def test_table_and_column_rules_separate(self):
        table_rules = get_table_rules()
        column_rules = get_column_rules()
        assert len(table_rules) > 0
        assert len(column_rules) > 0
        assert len(table_rules) + len(column_rules) == len(get_all_rules())

    def test_all_dimensions_covered(self):
        dimensions = {r.dimension for r in get_all_rules()}
        # At minimum we should cover these 4
        assert Dimension.COMPLETENESS in dimensions
        assert Dimension.UNIQUENESS in dimensions
        assert Dimension.VALIDITY in dimensions
        assert Dimension.TIMELINESS in dimensions


class TestEmptyTableRule:
    def test_applies_to_any_table(self):
        rule = EmptyTableRule()
        ctx = RuleContext(table=_table(row_count=0))
        assert rule.applies_to(ctx) is True

    def test_empty_table_finding(self):
        rule = EmptyTableRule()
        ctx = RuleContext(table=_table(row_count=0))
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")  # It's a Finding

    def test_non_empty_passes(self):
        rule = EmptyTableRule()
        ctx = RuleContext(table=_table(row_count=100))
        result = rule.evaluate(ctx)
        assert result is not None
        assert result.passed  # It's a CheckResult


class TestNotNullRule:
    def test_applies_when_no_nulls(self):
        rule = NotNullRule()
        col = _col(null_count=0)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is True

    def test_does_not_apply_when_has_nulls(self):
        rule = NotNullRule()
        col = _col(null_count=10, null_pct=1.0)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is False


class TestNullRateDriftRule:
    def test_detects_null_increase(self):
        rule = NullRateDriftRule()
        baseline_col = _col(name="email", null_count=1, null_pct=0.1, is_unique=False, is_primary_key=False)
        current_col = _col(name="email", null_count=32, null_pct=3.2, is_unique=False, is_primary_key=False)
        ctx = RuleContext(
            table=_table(columns=[current_col]),
            column=current_col,
            baseline_table=_table(columns=[baseline_col]),
            baseline_column=baseline_col,
        )
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")  # Finding
        assert "baseline" in result.message


class TestUniqueColumnRule:
    def test_unique_passes(self):
        rule = UniqueColumnRule()
        col = _col(is_unique=True, distinct_count=1000, null_count=0)
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert result.passed

    def test_duplicate_pk_fails(self):
        rule = UniqueColumnRule()
        col = _col(is_primary_key=True, is_unique=True, distinct_count=990, null_count=0)
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")


class TestUniquenessLostRule:
    def test_detects_uniqueness_loss(self):
        rule = UniquenessLostRule()
        baseline_col = _col(name="email", is_unique=True, distinct_count=1000, null_count=0, is_primary_key=False)
        current_col = _col(name="email", is_unique=False, distinct_count=950, null_count=0, is_primary_key=False)
        ctx = RuleContext(
            table=_table(), column=current_col,
            baseline_table=_table(), baseline_column=baseline_col,
        )
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")


class TestPatternMatchRule:
    def test_high_match_passes(self):
        rule = PatternMatchRule()
        col = _col(
            name="email", data_type="character varying",
            detected_pattern="email", pattern_match_pct=99.5,
            is_unique=False, is_primary_key=False,
        )
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert result.passed

    def test_low_match_flags(self):
        rule = PatternMatchRule()
        col = _col(
            name="email", data_type="character varying",
            detected_pattern="email", pattern_match_pct=75.0,
            is_unique=False, is_primary_key=False,
        )
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")


class TestPositiveValuesRule:
    def test_applies_to_price(self):
        rule = PositiveValuesRule()
        col = _col(name="price", min_value=0.01, max_value=100.0, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is True

    def test_does_not_apply_to_id(self):
        rule = PositiveValuesRule()
        col = _col(name="id", min_value=1, max_value=1000)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is False

    def test_negative_price_flags(self):
        rule = PositiveValuesRule()
        col = _col(name="price", min_value=-5.0, max_value=100.0, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")


class TestAllowedValuesRule:
    def test_applies_to_low_cardinality(self):
        rule = AllowedValuesRule()
        col = _col(
            name="status", distinct_count=5, distinct_pct=0.25,
            is_unique=False, is_primary_key=False,
        )
        ctx = RuleContext(table=_table(row_count=2000), column=col)
        assert rule.applies_to(ctx) is True

    def test_does_not_apply_to_high_cardinality(self):
        rule = AllowedValuesRule()
        col = _col(
            name="email", distinct_count=500, distinct_pct=50.0,
            is_unique=False, is_primary_key=False,
        )
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is False

    def test_detects_new_values(self):
        rule = AllowedValuesRule()
        baseline_col = _col(
            name="status", distinct_count=5, distinct_pct=0.25,
            is_unique=False, is_primary_key=False,
        )
        current_col = _col(
            name="status", distinct_count=7, distinct_pct=0.35,
            is_unique=False, is_primary_key=False,
        )
        ctx = RuleContext(
            table=_table(row_count=2000), column=current_col,
            baseline_table=_table(row_count=2000), baseline_column=baseline_col,
        )
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")
        assert "2 new" in result.message


class TestSemanticColumnRule:
    def test_applies_to_percentage(self):
        rule = SemanticColumnRule()
        col = _col(name="completion_percentage", min_value=0, max_value=100, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is True

    def test_applies_to_latitude(self):
        rule = SemanticColumnRule()
        col = _col(name="latitude", min_value=-33.8, max_value=51.5, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is True

    def test_flags_out_of_range_percentage(self):
        rule = SemanticColumnRule()
        col = _col(name="success_pct", min_value=-5, max_value=110, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")

    def test_valid_latitude_passes(self):
        rule = SemanticColumnRule()
        col = _col(name="latitude", min_value=-33.8, max_value=51.5, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        result = rule.evaluate(ctx)
        assert result is not None
        assert result.passed

    def test_does_not_apply_to_generic_column(self):
        rule = SemanticColumnRule()
        col = _col(name="description", min_value=None, is_primary_key=False)
        ctx = RuleContext(table=_table(), column=col)
        assert rule.applies_to(ctx) is False


class TestFreshnessRule:
    def test_fresh_data_passes(self):
        rule = FreshnessRule()
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        ctx = RuleContext(table=table)
        result = rule.evaluate(ctx)
        assert result is not None
        assert result.passed

    def test_stale_data_flags(self):
        rule = FreshnessRule()
        table = _table(
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc) - timedelta(days=10),
        )
        ctx = RuleContext(table=table)
        result = rule.evaluate(ctx)
        assert result is not None
        assert hasattr(result, "severity")


class TestGenerateHasReason:
    """Every rule's generate() must include a 'reason' field."""

    def test_all_generatable_rules_have_reason(self):
        table = _table(
            row_count=1000,
            columns=[
                _col(name="id"),
                _col(
                    name="email", data_type="character varying",
                    detected_pattern="email", pattern_match_pct=99.0,
                    is_unique=False, is_primary_key=False, distinct_count=900,
                ),
                _col(
                    name="status", distinct_count=5, distinct_pct=0.5,
                    is_unique=False, is_primary_key=False,
                ),
                _col(
                    name="price", min_value=0.01, max_value=100.0,
                    is_unique=False, is_primary_key=False, distinct_count=50,
                ),
                _col(
                    name="latitude", min_value=-33.8, max_value=51.5,
                    is_unique=False, is_primary_key=False, distinct_count=100,
                ),
                _col(
                    name="notes", null_count=100, null_pct=10.0,
                    is_unique=False, is_primary_key=False, distinct_count=800,
                ),
            ],
            freshness_column="created_at",
            latest_timestamp=datetime.now(timezone.utc),
        )

        for rule in get_all_rules():
            if rule.scope == "table":
                ctx = RuleContext(table=table)
                if rule.applies_to(ctx):
                    result = rule.generate(ctx)
                    if isinstance(result, dict) and result:
                        assert "reason" in result, f"{rule.name} missing reason"
            else:
                for col in table.columns:
                    ctx = RuleContext(table=table, column=col)
                    if rule.applies_to(ctx):
                        result = rule.generate(ctx)
                        if isinstance(result, dict) and result:
                            assert "reason" in result, f"{rule.name} on {col.name} missing reason"
