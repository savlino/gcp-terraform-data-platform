import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleResult:
    rule: str
    severity: str
    violations: int
    example_key: str | None


class QualityError(ValueError):
    def __init__(self, result: "QualityResult") -> None:
        self.result = result
        errors = [
            rule.rule for rule in result.rules if rule.severity == "ERROR" and rule.violations
        ]
        super().__init__("DQ failed: " + ", ".join(errors))


@dataclass(frozen=True)
class QualityResult:
    rows: int
    source_rows: int
    outlier_rows: int
    null_down_rows: int
    rules: tuple[RuleResult, ...]


def evaluate(
    stats: Mapping[str, int | str | None], minimum_row_percent: float, previous_rows: int
) -> QualityResult:
    def count(name: str) -> int:
        value = stats[name]
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"DQ aggregate {name} must be a non-negative integer.")
        return value

    rules: list[RuleResult] = []
    for name in (
        "invalid_tests",
        "invalid_devices",
        "invalid_quadkeys",
        "duplicate_quadkeys",
        "invalid_speeds",
        "outlier_rows",
        "null_down_rows",
    ):
        violations = count(name)
        example = stats[name + "_example"]
        if example is not None and not isinstance(example, str):
            raise ValueError(f"DQ example for {name} must be a string or null.")
        if violations > 0 and example is None:
            raise ValueError(f"DQ example missing for violated rule {name}.")
        rules.append(
            RuleResult(
                name,
                "WARN" if name in ("outlier_rows", "null_down_rows") else "ERROR",
                violations,
                example,
            )
        )
    rows = count("row_count")
    required_rows = math.ceil(previous_rows * minimum_row_percent / 100)
    rules.extend(
        (
            RuleResult("empty_region", "ERROR", int(rows == 0), None),
            RuleResult("row_count_drop", "ERROR", max(0, required_rows - rows), None),
        )
    )
    result = QualityResult(
        rows, count("source_rows"), count("outlier_rows"), count("null_down_rows"), tuple(rules)
    )
    for rule in result.rules:
        level = (
            (logging.ERROR if rule.severity == "ERROR" else logging.WARNING)
            if rule.violations
            else logging.INFO
        )
        LOGGER.log(
            level,
            "DQ %s: rule=%s violations=%d example_key=%r",
            rule.severity,
            rule.rule,
            rule.violations,
            rule.example_key,
        )
    LOGGER.warning(
        "DQ WARN: avg_lat_down_ms null share=%.2f%% (%d/%d)",
        100 * result.null_down_rows / rows if rows else 0,
        result.null_down_rows,
        rows,
    )
    if any(rule.severity == "ERROR" and rule.violations for rule in result.rules):
        raise QualityError(result)
    return result
