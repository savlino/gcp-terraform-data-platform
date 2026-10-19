import pytest
from test_build import stats

from ingest.quality import QualityError, evaluate


@pytest.mark.parametrize(
    "rule",
    [
        "invalid_tests",
        "invalid_devices",
        "invalid_quadkeys",
        "duplicate_quadkeys",
        "invalid_speeds",
    ],
)
def test_quality_errors(rule):
    with pytest.raises(QualityError, match=rule) as error:
        evaluate(stats(**{rule: 1}), 50, 20)
    report = next(item for item in error.value.result.rules if item.rule == rule)
    assert report.severity == "ERROR"
    assert report.violations == 1
    assert report.example_key == "0" * 16


def test_exact_row_threshold_passes_and_below_fails():
    assert evaluate(stats(row_count=10), 50, 20).rows == 10
    with pytest.raises(QualityError, match="row_count_drop"):
        evaluate(stats(row_count=9), 50, 20)
    assert evaluate(stats(row_count=1), 50, 0).rows == 1


def test_empty_region_fails():
    with pytest.raises(QualityError, match="empty_region"):
        evaluate(stats(row_count=0), 50, 0)


def test_warnings_do_not_discard_curated_rows(caplog):
    result = evaluate(stats(), 50, 20)
    assert result.rows == 10 and result.outlier_rows == 1
    assert "WARN" in caplog.text and "20.00%" in caplog.text
    warnings = {rule.rule: rule for rule in result.rules if rule.severity == "WARN"}
    assert warnings["outlier_rows"].violations == 1
    assert warnings["null_down_rows"].violations == 2
    assert all(rule.example_key == "0" * 16 for rule in warnings.values())


def test_aggregate_errors_return_counts_without_fabricated_keys():
    with pytest.raises(QualityError) as error:
        evaluate(stats(row_count=0), 50, 20)
    rules = {rule.rule: rule for rule in error.value.result.rules}
    assert rules["empty_region"].violations == 1
    assert rules["row_count_drop"].violations == 10
    assert rules["empty_region"].example_key is None
    assert rules["row_count_drop"].example_key is None


def test_missing_violation_example_is_rejected():
    values = stats(invalid_devices=1)
    values["invalid_devices_example"] = None
    with pytest.raises(ValueError, match="example missing"):
        evaluate(values, 50, 20)
