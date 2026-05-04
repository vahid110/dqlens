"""Core data models for DQLens."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Severity(enum.Enum):
    """Finding severity levels."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FindingCategory(enum.Enum):
    """Categories of findings."""

    FK_MISMATCH = "fk_mismatch"
    NULL_ANOMALY = "null_anomaly"
    UNIQUENESS_VIOLATION = "uniqueness_violation"
    ROW_COUNT_ANOMALY = "row_count_anomaly"
    DISTRIBUTION_SHIFT = "distribution_shift"
    PATTERN_VIOLATION = "pattern_violation"
    FRESHNESS = "freshness"
    TYPE_MISMATCH = "type_mismatch"
    SCHEMA_CHANGE = "schema_change"


@dataclass
class ColumnProfile:
    """Statistical profile of a single column."""

    name: str
    data_type: str
    nullable: bool
    row_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    is_unique: bool
    min_value: Any = None
    max_value: Any = None
    mean_value: float | None = None
    median_value: float | None = None
    stddev: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p95: float | None = None
    empty_string_count: int = 0
    empty_string_pct: float = 0.0
    most_common_values: list[tuple[Any, int]] = field(default_factory=list)
    detected_pattern: str | None = None  # email, uuid, url, phone, etc.
    pattern_match_pct: float | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    fk_target_table: str | None = None
    fk_target_column: str | None = None

    @property
    def non_null_count(self) -> int:
        return self.row_count - self.null_count


@dataclass
class ForeignKeyInfo:
    """Foreign key relationship metadata."""

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    constraint_name: str | None = None


@dataclass
class TableProfile:
    """Statistical profile of a single table."""

    schema_name: str
    table_name: str
    row_count: int
    columns: list[ColumnProfile] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    freshness_column: str | None = None
    latest_timestamp: datetime | None = None
    profiled_at: datetime = field(default_factory=_utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def get_column(self, name: str) -> ColumnProfile | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


@dataclass
class DatabaseProfile:
    """Profile of an entire database schema."""

    connection_url: str
    schema_name: str
    tables: list[TableProfile] = field(default_factory=list)
    profiled_at: datetime = field(default_factory=_utcnow)

    def get_table(self, name: str) -> TableProfile | None:
        for table in self.tables:
            if table.table_name == name or table.full_name == name:
                return table
        return None


@dataclass
class Finding:
    """A single data quality finding."""

    table: str
    column: str | None
    severity: Severity
    category: FindingCategory
    message: str
    detail: str
    current_value: Any = None
    baseline_value: Any = None
    dimension: str | None = None  # ISO/IEC 25012 dimension (for Pro compliance scoring)
    rule_name: str | None = None  # Which rule generated this finding

    def __str__(self) -> str:
        severity_str = self.severity.value.ljust(6)
        if self.column:
            return f"  {severity_str} {self.column}: {self.message}"
        return f"  {severity_str} {self.message}"


@dataclass
class CheckResult:
    """Result of a single test check."""

    table: str
    column: str | None
    test_name: str
    passed: bool
    message: str
    detail: str = ""
    dimension: str | None = None  # ISO/IEC 25012 dimension (for Pro compliance scoring)
    rule_name: str | None = None  # Which rule generated this result

    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        if self.column:
            return f"  {icon} {self.column}: {self.message}"
        return f"  {icon} {self.message}"


@dataclass
class TableResult:
    """All results for a single table."""

    table_name: str
    findings: list[Finding] = field(default_factory=list)
    passed_tests: list[CheckResult] = field(default_factory=list)

    @property
    def total_tests(self) -> int:
        return len(self.findings) + len(self.passed_tests)

    @property
    def problem_count(self) -> int:
        return len(self.findings)

    @property
    def passed_count(self) -> int:
        return len(self.passed_tests)


@dataclass
class RunResult:
    """Results of a full dqlens run."""

    tables: list[TableResult] = field(default_factory=list)
    ran_at: datetime = field(default_factory=_utcnow)

    @property
    def total_tests(self) -> int:
        return sum(t.total_tests for t in self.tables)

    @property
    def total_findings(self) -> int:
        return sum(t.problem_count for t in self.tables)

    @property
    def total_passed(self) -> int:
        return sum(t.passed_count for t in self.tables)

    @property
    def all_findings(self) -> list[Finding]:
        findings = []
        for table in self.tables:
            findings.extend(table.findings)
        return findings

    @property
    def has_problems(self) -> bool:
        return self.total_findings > 0
