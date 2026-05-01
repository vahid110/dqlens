"""DQLens rule engine — scalable, pluggable data quality checks.

Each rule is a self-contained class that knows:
1. When to activate (given a column/table profile)
2. What test definition to generate (for tests.yaml)
3. How to evaluate (given current data + baseline)

Rules are organized by ISO/IEC 25012 data quality dimensions:
- Completeness: missing values, empty tables, row gaps
- Uniqueness: duplicate detection
- Validity: format/pattern/range conformance
- Consistency: cross-column and cross-table coherence
- Timeliness: freshness and staleness
- Accuracy: (future) reference data comparison

To add a new check: create a class inheriting from Rule, implement
applies_to(), generate(), and evaluate(). Register it in RULES.
"""

from dqlens.rules.base import Rule, RuleContext, Dimension
from dqlens.rules.registry import get_all_rules, get_rules_for_dimension

__all__ = [
    "Rule",
    "RuleContext",
    "Dimension",
    "get_all_rules",
    "get_rules_for_dimension",
]
