"""require(...) affinity expressions: the DSL factories and their fail-closed
evaluation semantics (every row of the semantics table, plus guardrails)."""

from __future__ import annotations

import pytest

from flux.routing import (
    Condition,
    InputCondition,
    label,
    label_for,
    least,
    most,
    optional,
    prefer,
    require,
    require_diagnostic,
    require_matches,
    service,
    when,
)
from flux.routing import input as input_


class TestRequireCompile:
    def test_static_parity_with_dict_affinity(self):
        assert require(label("gpu") == "a100") == [
            {"kind": "match", "selector": "label:gpu", "op": "==", "value": "a100"},
        ]

    def test_input_driven_value(self):
        assert require(label("region") == input_("region")) == [
            {
                "kind": "match",
                "selector": "label:region",
                "op": "==",
                "value": {"$input": "region"},
            },
        ]

    def test_negation(self):
        assert require(label("maintenance") != "true")[0]["op"] == "!="

    def test_dynamic_label_key(self):
        assert require(label_for("sku.", input_("model")) == "true") == [
            {
                "kind": "match",
                "selector": {"kind": "label", "prefix": "sku.", "input": "model"},
                "op": "==",
                "value": "true",
            },
        ]

    def test_service_sugar_static_and_dynamic(self):
        assert require(service("inference")) == [
            {
                "kind": "match",
                "selector": "label:flux.service.inference",
                "op": "==",
                "value": "true",
            },
        ]
        assert require(service(input_("model")))[0]["selector"] == {
            "kind": "label",
            "prefix": "flux.service.",
            "input": "model",
        }

    def test_optional_flag(self):
        term = require(optional(label("node") == input_("node")))[0]
        assert term["optional"] is True

    def test_optional_wraps_service(self):
        term = require(optional(service("inference")))[0]
        assert term["optional"] is True
        assert term["selector"] == "label:flux.service.inference"

    def test_when_compiles_input_condition(self):
        assert require(
            when(input_("tier") == "dedicated", label("cap.dedicated") == "true"),
        ) == [
            {
                "kind": "when",
                "if": {"input": "tier", "op": "==", "value": "dedicated"},
                "then": {
                    "kind": "match",
                    "selector": "label:cap.dedicated",
                    "op": "==",
                    "value": "true",
                },
            },
        ]

    def test_require_needs_terms(self):
        with pytest.raises(ValueError, match="at least one term"):
            require()

    def test_ordered_comparisons_rejected(self):
        with pytest.raises(ValueError, match="only == and !="):
            require(label("size") > "10")

    def test_non_label_selectors_rejected(self):
        from flux.routing import load, metric, resource

        for selector in (metric("temp"), resource("cpu_available"), load()):
            with pytest.raises(ValueError, match="labels or metadata"):
                require(selector == 1)

    def test_label_for_requires_prefix_and_input_ref(self):
        with pytest.raises(ValueError, match="non-empty string prefix"):
            label_for("", input_("model"))
        with pytest.raises(ValueError, match="input"):
            label_for("sku.", "small-8b")
        with pytest.raises(ValueError, match="not a valid label key prefix"):
            label_for("../", input_("model"))

    def test_service_rejects_invalid_names(self):
        # Static names are held to the worker-side socket-name rule
        # (lowercase/digits/single hyphens, max 32) so an expression can
        # never target a name registration could not grant.
        for bad in ("../etc", "Foo", "a--b", "a" * 33, "-lead"):
            with pytest.raises(ValueError, match="lowercase letters"):
                service(bad)
        with pytest.raises(ValueError, match="service name or input"):
            service(42)
        assert service("small-8b")["selector"] == "label:flux.service.small-8b"

    def test_when_condition_must_be_input_condition(self):
        with pytest.raises(ValueError, match="input"):
            when(label("x") == "y", label("a") == "b")

    def test_input_condition_ops_and_values(self):
        cond = input_("tier") == "dedicated"
        assert isinstance(cond, InputCondition)
        assert (input_("tier") != "shared").op == "!="
        with pytest.raises(ValueError, match="constants"):
            input_("a") == input_("b")
        with pytest.raises(ValueError, match="only == and !="):
            InputCondition(input_("a"), "<", 1)
        with pytest.raises(ValueError, match="must be a constant"):
            InputCondition(input_("a"), "==", [1])

    def test_optional_and_when_reject_non_match_terms(self):
        with pytest.raises(ValueError, match="label comparison"):
            optional({"kind": "when"})
        with pytest.raises(ValueError, match="label comparison"):
            when(input_("t") == 1, "junk")

    def test_dynamic_selector_in_score_terms(self):
        # prefer() speaks the dynamic vocabulary (soft counterpart of a
        # require term); least()/most() reject it — labels have no ordering.
        dynamic = label_for("sku.", input_("model"))
        assert prefer(dynamic == "true")["selector"] == {
            "kind": "label",
            "prefix": "sku.",
            "input": "model",
        }
        with pytest.raises(ValueError, match="no ordering"):
            least(dynamic)
        with pytest.raises(ValueError, match="no ordering"):
            most(dynamic)

    def test_condition_still_usable_for_score(self):
        # require() must not break the score() vocabulary.
        cond = label("region") == input_("region")
        assert isinstance(cond, Condition)
        assert prefer(cond)["selector"] == "label:region"


