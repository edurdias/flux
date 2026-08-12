"""Routing-only execution input (issue #211).

The claim is indistinguishability: an execution carrying routing values must
look identical, to the worker, to one that carries none. Nothing else in the
suite fails when that breaks — the values simply start being visible — so the
surface assertions below mirror the design's table one-to-one.
"""

from __future__ import annotations

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.routing import (
    input as input_ref,
    label,
    prefer,
    require,
    require_diagnostic,
    require_matches,
    routing_input,
    score,
    when,
)
from flux.routing_input import (
    RoutingInputError,
    parse_cli_pairs,
    parse_routing_input_header,
    validate_routing_input,
)

LABELS = {"cohort": "canary"}
CANARY = {"cohort": "canary"}


class TestSourcesNeverCross:
    """The whole design rests on routing_input() and input() reading different
    stores. If they ever cross, the value being hidden is read from the payload
    that is delivered."""

    def test_routing_term_reads_routing_values(self):
        terms = require(label("cohort") == routing_input("cohort"))

        assert require_matches(terms, LABELS, None, routing_value=CANARY) is True
        assert require_matches(terms, LABELS, None, routing_value={"cohort": "control"}) is False

    def test_routing_term_does_not_fall_back_to_input(self):
        terms = require(label("cohort") == routing_input("cohort"))

        assert require_matches(terms, LABELS, CANARY, routing_value=None) is False

    def test_input_term_does_not_read_routing_values(self):
        terms = require(label("cohort") == input_ref("cohort"))

        assert require_matches(terms, LABELS, None, routing_value=CANARY) is False
        assert require_matches(terms, LABELS, CANARY) is True


class TestEveryEncoding:
    """input() compiles three different ways and each needed a routing twin. A
    missed one does not raise — it resolves against `input`, silently reading
    the wrong source."""

    def test_comparison_value(self):
        spec = require(label("cohort") == routing_input("cohort"))

        assert spec[0]["value"] == {"$routing_input": "cohort"}

    def test_when_condition(self):
        spec = score(when(routing_input("tier") == "gold", prefer(label("x") == "1")))

        assert spec["terms"][0]["if"]["routing_input"] == "tier"

    def test_dynamic_key(self):
        from flux.routing import label_for

        spec = require(label_for("cache.", routing_input("dataset")) == "true")

        assert spec[0]["selector"]["routing_input"] == "dataset"

    def test_dynamic_key_resolves_from_routing_values(self):
        from flux.routing import label_for

        terms = require(label_for("cache.", routing_input("dataset")) == "true")
        labels = {"cache.orders": "true"}

        assert require_matches(terms, labels, None, routing_value={"dataset": "orders"}) is True
        assert require_matches(terms, labels, {"dataset": "orders"}) is False


class TestDiagnosticsAreBlind:
    """`_fail_undispatchable` writes diagnostics into `output`, which the
    execution read API returns — and the `worker` built-in role holds
    execution:*:read. A message naming the key still separates "absent" from
    "present but unmatched"."""

    def test_routing_diagnostic_names_neither_key_nor_value(self):
        terms = require(label("cohort") == routing_input("secret_cohort"))

        message = require_diagnostic(terms, None, None)

        assert message == "routing constraint unsatisfied"
        assert "secret_cohort" not in message

    def test_input_diagnostics_keep_their_detail(self):
        terms = require(label("cohort") == input_ref("normal_field"))

        message = require_diagnostic(terms, None, None)

        assert "normal_field" in message


class TestValidation:
    """Rejected, never dropped: a discarded routing directive routes the
    execution somewhere the caller did not intend, invisibly."""

    def test_valid_shapes(self):
        assert parse_routing_input_header('{"cohort":"canary"}') == CANARY
        assert parse_routing_input_header('{"a":{"b":1}}') == {"a": {"b": 1}}
        assert parse_routing_input_header(None) is None

    @pytest.mark.parametrize(
        "raw",
        ["{oops", '"scalar"', "[1,2]", '{"a.b":1}'],
    )
    def test_rejected_shapes(self, raw):
        with pytest.raises(RoutingInputError):
            parse_routing_input_header(raw)

    def test_nested_dotted_key_is_rejected_too(self):
        """Path resolution splits the whole path on dots, so {"a": {"b.c": 1}}
        is exactly as unreachable as {"a.b": 1}. A rule written only for the
        top level re-admits the silent unreachability it exists to prevent."""
        with pytest.raises(RoutingInputError, match=r"contains '\.'"):
            validate_routing_input({"a": {"b.c": 1}})

    def test_repeated_header_is_rejected_by_rule(self):
        """Starlette joins duplicates into invalid JSON, which would reject it
        by accident. Resting the rule on that coincidence is the silent-drop
        risk this validation exists to remove."""
        with pytest.raises(RoutingInputError, match="more than once"):
            parse_routing_input_header(["{}", "{}"])

    def test_oversized_is_rejected(self):
        with pytest.raises(RoutingInputError, match="exceeds"):
            parse_routing_input_header('{"k":"' + "x" * 5000 + '"}')

    @pytest.mark.parametrize("pairs", [("nokey",), ("=v",), ("a=1", "a=2")])
    def test_cli_pairs_rejected(self, pairs):
        with pytest.raises(RoutingInputError):
            parse_cli_pairs(pairs)

    def test_cli_pairs_accepted(self):
        assert parse_cli_pairs(("cohort=canary", "region=eu")) == {
            "cohort": "canary",
            "region": "eu",
        }


class TestNotVisibleToTheWorker:
    """One assertion per surface in the design's table. Each fails if the
    field is ever added to that surface — verified by adding it temporarily,
    not assumed."""

    def _ctx(self):
        return ExecutionContext(
            workflow_id="default/probe",
            workflow_namespace="default",
            workflow_name="probe",
            input={"real": "payload"},
        )

    def test_execution_context_has_no_routing_input(self):
        ctx = self._ctx()

        assert not hasattr(ctx, "routing_input")
        assert "routing_input" not in ctx.to_dict()

    def test_serialized_context_has_no_routing_input(self):
        assert "routing_input" not in self._ctx().to_json()

    def test_summary_has_no_routing_input(self):
        from flux.servers.models import ExecutionContext as ExecutionContextDTO

        dto = ExecutionContextDTO.from_domain(self._ctx())

        assert "routing_input" not in dto.summary()

    def test_round_trip_through_the_wire_drops_nothing_and_adds_nothing(self):
        ctx = self._ctx()

        restored = ExecutionContext.from_json(ctx.to_dict())

        assert restored.input == {"real": "payload"}
        assert not hasattr(restored, "routing_input")
