"""Tests for output formatting."""

from datetime import datetime

from dqlens.models import (
    Finding,
    FindingCategory,
    RunResult,
    Severity,
    TableResult,
    CheckResult,
)
from dqlens.output import format_json_result


class TestJsonOutput:
    def test_format_empty_result(self):
        result = RunResult(tables=[])
        data = format_json_result(result)
        assert data["summary"]["tables"] == 0
        assert data["summary"]["total_tests"] == 0

    def test_format_with_findings(self):
        result = RunResult(tables=[
            TableResult(
                table_name="public.orders",
                findings=[
                    Finding(
                        table="public.orders",
                        column="email",
                        severity=Severity.HIGH,
                        category=FindingCategory.NULL_ANOMALY,
                        message="3.2% null",
                        detail="Was 0.1%",
                        current_value=3.2,
                        baseline_value=0.1,
                    ),
                ],
                passed_tests=[
                    CheckResult(
                        table="public.orders",
                        column="id",
                        test_name="unique",
                        passed=True,
                        message="unique, not null",
                    ),
                ],
            ),
        ])
        data = format_json_result(result)

        assert data["summary"]["tables"] == 1
        assert data["summary"]["total_tests"] == 2
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1

        table = data["tables"][0]
        assert table["name"] == "public.orders"
        assert len(table["findings"]) == 1
        assert table["findings"][0]["severity"] == "HIGH"
        assert table["findings"][0]["current_value"] == 3.2
        assert table["findings"][0]["baseline_value"] == 0.1

        assert len(table["passed_tests"]) == 1
        assert table["passed_tests"][0]["test"] == "unique"