class TestRequireMatches:
    """One test per row of the evaluation-semantics table."""

    def test_unresolved_input_fails_closed(self):
        spec = require(label("region") == input_("region"))
        assert not require_matches(spec, {"region": "eu-west"}, {})
        assert not require_matches(spec, {"region": "eu-west"}, None)

    def test_resolved_input_label_absent(self):
        spec = require(label("region") == input_("region"))
        assert not require_matches(spec, {}, {"region": "eu-west"})

    def test_resolved_input_label_equal(self):
        spec = require(label("region") == input_("region"))
        assert require_matches(spec, {"region": "eu-west"}, {"region": "eu-west"})

    def test_negation_absent_label_passes(self):
        spec = require(label("maintenance") != "true")
        assert require_matches(spec, {}, None)
        assert require_matches(spec, {"maintenance": "false"}, None)
        assert not require_matches(spec, {"maintenance": "true"}, None)

    def test_optional_unresolved_skips(self):
        spec = require(optional(label("node") == input_("node")))
        assert require_matches(spec, {}, {})

    def test_optional_forgives_only_absent_input(self):
        # An invalid resolved key or a non-scalar input is an execution-level
        # error, not a missing pin — optional() does not paper over it.
        spec = require(optional(label_for("sku.", input_("model")) == "true"))
        assert require_matches(spec, {"sku.x": "true"}, {})  # absent: skipped
        assert not require_matches(spec, {"sku.x": "true"}, {"model": "../x"})
        assert not require_matches(spec, {"sku.x": "true"}, {"model": {"nested": 1}})

    def test_dynamic_service_name_validated_on_resolution(self):
        from flux.routing import service

        spec = require(service(input_("model")))
        assert require_matches(spec, {"flux.service.small-8b": "true"}, {"model": "small-8b"})
        # A name worker registration could never grant fails closed...
        assert not require_matches(spec, {"flux.service.small-8b": "true"}, {"model": "Big_8B"})
        # ...and diagnoses, so the execution fails fast instead of parking.
        message = require_diagnostic(spec, {"model": "Big_8B"})
        assert message is not None and "invalid service name" in message

    def test_optional_resolved_false_still_fails(self):
        spec = require(optional(label("node") == input_("node")))
        assert not require_matches(spec, {"node": "a"}, {"node": "b"})
        assert require_matches(spec, {"node": "b"}, {"node": "b"})

    def test_when_unresolved_condition_is_inactive(self):
        spec = require(when(input_("tier") == "dedicated", label("cap") == "true"))
        assert require_matches(spec, {}, {})

    def test_when_active_condition_enforces_term(self):
        spec = require(when(input_("tier") == "dedicated", label("cap") == "true"))
        assert not require_matches(spec, {}, {"tier": "dedicated"})
        assert require_matches(spec, {"cap": "true"}, {"tier": "dedicated"})
        assert require_matches(spec, {}, {"tier": "shared"})

    def test_dynamic_key_resolution(self):
        spec = require(label_for("sku.", input_("model")) == "true")
        assert require_matches(spec, {"sku.small-8b": "true"}, {"model": "small-8b"})
        assert not require_matches(spec, {"sku.small-8b": "true"}, {"model": "big-70b"})

    def test_dynamic_key_invalid_resolution_fails(self):
        spec = require(label_for("sku.", input_("model")) == "true")
        assert not require_matches(spec, {"sku.x": "true"}, {"model": "../x"})
        assert not require_matches(spec, {"sku.x": "true"}, {"model": {"nested": 1}})

    def test_dotted_input_path(self):
        spec = require(label("region") == input_("customer.region"))
        assert require_matches(spec, {"region": "eu"}, {"customer": {"region": "eu"}})
        assert not require_matches(spec, {"region": "eu"}, {"customer": {}})

    def test_values_compare_as_strings_bools_lowercase(self):
        spec = require(label("replicas") == input_("n"), label("hipaa") == input_("phi"))
        assert require_matches(spec, {"replicas": "3", "hipaa": "true"}, {"n": 3, "phi": True})

    def test_terms_are_anded(self):
        spec = require(label("a") == "1", label("b") == "2")
        assert require_matches(spec, {"a": "1", "b": "2"}, None)
        assert not require_matches(spec, {"a": "1"}, None)

    def test_none_and_empty_labels(self):
        spec = require(label("a") == "1")
        assert not require_matches(spec, None, None)
        assert not require_matches(spec, {}, None)

    def test_malformed_specs_fail_closed(self):
        assert not require_matches({"not": "a list"}, {"a": "1"}, None)
        # require() demands at least one term: a hand-written empty spec is
        # malformed, never match-everything.
        assert not require_matches([], {"a": "1"}, None)
        assert not require_matches(["not a dict"], {"a": "1"}, None)
        assert not require_matches([{"kind": "unknown"}], {"a": "1"}, None)
        assert not require_matches(
            [{"kind": "match", "selector": "metric:temp", "op": "==", "value": "1"}],
            {"a": "1"},
            None,
        )
        assert not require_matches(
            [{"kind": "match", "selector": "label:a", "op": "<", "value": "1"}],
            {"a": "1"},
            None,
        )

    def test_malformed_hand_written_metadata_fails_closed(self):
        # Terms the factories cannot produce, but hand-edited wf_metadata can.
        bad_dynamic = {
            "kind": "match",
            "selector": {"kind": "label", "prefix": 1, "input": "x"},
            "op": "==",
            "value": "1",
        }
        bad_value = {"kind": "match", "selector": "label:a", "op": "==", "value": {"$input": ""}}
        bad_if = {"kind": "when", "if": "junk", "then": {"kind": "match"}}
        bad_if_fields = {"kind": "when", "if": {"input": "", "op": "<"}, "then": {}}
        bad_then = {"kind": "when", "if": {"input": "t", "op": "==", "value": 1}, "then": "junk"}
        for term in (bad_dynamic, bad_value, bad_if, bad_if_fields):
            assert not require_matches([term], {"a": "1"}, {"t": 1})
        assert not require_matches([bad_then], {"a": "1"}, {"t": 1})


