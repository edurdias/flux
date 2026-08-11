"""Tests for the declarative routing DSL and its server-side evaluator."""

from __future__ import annotations

import pytest

from flux.routing import (
    Condition,
    InputRef,
    input as input_ref,
    label,
    least,
    load,
    metric,
    most,
    pick_worker,
    prefer,
    resource,
    score,
    sticky,
    validate_worker_metrics,
)
from flux.worker_registry import WorkerInfo


def _worker(
    name: str,
    labels: dict | None = None,
    metrics: dict | None = None,
    metadata: dict | None = None,
) -> WorkerInfo:
    return WorkerInfo(name=name, labels=labels, metrics=metrics, metadata=metadata)


class TestFactories:
    def test_score_compiles_to_spec(self):
        spec = score(
            prefer(label("region") == "eu", weight=10),
            least(metric("queue"), weight=5),
            most(resource("memory_available")),
            sticky(weight=3),
            least(load()),
        )
        kinds = [t["kind"] for t in spec["terms"]]
        assert kinds == ["prefer", "least", "most", "sticky", "least"]
        assert spec["terms"][0] == {
            "kind": "prefer",
            "selector": "label:region",
            "op": "==",
            "value": "eu",
            "weight": 10.0,
        }
        assert spec["terms"][1]["selector"] == "metric:queue"
        assert spec["terms"][2]["selector"] == "resource:memory_available"
        assert spec["terms"][4]["selector"] == "load"

    def test_comparison_operators_map_to_ops(self):
        cases = {
            "==": label("x") == "v",
            "!=": label("x") != "v",
            "<": metric("m") < 5,
            "<=": metric("m") <= 5,
            ">": metric("m") > 5,
            ">=": metric("m") >= 5,
        }
        for op, condition in cases.items():
            assert isinstance(condition, Condition)
            assert condition.op == op

    def test_reversed_comparison_uses_reflected_operator(self):
        condition = 60 > metric("temp")  # int.__gt__ -> NotImplemented -> reflected __lt__
        assert isinstance(condition, Condition)
        assert condition.op == "<"
        assert condition.value == 60

    def test_input_ref_serializes_to_marker(self):
        term = prefer(label("tier") == input_ref("customer.tier"))
        assert term["value"] == {"$input": "customer.tier"}

    def test_score_requires_terms(self):
        with pytest.raises(ValueError, match="at least one term"):
            score()

    def test_score_rejects_foreign_terms(self):
        with pytest.raises(ValueError, match="prefer\\(\\)/least\\(\\)"):
            score({"kind": "custom"})

    def test_invalid_selectors_rejected(self):
        with pytest.raises(ValueError, match="non-empty string key"):
            label("")
        with pytest.raises(ValueError, match="non-empty string key"):
            metric("")
        with pytest.raises(ValueError, match="unknown resource field"):
            resource("gpu_flops")

    def test_least_and_most_require_selector_objects(self):
        with pytest.raises(ValueError, match="takes a selector"):
            least("load")
        with pytest.raises(ValueError, match="takes a selector"):
            most("metric:fitness")

    def test_prefer_requires_a_condition(self):
        with pytest.raises(ValueError, match="selector comparison"):
            prefer(label("x"))
        with pytest.raises(ValueError, match="selector comparison"):
            prefer(True)

    def test_selector_cannot_be_compared_to_selector(self):
        with pytest.raises(ValueError, match="constants or input"):
            label("a") == label("b")

    def test_invalid_op_rejected(self):
        with pytest.raises(ValueError, match="op must be one of"):
            Condition(label("x"), "~=", "y")

    def test_invalid_weight_rejected(self):
        for bad in (0, -1, float("inf"), "heavy"):
            with pytest.raises(ValueError, match="weight"):
                least(load(), weight=bad)

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
        policy = score(prefer(label("region") == "eu", weight=10), least(load()))

        # eu-1 is far busier, but the region preference dominates.
        winner = pick_worker([eu, us], policy, loads={"eu-1": 9, "us-1": 0})

        assert winner.name == "eu-1"

    def test_input_resolved_against_execution_input(self):
        gold = _worker("gold-w", labels={"tier": "gold"})
        silver = _worker("silver-w", labels={"tier": "silver"})
        policy = score(prefer(label("tier") == input_ref("tier"), weight=10), least(load()))

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
            prefer(label("tier") == input_ref("customer.tier"), weight=10),
            least(load()),
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
        policy = score(least(metric("queue")))

        assert pick_worker([low, high], policy, loads={}).name == "low"

    def test_most_metric(self):
        weak = _worker("weak", metrics={"fitness": 0.2})
        strong = _worker("strong", metrics={"fitness": 0.9})
        policy = score(most(metric("fitness")))

        assert pick_worker([weak, strong], policy, loads={}).name == "strong"

    def test_missing_metric_scores_worst(self):
        reporting = _worker("reporting", metrics={"fitness": 0.1})
        silent = _worker("silent")
        policy = score(most(metric("fitness")))

        assert pick_worker([reporting, silent], policy, loads={}).name == "reporting"

    def test_metric_absent_everywhere_cannot_discriminate(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(most(metric("fitness")), least(load()))

        winner = pick_worker([a, b], policy, loads={"a": 3, "b": 1})

        assert winner.name == "b"  # only the load term discriminates

    def test_sticky_term_prefers_hinted_worker(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(sticky(weight=5), least(load()))

        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a")

        assert winner.name == "a"

    def test_policy_without_sticky_term_ignores_hint(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(least(load()))

        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a")

        assert winner.name == "b"

    def test_ordering_ops_on_metrics(self):
        cold = _worker("cold", metrics={"temp": 40})
        hot = _worker("hot", metrics={"temp": 90})
        policy = score(prefer(metric("temp") < 60, weight=10))

        assert pick_worker([cold, hot], policy, loads={}).name == "cold"

    def test_ordering_op_on_non_numeric_is_false(self):
        a = _worker("a", labels={"zone": "z1"})
        b = _worker("b")
        policy = score(prefer(label("zone") < "z2", weight=10), least(load()))

        # Strings never satisfy ordering ops: the term matches nobody.
        winner = pick_worker([a, b], policy, loads={"a": 1, "b": 0})

        assert winner.name == "b"

    def test_tie_breaks_deterministically(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(least(load()))

        # Equal scores and loads: name ascends.
        assert pick_worker([b, a], policy, loads={}).name == "a"

    def test_malformed_policy_returns_none(self):
        a = _worker("a")
        for bad in (None, {}, {"terms": "x"}, {"terms": [{"kind": "warp"}]}, {"terms": [42]}):
            assert pick_worker([a], bad, loads={}) is None

    def test_empty_eligible_returns_none(self):
        assert pick_worker([], score(least(load())), loads={}) is None


class TestEvaluatorCoverage:
    """Paths the headline tests don't reach: every operator, resource and
    load selectors inside conditions, and malformed-at-evaluation specs."""

    def test_all_operators_evaluate(self):
        w40 = _worker("w40", metrics={"temp": 40}, labels={"zone": "z1"})
        w90 = _worker("w90", metrics={"temp": 90}, labels={"zone": "z2"})

        cases = [
            (prefer(label("zone") != "z2", weight=10), "w40"),
            (prefer(metric("temp") <= 40, weight=10), "w40"),
            (prefer(metric("temp") > 50, weight=10), "w90"),
            (prefer(metric("temp") >= 90, weight=10), "w90"),
        ]
        for condition, expected in cases:
            winner = pick_worker([w40, w90], score(condition), loads={})
            assert winner.name == expected, condition

    def test_resource_selector_evaluates(self):
        from flux.worker_registry import WorkerResourcesInfo

        def resources(memory: int) -> WorkerResourcesInfo:
            return WorkerResourcesInfo(
                cpu_total=4,
                cpu_available=4,
                memory_total=memory,
                memory_available=memory,
                disk_total=1,
                disk_free=1,
                gpus=[],
            )

        small = WorkerInfo(name="small", resources=resources(1_000))
        big = WorkerInfo(name="big", resources=resources(9_000))
        bare = WorkerInfo(name="bare")  # no resources: scores 0 for the term

        policy = score(most(resource("memory_available")))
        assert pick_worker([small, big, bare], policy, loads={}).name == "big"

    def test_load_selector_in_conditions(self):
        a = _worker("a")
        b = _worker("b")
        policy = score(prefer(load() < 2, weight=10))

        winner = pick_worker([a, b], policy, loads={"a": 5, "b": 0})

        assert winner.name == "b"

    def test_unknown_selector_kind_is_a_missing_value(self):
        # A spec with an unrecognized selector kind (hand-written or from a
        # future version) reads as "no value": the term cannot discriminate.
        a = _worker("a")
        b = _worker("b")
        policy = {
            "terms": [
                {"kind": "most", "selector": "quantum:flux", "weight": 5.0},
                {"kind": "least", "selector": "load", "weight": 1.0},
            ],
        }

        winner = pick_worker([a, b], policy, loads={"a": 3, "b": 1})

        assert winner.name == "b"

    def test_malformed_at_evaluation_variants_return_none(self):
        a = _worker("a")
        bad_specs = [
            {"terms": [{"kind": "least", "selector": "load", "weight": 0}]},
            {"terms": [{"kind": "least", "selector": "load", "weight": "heavy"}]},
            {"terms": [{"kind": "prefer", "selector": "load", "op": "~=", "value": 1}]},
            {"terms": [{"kind": "prefer", "selector": 42, "op": "==", "value": 1}]},
            {"terms": [{"kind": "warp", "selector": "load", "weight": 1.0}]},
        ]
        for spec in bad_specs:
            assert pick_worker([a], spec, loads={}) is None, spec

    def test_condition_rejects_non_constant_values(self):
        with pytest.raises(ValueError, match="constant or input"):
            Condition(label("x"), "==", [1, 2])


class TestWorkflowOption:
    def test_workflow_accepts_policy_and_exposes_it(self):
        from flux.workflow import workflow

        policy = score(least(load()))

        @workflow.with_options(routing=policy)
        async def routed(ctx):
            return 1

        assert routed.routing == policy

    def test_workflow_rejects_non_policy_routing(self):
        from flux.workflow import workflow

        for bad in ("least-loaded", {"terms": "x"}, {"terms": []}, 42):
            with pytest.raises(ValueError, match="flux.routing.score"):
                workflow(func=lambda ctx: 1, name="bad", routing=bad)


class TestMetricsCaps:
    def test_total_cap_admits_merged_payloads_beyond_the_provider_budget(self):
        from flux.routing import MAX_TOTAL_METRICS

        merged = {f"k{i}": 1.0 for i in range(40)}  # > provider budget of 32
        assert validate_worker_metrics(merged, max_keys=MAX_TOTAL_METRICS) is not None
        over = {f"k{i}": 1.0 for i in range(MAX_TOTAL_METRICS + 1)}
        assert validate_worker_metrics(over, max_keys=MAX_TOTAL_METRICS) is None


class TestDynamicScoring:
    """The require() vocabulary in the score stage: dynamic label keys in
    prefer(), service() as a preference, and when()-gated score terms."""

    def test_prefer_accepts_dynamic_label_key(self):
        from flux.routing import label_for

        term = prefer(label_for("cache.", input_ref("dataset")) == "true", weight=5)
        assert term == {
            "kind": "prefer",
            "selector": {"kind": "label", "prefix": "cache.", "input": "dataset"},
            "op": "==",
            "value": "true",
            "weight": 5.0,
        }

    def test_prefer_accepts_service(self):
        from flux.routing import service

        term = prefer(service(input_ref("model")), weight=2)
        assert term["selector"] == {
            "kind": "label",
            "prefix": "flux.service.",
            "input": "model",
        }
        assert term["op"] == "==" and term["value"] == "true"
        assert prefer(service("inference"))["selector"] == "label:flux.service.inference"

    def test_least_and_most_still_reject_dynamic_keys(self):
        from flux.routing import label_for, meta_for

        for dynamic in (
            label_for("cache.", input_ref("dataset")),
            meta_for("approved.", input_ref("artefact")),
        ):
            with pytest.raises(ValueError, match="dynamic keys"):
                least(dynamic)
            with pytest.raises(ValueError, match="dynamic keys"):
                most(dynamic)

    def test_when_wraps_score_terms(self):
        from flux.routing import when

        term = when(input_ref("fast") == "true", least(load(), weight=10))
        assert term == {
            "kind": "when",
            "if": {"input": "fast", "op": "==", "value": "true"},
            "then": {"kind": "least", "selector": "load", "weight": 10.0},
        }
        assert score(term)["terms"] == [term]

    def test_score_rejects_require_flavored_when(self):
        from flux.routing import when

        with pytest.raises(ValueError, match="must wrap a prefer"):
            score(when(input_ref("t") == "1", label("y") == "1"))

    def test_score_rejects_non_dict_terms(self):
        with pytest.raises(ValueError, match="accepts only"):
            score("junk")

    def test_require_rejects_score_flavored_when(self):
        from flux.routing import require, when

        with pytest.raises(ValueError, match="only valid in score"):
            require(when(input_ref("t") == "1", least(load())))

    def test_pick_worker_resolves_dynamic_key(self):
        warm = _worker("warm", labels={"cache.orders": "true"})
        cold = _worker("cold")
        from flux.routing import label_for

        policy = score(
            prefer(label_for("cache.", input_ref("dataset")) == "true", weight=10),
            least(load()),
        )
        # Warm wins despite carrying more load.
        picked = pick_worker(
            [warm, cold],
            policy,
            loads={"warm": 5, "cold": 0},
            input_value={"dataset": "orders"},
        )
        assert picked is warm

    def test_pick_worker_dynamic_key_unresolved_cannot_discriminate(self):
        # Unresolved input or an invalid resolved key: the term scores 0 for
        # everyone (like a missing metric); the policy does not degrade.
        warm = _worker("warm", labels={"cache.orders": "true"})
        cold = _worker("cold")
        from flux.routing import label_for

        policy = score(
            prefer(label_for("cache.", input_ref("dataset")) == "true", weight=10),
            least(load()),
        )
        loads = {"warm": 5, "cold": 0}
        assert pick_worker([warm, cold], policy, loads=loads, input_value={}) is cold
        assert (
            pick_worker([warm, cold], policy, loads=loads, input_value={"dataset": "../x"}) is cold
        )

    def test_prefer_accepts_dynamic_meta_key(self):
        from flux.routing import meta_for

        term = prefer(meta_for("approved.", input_ref("artefact")) == "true", weight=5)
        assert term == {
            "kind": "prefer",
            "selector": {"kind": "meta", "prefix": "approved.", "input": "artefact"},
            "op": "==",
            "value": "true",
            "weight": 5.0,
        }

    def test_pick_worker_resolves_dynamic_meta_key_from_metadata_only(self):
        from flux.routing import meta_for

        approved = _worker("approved", metadata={"approved.model-a": "true"})
        # A worker asserting the same key as a label gets no credit — the
        # term reads the server-held metadata dict exclusively.
        pretender = _worker("pretender", labels={"approved.model-a": "true"})
        policy = score(
            prefer(meta_for("approved.", input_ref("artefact")) == "true", weight=10),
            least(load()),
        )
        picked = pick_worker(
            [approved, pretender],
            policy,
            loads={"approved": 5, "pretender": 0},
            input_value={"artefact": "model-a"},
        )
        assert picked is approved

    def test_pick_worker_dynamic_meta_key_unresolved_cannot_discriminate(self):
        from flux.routing import meta_for

        approved = _worker("approved", metadata={"approved.model-a": "true"})
        idle = _worker("idle")
        policy = score(
            prefer(meta_for("approved.", input_ref("artefact")) == "true", weight=10),
            least(load()),
        )
        loads = {"approved": 5, "idle": 0}
        assert pick_worker([approved, idle], policy, loads=loads, input_value={}) is idle
        assert (
            pick_worker([approved, idle], policy, loads=loads, input_value={"artefact": "../x"})
            is idle
        )

    def test_pick_worker_malformed_dynamic_selector_degrades(self):
        policy = {
            "terms": [
                {
                    "kind": "prefer",
                    "selector": {"kind": "label", "prefix": 1, "input": "x"},
                    "op": "==",
                    "value": "true",
                    "weight": 1.0,
                },
            ],
        }
        assert pick_worker([_worker("a")], policy, loads={}, input_value={}) is None

    def test_pick_worker_when_gates_term_on_input(self):
        from flux.routing import when

        fast = _worker("fast", metrics={"lag": 0.1})
        slow = _worker("slow", labels={"x": "1"}, metrics={"lag": 9.0})
        policy = score(
            prefer(label("x") == "1", weight=1),
            when(input_ref("fast") == "true", least(metric("lag"), weight=100)),
        )
        assert pick_worker([fast, slow], policy, loads={}, input_value={}) is slow
        assert pick_worker([fast, slow], policy, loads={}, input_value={"fast": "true"}) is fast

    def test_pick_worker_when_unresolved_condition_skips_term(self):
        from flux.routing import when

        a = _worker("a", labels={"x": "1"})
        b = _worker("b", metrics={"lag": 0.0})
        policy = score(
            prefer(label("x") == "1", weight=1),
            when(input_ref("fast") == "true", least(metric("lag"), weight=100)),
        )
        assert pick_worker([a, b], policy, loads={}, input_value=None) is a

    def test_pick_worker_malformed_when_degrades(self):
        policy = {"terms": [{"kind": "when", "if": "junk", "then": {"kind": "sticky"}}]}
        assert pick_worker([_worker("a")], policy, loads={}, input_value={}) is None
        policy = {
            "terms": [
                {"kind": "when", "if": {"input": "t", "op": "==", "value": 1}, "then": "junk"},
            ],
        }
        assert pick_worker([_worker("a")], policy, loads={}, input_value={"t": 1}) is None

    def test_pick_worker_when_wrapped_sticky(self):
        from flux.routing import when

        a, b = _worker("a"), _worker("b")
        policy = score(when(input_ref("pin") == "true", sticky(weight=5)), least(load()))
        loads = {"a": 3, "b": 0}
        assert (
            pick_worker([a, b], policy, loads=loads, input_value={"pin": "true"}, preferred="a")
            is a
        )
        assert pick_worker([a, b], policy, loads=loads, input_value={}, preferred="a") is b


class TestWhenWorkerState:
    """when() gating on meta()/metric(): dynamic per-worker state, evaluated
    per candidate (unlike when(input(...)), which resolves once)."""

    def test_when_meta_excludes_non_matching_worker_from_score(self):
        from flux.routing import meta, when

        gpu = WorkerInfo(name="gpu", metadata={"class": "gpu", "priority": 5})
        cpu = WorkerInfo(name="cpu", metadata={"class": "cpu", "priority": 99})
        policy = score(when(meta("class") == "gpu", most(meta("priority"))))

        winner = pick_worker([gpu, cpu], policy, loads={})

        # cpu is excluded from applies_to (its class isn't "gpu"), so it
        # scores 0 regardless of its (higher) priority.
        assert winner.name == "gpu"

    def test_when_metric_excludes_worker_from_prefer(self):
        from flux.routing import metric, when

        busy = _worker("busy", metrics={"q": 200}, labels={"x": "1"})
        idle = _worker("idle", metrics={"q": 5}, labels={"x": "1"})
        policy = score(when(metric("q") < 100, prefer(label("x") == "1", weight=10)))

        winner = pick_worker([busy, idle], policy, loads={})

        assert winner.name == "idle"


class TestWhenServerComputedLoad:
    """when() gating on load()/utilization(): the server counts both from its
    own execution table, so a worker cannot move them to dodge or attract work
    — which is what separates them from the worker-asserted label()/resource().
    """

    def _capped(self, name: str, cap: int | None) -> WorkerInfo:
        return WorkerInfo(name=name, max_concurrent_executions=cap)

    def test_load_gate_keeps_stickiness_until_the_worker_is_busy(self):
        """The motivating case (#204): plain sticky() is either deterministic
        (weight > 1 wins at any load) or defeated by a single execution
        (weight < 1, because least() normalizes to a full 1.0 gap over two
        workers). An absolute gate gives the middle posture."""
        from flux.routing import when

        a, b = _worker("a"), _worker("b")
        policy = score(when(load() < 5, sticky(weight=3)), least(load()))

        # One execution in flight: 'a' keeps its bonus.
        assert pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a") is a
        # Past the gate: the bonus is gone and least(load()) decides.
        assert pick_worker([a, b], policy, loads={"a": 6, "b": 0}, preferred="a") is b

    def test_bare_sticky_cannot_express_that(self):
        """Documents why the gate is needed rather than a weight tweak."""
        a, b = _worker("a"), _worker("b")
        loads = {"a": 6, "b": 0}

        # Deterministic: wins even at load 6.
        assert (
            pick_worker([a, b], score(sticky(weight=3), least(load())), loads=loads, preferred="a")
            is a
        )
        # Defeated by a single execution: 'a' at load 1 already loses.
        assert (
            pick_worker(
                [a, b],
                score(sticky(weight=0.5), least(load())),
                loads={"a": 1, "b": 0},
                preferred="a",
            )
            is b
        )

    def test_reversed_comparison(self):
        from flux.routing import when

        a, b = _worker("a"), _worker("b")
        policy = score(when(5 > load(), sticky(weight=3)), least(load()))
        assert pick_worker([a, b], policy, loads={"a": 1, "b": 0}, preferred="a") is a
        assert pick_worker([a, b], policy, loads={"a": 6, "b": 0}, preferred="a") is b

    def test_utilization_is_load_over_advertised_capacity(self):
        """Same load, different capacity, opposite verdicts — which is the
        whole point: an absolute count cannot tell these two apart."""
        from flux.routing import utilization, when

        other = self._capped("other", 32)
        loads = {"preferred": 3, "other": 0}
        policy = score(when(utilization() < 0.5, sticky(weight=3)), least(load()))

        # 3 of 32 slots: still roomy, so the bonus holds and beats least(load).
        roomy = self._capped("preferred", 32)
        assert pick_worker([roomy, other], policy, loads=loads, preferred="preferred") is roomy

        # The same 3 executions on a 4-slot worker: over the gate, bonus gone.
        cramped = self._capped("preferred", 4)
        assert pick_worker([cramped, other], policy, loads=loads, preferred="preferred") is other

    def test_least_utilization_ranks_a_mixed_fleet_by_headroom(self):
        from flux.routing import utilization

        small = self._capped("small", 4)
        big = self._capped("big", 32)
        loads = {"small": 3, "big": 8}

        # By count 'small' looks emptier; by headroom 'big' plainly is.
        assert pick_worker([small, big], score(least(load())), loads=loads) is small
        assert pick_worker([small, big], score(least(utilization())), loads=loads) is big

    def test_unlimited_capacity_is_unknown_not_idle(self):
        """A worker advertising no capacity must not read as 0% utilized, or
        one legacy worker collects every preference in the fleet."""
        from flux.routing import utilization, when

        legacy = self._capped("legacy", None)
        known = self._capped("known", 8)
        policy = score(when(utilization() < 0.9, sticky(weight=3)), least(load()))

        # 'legacy' carries the heavier load, so it wins only if it wrongly
        # reads as 0% utilized and collects the bonus.
        loads = {"legacy": 5, "known": 4}
        assert pick_worker([legacy, known], policy, loads=loads, preferred="legacy") is known

    def test_gating_a_require_term_is_rejected(self):
        """Skipping a require term makes a worker match MORE easily, so a
        load-gated hard constraint would drop its requirement on exactly the
        busy workers it was meant to steer away from."""
        from flux.routing import utilization, when

        with pytest.raises(ValueError, match="score terms only"):
            when(load() < 5, label("gpu") == "true")
        with pytest.raises(ValueError, match="score terms only"):
            when(utilization() < 0.5, label("gpu") == "true")

    def test_worker_asserted_selectors_are_still_rejected(self):
        from flux.routing import when

        with pytest.raises(ValueError, match="not valid when"):
            when(label("region") == "eu", sticky(weight=2))
        with pytest.raises(ValueError, match="not valid when"):
            when(resource("memory_available") > 1000, sticky(weight=2))

    def test_condition_still_compares_against_a_constant(self):
        from flux.routing import when

        with pytest.raises(ValueError, match="constant"):
            when(load() < input_ref("max"), sticky(weight=2))

    def test_hand_written_require_metadata_fails_loudly(self):
        """when() refuses to build one, so this shape only reaches the server
        as hand-written metadata. It must diagnose as a terminal error rather
        than match no worker and park the execution forever."""
        from flux.routing import require_diagnostic, require_matches

        terms = [
            {
                "kind": "when",
                "if": {"selector": "load", "op": "<", "value": 5},
                "then": {"kind": "match", "selector": "label:gpu", "op": "==", "value": "true"},
            },
        ]

        assert require_diagnostic(terms, None) is not None
        assert require_matches(terms, {"gpu": "true"}, None, loads={"w": 0}) is False
