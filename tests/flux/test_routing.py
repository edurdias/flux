"""Tests for the declarative routing DSL and its server-side evaluator."""

from __future__ import annotations

import pytest

from flux.routing import (
    InputRef,
    input as input_ref,
    least,
    most,
    pick_worker,
    prefer,
    score,
    sticky,
    validate_worker_metrics,
)
from flux.worker_registry import WorkerInfo


def _worker(name: str, labels: dict | None = None, metrics: dict | None = None) -> WorkerInfo:
    return WorkerInfo(name=name, labels=labels, metrics=metrics)


class TestFactories:
    def test_score_compiles_to_spec(self):
        spec = score(
            prefer("label:region", "==", "eu", weight=10),
            least("metric:queue", weight=5),
            most("resource:memory_available"),
            sticky(weight=3),
            least("load"),
        )
        kinds = [t["kind"] for t in spec["terms"]]
        assert kinds == ["prefer", "least", "most", "sticky", "least"]
        assert spec["terms"][0]["weight"] == 10.0

    def test_input_ref_serializes_to_marker(self):
        term = prefer("label:tier", "==", input_ref("customer.tier"))
        assert term["value"] == {"$input": "customer.tier"}

    def test_score_requires_terms(self):
        with pytest.raises(ValueError, match="at least one term"):
            score()

    def test_score_rejects_foreign_terms(self):
        with pytest.raises(ValueError, match="prefer\\(\\)/least\\(\\)"):
            score({"kind": "custom"})

    def test_invalid_selector_rejected(self):
        with pytest.raises(ValueError, match="selector"):
            least("cpu")
        with pytest.raises(ValueError, match="missing its key"):
            least("metric:")
        with pytest.raises(ValueError, match="unknown resource field"):
            most("resource:gpu_flops")

    def test_invalid_op_rejected(self):
        with pytest.raises(ValueError, match="op must be one of"):
            prefer("label:x", "~=", "y")

    def test_invalid_weight_rejected(self):
        for bad in (0, -1, float("inf"), "heavy"):
            with pytest.raises(ValueError, match="weight"):
                least("load", weight=bad)

    def test_input_requires_path(self):
        with pytest.raises(ValueError):
            InputRef("")


class TestValidateWorkerMetrics:
    def test_valid_payload(self):
        assert validate_worker_metrics({"queue": 3, "latency": 1.5}) == {
            "queue": 3.0,
            "latency": 1.5,
        }

    def test_rejects_non_dict_and_bad_entries(self):
        assert validate_worker_metrics("nope") is None
        assert validate_worker_metrics({"": 1.0}) is None
        assert validate_worker_metrics({"x" * 65: 1.0}) is None
        assert validate_worker_metrics({"x": "high"}) is None
        assert validate_worker_metrics({"x": True}) is None
        assert validate_worker_metrics({"x": float("nan")}) is None
        assert validate_worker_metrics({f"k{i}": 1.0 for i in range(33)}) is None


class TestPickWorker:
    def test_prefer_label_wins_over_load(self):
        eu = _worker("eu-1", labels={"region": "eu"})
        us = _worker("us-1", labels={"region": "us"})
        policy = score(prefer("label:region", "==", "eu", weight=10), least("load"))

        # eu-1 is far busier, but the region preference dominates.
        winner = pick_worker([eu, us], policy, loads={"eu-1": 9, "us-1": 0})

        assert winner.name == "eu-1"

    def test_input_resolved_against_execution_input(self):
        gold = _worker("gold-w", labels={"tier": "gold"})
        silver = _worker("silver-w", labels={"tier": "silver"})
        policy = score(prefer("label:tier", "==", input_ref("tier"), weight=10), least("load"))

        assert (
            pick_worker([gold, silver], policy, loads={}, input_value={"tier": "gold"}).name
            == "gold-w"
        )
        assert (
            pick_worker([gold, silver], policy, loads={}, input_value={"tier": "silver"}).name
            == "silver-w"
        )

    def test_input_dotted_path_and_missing_path(self):
        gold = _worker("gold-w", labels={"tier": "gold"})
        silver = _worker("silver-w", labels={"tier": "silver"})
        policy = score(
            prefer("label:tier", "==", input_ref("customer.tier"), weight=10),
            least("load"),
        )

        nested = {"customer": {"tier": "silver"}}
        assert pick_worker([gold, silver], policy, loads={}, input_value=nested).name == "silver-w"
        # Missing path: the prefer term matches nobody; load breaks the tie.
        winner = pick_worker(
            [gold, silver],
            policy,
            loads={"gold-w": 2, "silver-w": 0},
            input_value={"other": 1},
        )
        assert winner.name == "silver-w"

    def test_least_metric_normalized_against_candidates(self):
        low = _worker("low", metrics={"queue": 1})
        high = _worker("high", metrics={"queue": 50})
        policy = score(least("metric:queue"))

        assert pick_worker([low, high], policy, loads={}).name == "low"

    def test_most_metric(self):
        weak = _worker("weak", metrics={"fitness": 0.2})
        strong = _worker("strong", metrics={"fitness": 0.9})
        policy = score(most("metric:fitness"))

        assert pick_worker([weak, strong], policy, loads={}).name == "strong"

    def test_missing_metric_scores_worst(self):
        reporting = _worker("reporting", metrics={"fitness": 0.1})
        silent = _worker("silent")
        policy = score(most("metric:fitness"))

        assert pick_worker([reporting, silent], policy, loads={}).name == "reporting"

    def test_metric_absent_everywhere_cannot_discriminate(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(most("metric:fitness"), least("load"))

        winner = pick_worker([a, b], policy, loads={"a": 3, "b": 1})

        assert winner.name == "b"  # only the load term discriminates

    def test_sticky_term_prefers_hinted_worker(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(sticky(weight=5), least("load"))

        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a")

        assert winner.name == "a"

    def test_policy_without_sticky_term_ignores_hint(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(least("load"))

        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a")

        assert winner.name == "b"

    def test_ordering_ops_on_metrics(self):
        cold = _worker("cold", metrics={"temp": 40})
        hot = _worker("hot", metrics={"temp": 90})
        policy = score(prefer("metric:temp", "<", 60, weight=10))

        assert pick_worker([cold, hot], policy, loads={}).name == "cold"

    def test_ordering_op_on_non_numeric_is_false(self):
        a = _worker("a", labels={"zone": "z1"})
        b = _worker("b")
        policy = score(prefer("label:zone", "<", "z2", weight=10), least("load"))

        # Strings never satisfy ordering ops: the term matches nobody.
        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0})

        assert winner.name == "b"

    def test_tie_breaks_deterministically(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(least("load"))

        # Equal scores and loads: name ascends.
        assert pick_worker([b, a], policy, loads={}).name == "a"

    def test_malformed_policy_returns_none(self):
        a = _worker("a")
        for bad in (None, {}, {"terms": "x"}, {"terms": [{"kind": "warp"}]}, {"terms": [42]}):
            assert pick_worker([a], bad, loads={}) is None

    def test_empty_eligible_returns_none(self):
        assert pick_worker([], score(least("load")), loads={}) is None
