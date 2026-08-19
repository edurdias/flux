"""Unit tests for the benchmark metric math (#259).

Lives here rather than under tests/perf so it runs in the ordinary unit
job: the perf suite is opt-in, but a percentile that is subtly wrong makes
every number the benchmark publishes wrong, and that should not wait for
someone to run the suite deliberately. Same split T8 uses -- the
structural halves are always-on (tests/flux/test_startup_import_budget.py),
the wall-clock halves are not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parents[1] / "perf"
if str(PERF_DIR) not in sys.path:
    sys.path.insert(0, str(PERF_DIR))

from fixtures.harness.metrics import latency_summary, percentile, throughput  # noqa: E402


class TestPercentile:
    def test_p50_of_a_known_series(self):
        assert percentile([1, 2, 3, 4, 5], 50) == 3

    def test_p99_picks_the_worst_of_a_hundred(self):
        # Nearest-rank: p99 of 1..100 is the 99th value, not an average of
        # neighbours -- a tail metric must name a sample that happened.
        assert percentile(list(range(1, 101)), 99) == 99

    def test_p100_is_the_maximum(self):
        assert percentile([4, 1, 9], 100) == 9

    def test_a_single_sample_is_every_percentile(self):
        for q in (50, 95, 99):
            assert percentile([7.5], q) == 7.5

    def test_order_does_not_matter(self):
        assert percentile([5, 1, 4, 2, 3], 95) == percentile([1, 2, 3, 4, 5], 95)

    def test_an_empty_series_has_no_percentile(self):
        with pytest.raises(ValueError):
            percentile([], 50)


class TestLatencySummary:
    def test_reports_the_three_headline_quantiles_and_the_shape(self):
        summary = latency_summary(list(range(1, 101)))

        assert summary["count"] == 100
        assert summary["p50"] == 50
        assert summary["p95"] == 95
        assert summary["p99"] == 99
        assert summary["min"] == 1
        assert summary["max"] == 100
        assert summary["mean"] == pytest.approx(50.5)

    def test_an_empty_series_summarizes_to_a_zero_count_not_a_crash(self):
        """A scenario that produced no samples must still record a run --
        'nothing measured' is itself the finding."""
        summary = latency_summary([])

        assert summary["count"] == 0
        assert summary["p50"] is None
        assert summary["p99"] is None


class TestThroughput:
    def test_units_are_per_second(self):
        assert throughput(300, 10.0) == 30.0

    def test_a_zero_length_window_has_no_rate(self):
        assert throughput(5, 0.0) is None

    def test_no_work_in_a_real_window_is_zero_not_none(self):
        assert throughput(0, 5.0) == 0.0
