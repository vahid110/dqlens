"""Rule registry — central catalog of all available rules.

To add a new rule:
1. Create a class inheriting from Rule in the appropriate dimension module
2. Add it to the RULES list below

That's it. The engine will automatically:
- Check if it applies to each column/table
- Generate test definitions for tests.yaml
- Evaluate it during `dqlens run`
"""

from __future__ import annotations

from dqlens.rules.base import Dimension, Rule
from dqlens.rules.completeness import (AllNullRule, EmptyTableRule,
                                       NotNullRule, NullRateDriftRule,
                                       PrimaryKeyNullRule, RowCountDriftRule)
from dqlens.rules.consistency import ForeignKeyIntegrityRule
from dqlens.rules.timeliness import FreshnessRule
from dqlens.rules.uniqueness import UniqueColumnRule, UniquenessLostRule
from dqlens.rules.validity import (AllowedValuesRule, PatternDriftRule,
                                   PatternMatchRule, PositiveValuesRule,
                                   SemanticColumnRule, ValueRangeDriftRule)

# All registered rules. Order matters — rules are evaluated in this order.
# Table-scoped rules first, then column-scoped.
RULES: list[Rule] = [
    # Table-scoped
    EmptyTableRule(),
    RowCountDriftRule(),
    ForeignKeyIntegrityRule(),
    FreshnessRule(),
    # Column-scoped: completeness
    NotNullRule(),
    NullRateDriftRule(),
    AllNullRule(),
    PrimaryKeyNullRule(),
    # Column-scoped: uniqueness
    UniqueColumnRule(),
    UniquenessLostRule(),
    # Column-scoped: validity
    PatternMatchRule(),
    PatternDriftRule(),
    PositiveValuesRule(),
    AllowedValuesRule(),
    ValueRangeDriftRule(),
    SemanticColumnRule(),
]


def get_all_rules() -> list[Rule]:
    """Get all registered rules."""
    return RULES


def get_rules_for_dimension(dimension: Dimension) -> list[Rule]:
    """Get rules for a specific data quality dimension."""
    return [r for r in RULES if r.dimension == dimension]


def get_table_rules() -> list[Rule]:
    """Get rules that operate at the table level."""
    return [r for r in RULES if r.scope == "table"]


def get_column_rules() -> list[Rule]:
    """Get rules that operate at the column level."""
    return [r for r in RULES if r.scope == "column"]
