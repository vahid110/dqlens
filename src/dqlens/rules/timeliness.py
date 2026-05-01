"""Timeliness rules — freshness and staleness detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dqlens.models import CheckResult, Finding, FindingCategory, Severity
from dqlens.rules.base import Dimension, Rule, RuleContext


class FreshnessRule(Rule):
    """Check data freshness based on timestamp columns."""

    name = "freshness"
    dimension = Dimension.TIMELINESS
    scope = "table"

    def applies_to(self, ctx: RuleContext) -> bool:
        return (
            ctx.table.freshness_column is not None
            and ctx.table.latest_timestamp is not None
        )

    def generate(self, ctx: RuleContext) -> dict[str, Any]:
        return {
            "check": "freshness",
            "column": ctx.table.freshness_column,
            "expect": "within",
            "value": 24,
            "unit": "hours",
            "reason": (
                f"Most recent value in {ctx.table.freshness_column} was "
                f"{ctx.table.latest_timestamp.isoformat()}. "
                f"Flag if data becomes stale."
            ),
        }

    def evaluate(self, ctx: RuleContext) -> Finding | CheckResult | None:
        if not ctx.table.freshness_column or not ctx.table.latest_timestamp:
            return None

        now = datetime.now(timezone.utc)
        latest = ctx.table.latest_timestamp
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age = now - latest

        if age > timedelta(days=7):
            return Finding(
                table=ctx.table.full_name,
                column=ctx.table.freshness_column,
                severity=Severity.MEDIUM,
                category=FindingCategory.FRESHNESS,
                message=f"Last row is {_format_age(age)} old",
                detail=(
                    f"Flagged because: the most recent value in "
                    f"{ctx.table.freshness_column} is "
                    f"{ctx.table.latest_timestamp.isoformat()}, "
                    f"which is {_format_age(age)} ago."
                ),
                current_value=str(ctx.table.latest_timestamp),
            )
        elif age > timedelta(days=1):
            return Finding(
                table=ctx.table.full_name,
                column=ctx.table.freshness_column,
                severity=Severity.LOW,
                category=FindingCategory.FRESHNESS,
                message=f"Last row is {_format_age(age)} old",
                detail=(
                    f"Flagged because: the most recent value in "
                    f"{ctx.table.freshness_column} is "
                    f"{ctx.table.latest_timestamp.isoformat()}."
                ),
                current_value=str(ctx.table.latest_timestamp),
            )
        else:
            return CheckResult(
                table=ctx.table.full_name,
                column=ctx.table.freshness_column,
                test_name="freshness",
                passed=True,
                message=f"last row < {_format_age(age)} ago",
            )


def _format_age(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"