class TestRequireDiagnostic:
    """Worker-independent problems become dispatch errors, never eternal queues."""

    def test_unresolved_input_names_the_path(self):
        spec = require(label("region") == input_("region"))
        message = require_diagnostic(spec, {})
        assert message is not None and "'region'" in message

    def test_unresolved_dynamic_key_names_the_path(self):
        spec = require(label_for("sku.", input_("model")) == "true")
        message = require_diagnostic(spec, {})
        assert message is not None and "'model'" in message

    def test_invalid_resolved_key_names_the_key(self):
        spec = require(label_for("sku.", input_("model")) == "true")
        message = require_diagnostic(spec, {"model": "../etc"})
        assert message is not None and "invalid label key" in message

    def test_resolvable_input_no_diagnostic(self):
        spec = require(label("region") == input_("region"))
        assert require_diagnostic(spec, {"region": "eu"}) is None

    def test_label_mismatch_is_not_a_diagnostic(self):
        # A mismatch is fleet-dependent: the execution parks, it does not fail.
        spec = require(label("gpu") == "a100")
        assert require_diagnostic(spec, {}) is None

    def test_optional_unresolved_no_diagnostic(self):
        spec = require(optional(label("node") == input_("node")))
        assert require_diagnostic(spec, {}) is None

    def test_optional_invalid_resolution_still_diagnoses(self):
        spec = require(optional(label_for("sku.", input_("model")) == "true"))
        assert require_diagnostic(spec, {}) is None  # absent input: skipped
        message = require_diagnostic(spec, {"model": "../x"})
        assert message is not None and "invalid label key" in message
        message = require_diagnostic(spec, {"model": [1, 2]})
        assert message is not None and "must be a scalar" in message

    def test_when_unresolved_condition_no_diagnostic(self):
        spec = require(when(input_("tier") == "dedicated", label("cap") == input_("cap")))
        assert require_diagnostic(spec, {}) is None

    def test_when_active_term_diagnoses_unresolved_input(self):
        spec = require(when(input_("tier") == "dedicated", label("cap") == input_("cap")))
        message = require_diagnostic(spec, {"tier": "dedicated"})
        assert message is not None and "'cap'" in message

    def test_malformed_hand_written_metadata_diagnoses(self):
        cases = [
            {"not": "a list"},
            [],
            ["not a dict"],
            [{"kind": "unknown"}],
            [
                {
                    "kind": "match",
                    "selector": {"kind": "label", "prefix": 1, "input": "x"},
                    "op": "==",
                    "value": "1",
                },
            ],
            [{"kind": "match", "selector": "label:a", "op": "==", "value": {"$input": ""}}],
            [{"kind": "when", "if": "junk", "then": {"kind": "match"}}],
            [{"kind": "when", "if": {"input": "", "op": "<"}, "then": {}}],
            [{"kind": "when", "if": {"input": "t", "op": "==", "value": 1}, "then": "junk"}],
        ]
        for spec in cases:
            assert "malformed" in (require_diagnostic(spec, {"t": 1}) or ""), spec

    def test_malformed_spec_diagnoses_even_under_optional(self):
        malformed = [{"kind": "match", "selector": "metric:x", "op": "==", "value": "1"}]
        assert "malformed" in (require_diagnostic(malformed, None) or "")
        malformed_optional = [dict(malformed[0], optional=True)]
        assert "malformed" in (require_diagnostic(malformed_optional, None) or "")

    def test_diagnostic_matches_evaluator_verdict(self):
        # Whenever a diagnostic fires, no worker can match — the pair of
        # functions must agree or fail-closed would mean queue-forever.
        specs = [
            require(label("region") == input_("region")),
            require(label_for("sku.", input_("model")) == "true"),
            require(service(input_("model"))),
        ]
        for spec in specs:
            assert require_diagnostic(spec, {}) is not None
            assert not require_matches(spec, {"region": "eu", "sku.x": "true"}, {})


