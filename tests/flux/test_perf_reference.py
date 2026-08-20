"""Unit tests for the benchmark drift guard's math (#264 follow-up).

Lives in the always-on suite for the same reason the percentile math does:
a comparison that is subtly wrong either hides a regression or cries wolf,
and neither should wait for someone to run the perf suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[1] / "perf"
if str(PERF_DIR) not in sys.path:
    sys.path.insert(0, str(PERF_DIR))

from fixtures.harness.reference import (  # noqa: E402
    Comparison,
    compare,
    tolerance_from_samples,
)


def _cmp(observed, reference=100.0, tolerance=0.1, better="lower"):
    return Comparison(
        metric="m",
        reference=reference,
        observed=observed,
        tolerance=tolerance,
        better=better,
    )


class TestRegressionVerdict:
    def test_inside_tolerance_is_not_a_regression(self):
        assert not _cmp(109).regressed

    def test_outside_tolerance_in_the_worse_direction_is(self):
        assert _cmp(111).regressed

    def test_faster_than_reference_is_never_a_regression(self):
        """A run beating the reference is a reason to re-bless, which is a
        person's decision -- not a failure."""
        assert not _cmp(50).regressed

    def test_higher_is_better_metrics_invert(self):
        # throughput: below the band is the regression
        assert _cmp(85, better="higher").regressed
        assert not _cmp(95, better="higher").regressed
        assert not _cmp(200, better="higher").regressed


class TestTolerance:
    def test_derived_from_the_observed_spread(self):
        # 20% spread around a median of 100 -> ±10%
        assert tolerance_from_samples([90, 100, 110]) == pytest.approx(0.1)

    def test_never_tighter_than_the_floor(self):
        """A lucky quiet batch must not set a band nothing can meet again."""
        assert tolerance_from_samples([100, 100, 100]) == 0.10

    def test_a_wide_spread_widens_the_band(self):
        assert tolerance_from_samples([50, 100, 150]) == pytest.approx(0.5)

    def test_a_single_sample_falls_back_to_the_floor(self):
        assert tolerance_from_samples([100]) == 0.10


class TestCompare:
    REFERENCE = {
        "entries": {
            "B1/sqlite/ci": {
                "metrics": {
                    "dispatch_p50": {"median": 850.0, "tolerance": 0.15, "better": "lower"},
                    "tasks_per_s": {"median": 180.0, "tolerance": 0.15, "better": "higher"},
                },
            },
        },
    }

    def test_compares_medians_not_single_samples(self):
        # One slow outlier must not flip the verdict on its own.
        results = compare(
            {"dispatch_p50": [840.0, 855.0, 1400.0]},
            self.REFERENCE,
            "B1/sqlite/ci",
        )

        assert len(results) == 1
        assert results[0].observed == 855.0
        assert not results[0].regressed

    def test_flags_a_median_outside_the_band(self):
        results = compare(
            {"dispatch_p50": [1100.0, 1120.0, 1130.0]},
            self.REFERENCE,
            "B1/sqlite/ci",
        )

        assert results[0].regressed

    def test_unknown_entry_is_an_error_not_a_pass(self):
        """A typo'd key must not silently compare against nothing."""
        with pytest.raises(KeyError):
            compare({"dispatch_p50": [1.0]}, self.REFERENCE, "B1/postgresql/full")

    def test_metrics_absent_from_the_reference_are_skipped(self):
        results = compare({"unknown_metric": [1.0]}, self.REFERENCE, "B1/sqlite/ci")

        assert results == []


class TestBlessedMedianMatchesTheCheck:
    """Blessing and checking have to agree on what a median is.

    They were computed two different ways: `sorted(v)[len(v)//2]` when
    blessing (the upper middle of an even sample) and `statistics.median`
    when comparing. A reference blessed by one definition and checked
    against the other drifts by half a sample before any code changes.
    """

    def test_even_sample_counts_use_the_statistical_median(self, tmp_path):
        import json
        import statistics

        from fixtures.harness.reference_cli import bless

        samples = {"B1/sqlite/ci": {"dispatch_p50": [100.0, 200.0, 300.0, 400.0]}}
        target = tmp_path / "reference.json"

        bless(samples, target)

        blessed = json.loads(target.read_text())
        median = blessed["entries"]["B1/sqlite/ci"]["metrics"]["dispatch_p50"]["median"]
        assert median == pytest.approx(statistics.median([100.0, 200.0, 300.0, 400.0]))
        assert median == pytest.approx(250.0)  # not 300.0, the upper middle

    def test_the_blessed_entry_records_how_many_runs_backed_it(self, tmp_path):
        import json

        from fixtures.harness.reference_cli import bless

        target = tmp_path / "reference.json"
        bless({"B2/sqlite/ci": {"tasks_per_s": [180.0, 190.0, 200.0]}}, target)

        entry = json.loads(target.read_text())["entries"]["B2/sqlite/ci"]
        assert entry["samples"] == 3
        assert entry["metrics"]["tasks_per_s"]["better"] == "higher"
