"""Base class and types for the rule engine."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import psycopg2.extensions

from dqlens.models import (
    ColumnProfile,
    DatabaseProfile,
    Finding,
    CheckResult,
    TableProfile,
)


class Dimension(enum.Enum):
    """ISO/IEC 25012 data quality dimensions."""

    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    TIMELINESS = "timeliness"
    ACCURACY = "accuracy"


@dataclass
class RuleContext:
    """Everything a rule needs to make decisions.

    Passed to applies_to(), generate(), and evaluate() so rules
    don't need to reach into global state.
    """

    table: TableProfile
    column: ColumnProfile | None = None
    baseline_table: TableProfile | None = None
    baseline_column: ColumnProfile | None = None
    conn: psycopg2.extensions.connection | None = None


class Rule(ABC):
    """Base class for all data quality rules.

    A rule is a self-contained check that knows:
    - When it applies (given a profile)
    - What test definition to generate (for tests.yaml)
    - How to evaluate (producing findings or passed results)
    """

    # Subclasses must set these
    name: str = ""
    dimension: Dimension = Dimension.COMPLETENESS
    scope: str = "column"  # "column" or "table"

    @abstractmethod
    def applies_to(self, ctx: RuleContext) -> bool:
        """Should this rule be generated for the given context?

        For column-scoped rules, ctx.column is set.
        For table-scoped rules, ctx.column is None.
        """
        ...

    @abstractmethod
    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        """Generate a test definition dict for tests.yaml.

        Must include a 'reason' field explaining why this check exists.
        """
        ...

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        """Evaluate the rule against current data.

        Returns:
            Finding — if a problem was detected
            CheckResult — if the check passed
            None — if the rule can't be evaluated (skip silently)
        """
        ...