class TestWorkflowOptionValidation:
    def test_with_options_accepts_require_expression(self):
        from flux import workflow

        @workflow.with_options(affinity=require(label("gpu") == "a100"))
        async def wf(ctx):
            return 1

        assert wf.affinity == require(label("gpu") == "a100")

    def test_with_options_accepts_legacy_dict(self):
        from flux import workflow

        @workflow.with_options(affinity={"gpu": "a100"})
        async def wf(ctx):
            return 1

        assert wf.affinity == {"gpu": "a100"}

    def test_with_options_rejects_junk_affinity(self):
        from flux import workflow

        for junk in ("gpu=a100", [], ["nope"], [{"kind": "score"}]):
            with pytest.raises(ValueError, match="affinity must be"):
                workflow.with_options(affinity=junk)(lambda ctx: None)


class TestWorkerMatchesSpecBranch:
    def _worker(self, labels):
        class W:
            pass

        w = W()
        w.labels = labels
        w.resources = None
        w.packages = []
        w.runners = ["subprocess"]
        return w

    def test_spec_affinity_uses_input(self):
        from flux.domain.resource_request import worker_matches

        spec = require(label("region") == input_("region"))
        worker = self._worker({"region": "eu"})
        assert worker_matches(worker, None, spec, input_value={"region": "eu"})
        assert not worker_matches(worker, None, spec, input_value={"region": "us"})
        assert not worker_matches(worker, None, spec, input_value=None)

    def test_dict_affinity_regression(self):
        from flux.domain.resource_request import worker_matches

        worker = self._worker({"gpu": "a100"})
        assert worker_matches(worker, None, {"gpu": "a100"})
        assert not worker_matches(worker, None, {"gpu": "h100"})
