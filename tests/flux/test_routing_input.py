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

    def test_dynamic_key_diagnostic_leaks_neither(self):
        """The dynamic-key path had an unblinded diagnostic that named the
        routing key *and* its resolved value. It reaches executions.output,
        which the worker role can read."""
        from flux.routing import label_for

        terms = require(label_for("cache.", routing_input("dataset")) == "true")

        message = str(require_diagnostic(terms, None, {"dataset": "my/bad key"}))

        assert "my/bad key" not in message
        assert "dataset" not in message

    def test_routing_key_is_blinded_even_when_the_value_ref_is_plain_input(self):
        """The blind flag tracked the *value* reference, but the message names
        the resolved *key* — which for a routing-derived dynamic selector is
        the routing value itself. A plain input() on the right-hand side
        therefore routed around the blinding."""
        terms = [
            {
                "kind": "match",
                "selector": {"kind": "label", "prefix": "cohort.", "routing_input": "c"},
                "op": "==",
                "value": {"$input": "missing"},
            },
        ]

        message = str(require_diagnostic(terms, {}, {"c": "canary-secret"}))

        assert "canary-secret" not in message
        assert "cohort." not in message
        assert "routing constraint unsatisfied" in message

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
        """By rule, not by the coincidence that joined duplicates happen to be
        invalid JSON. The route reads the header with getlist() so the repeat
        is visible here rather than collapsed before it arrives."""
        with pytest.raises(RoutingInputError, match="sent once"):
            parse_routing_input_header(["{}", "{}"])

    def test_single_element_list_is_the_normal_case(self):
        """getlist() always yields a list, so one header arrives as one item."""
        assert parse_routing_input_header(['{"cohort":"canary"}']) == CANARY

    def test_null_body_is_rejected_not_treated_as_absent(self):
        """Sending the header at all is a directive. `null` collapsing to the
        same None as an absent header would return 200 for a dropped one."""
        with pytest.raises(RoutingInputError, match="null"):
            parse_routing_input_header("null")

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


class TestRunEndpointIngress:
    """Exercised through the real route, not the validator.

    The unit tests above passed while the ingress was broken: the header was
    typed `str`, so FastAPI read it with `get()` and silently kept only the
    first of repeated headers. Validating the parser proves nothing about the
    path that feeds it.
    """

    SOURCE = b"""
from flux import workflow, ExecutionContext


@workflow
async def probe(ctx: ExecutionContext):
    return "ok"
"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS", "true")
        from flux.config import Configuration
        from flux.models import DatabaseRepository

        Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'ri.db'}")
        DatabaseRepository._engines.clear()
        from fastapi.testclient import TestClient

        from flux.server import Server

        client = TestClient(Server("127.0.0.1", 0)._create_api())
        assert (
            client.post(
                "/workflows",
                files={"file": ("f.py", self.SOURCE, "text/x-python")},
            ).status_code
            == 200
        )
        yield client
        DatabaseRepository._engines.clear()

    def _run(self, client, headers=None):
        return client.post(
            "/workflows/default/probe/run/async",
            json={"real": "payload"},
            headers=headers,
        )

    def _stored(self, execution_id):
        from flux.models import ExecutionContextModel, RepositoryFactory

        with RepositoryFactory.create_repository().session() as session:
            row = session.get(ExecutionContextModel, execution_id)
            return row.routing_input, row.input

    def test_values_are_stored_off_the_payload(self, client):
        resp = self._run(client, {"X-Flux-Routing-Input": '{"cohort":"canary"}'})

        assert resp.status_code == 200
        routing, payload = self._stored(resp.json()["execution_id"])
        assert routing == {"cohort": "canary"}
        assert payload == {"real": "payload"}, "input must be untouched"

    def test_repeated_header_is_rejected(self, client):
        """Two contradictory directives used to return 200 with one silently
        applied — the exact silent-drop this feature refuses."""
        resp = client.post(
            "/workflows/default/probe/run/async",
            json={"real": "payload"},
            headers=[
                ("X-Flux-Routing-Input", '{"cohort":"canary"}'),
                ("X-Flux-Routing-Input", '{"cohort":"control"}'),
            ],
        )

        assert resp.status_code == 400
        assert "sent once" in str(resp.json()["detail"])

    @pytest.mark.parametrize(
        "raw",
        ["{oops", '"scalar"', '{"a.b":1}', '{"a":{"b.c":1}}'],
    )
    def test_invalid_values_are_rejected_not_dropped(self, client, raw):
        assert self._run(client, {"X-Flux-Routing-Input": raw}).status_code == 400

    def test_absent_header_leaves_no_routing_values(self, client):
        resp = self._run(client)

        assert resp.status_code == 200
        routing, _ = self._stored(resp.json()["execution_id"])
        assert routing is None


class TestAuthoringGuards:
    """Rejections that must fire for routing_input() exactly as they do for
    input(), or an expression compiles into a term that can never match."""

    def test_worker_condition_against_a_routing_ref_is_rejected(self):
        from flux.routing import meta

        with pytest.raises(ValueError, match="constant"):
            when(meta("region") == routing_input("r"), prefer(label("x") == "1"))

    def test_same_rejection_as_the_input_spelling(self):
        from flux.routing import meta

        with pytest.raises(ValueError, match="constant"):
            when(meta("region") == input_ref("r"), prefer(label("x") == "1"))


class TestDigestExclusion:
    """The task id is also the cache key and the approval call_id, so excluding
    a kwarg from it collapses calls that differ only in that kwarg. That is
    wanted for call(routing_input=...) and wrong for anything else — hence a
    declared option rather than a name matched in the shared machinery."""

    def test_call_excludes_routing_input(self):
        from flux.tasks.call import call

        assert call.digest_exclude == ("routing_input",)

        kwargs = {"workflow": "w", "routing_input": {"cohort": "canary"}}
        assert call._digest_kwargs(kwargs) == {"workflow": "w"}

        # and therefore the same id as a call carrying no routing values
        assert call._compute_task_id(
            "c",
            {},
            (),
            call._digest_kwargs(kwargs),
        ) == call._compute_task_id("c", {}, (), {"workflow": "w"})

    def test_an_unrelated_task_keeps_the_kwarg_in_its_identity(self):
        """Otherwise a cached task taking a kwarg of that name would return the
        first call's output for every later value."""
        from flux.task import task

        @task
        async def scorer(model, routing_input=None):
            return model

        assert scorer.digest_exclude == ()
        kwargs = {"model": "m", "routing_input": "a"}
        assert scorer._digest_kwargs(kwargs) == kwargs, "nothing is stripped"

        a = scorer._compute_task_id("s", {}, (), scorer._digest_kwargs(kwargs))
        b = scorer._compute_task_id(
            "s",
            {},
            (),
            scorer._digest_kwargs({"model": "m", "routing_input": "b"}),
        )
        assert a != b, "cache keys must stay distinct for an unrelated task"


class TestScheduleIngress:
    """The clear semantics are the most intricate logic in the feature and had
    no coverage: validate_routing_input normalises {} to None, which the update
    path would otherwise read as "not supplied"."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS", "true")
        from flux.config import Configuration
        from flux.models import DatabaseRepository

        Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sched.db'}")
        DatabaseRepository._engines.clear()
        from fastapi.testclient import TestClient

        from flux.server import Server

        client = TestClient(Server("127.0.0.1", 0)._create_api())
        source = b"""
from flux import workflow, ExecutionContext


@workflow
async def probe(ctx: ExecutionContext):
    return "ok"
"""
        assert (
            client.post(
                "/workflows",
                files={"file": ("f.py", source, "text/x-python")},
            ).status_code
            == 200
        )
        yield client
        DatabaseRepository._engines.clear()

    def _create(self, client, name, routing_input=None):
        body = {
            "workflow_name": "probe",
            "workflow_namespace": "default",
            "name": name,
            "schedule_config": {"type": "interval", "interval_seconds": 3600},
        }
        if routing_input is not None:
            body["routing_input"] = routing_input
        return client.post("/schedules", json=body)

    def _stored(self, name):
        from flux.models import RepositoryFactory, ScheduleModel

        with RepositoryFactory.create_repository().session() as session:
            return session.query(ScheduleModel).filter(ScheduleModel.name == name).first()

    def test_created_with_routing_values(self, client):
        assert self._create(client, "canary", {"cohort": "canary"}).status_code == 200

        assert self._stored("canary").routing_input == {"cohort": "canary"}

    def test_response_omits_them(self, client):
        """Consistent with input_data, which ScheduleResponse also omits."""
        resp = self._create(client, "quiet", {"cohort": "canary"})

        assert "routing_input" not in resp.json()

    def test_invalid_values_are_rejected(self, client):
        assert self._create(client, "bad", {"a.b": 1}).status_code == 400

    def test_empty_object_clears_them(self, client):
        """Without this an operator could set routing values on a schedule and
        never remove them — every future fire keeps routing to the cohort."""
        self._create(client, "clearme", {"cohort": "canary"})
        schedule_id = self._stored("clearme").id

        resp = client.put(f"/schedules/{schedule_id}", json={"routing_input": {}})

        assert resp.status_code == 200
        assert not self._stored("clearme").routing_input

    def test_omitting_the_field_leaves_them_untouched(self, client):
        """Absent must mean "unchanged", or every unrelated edit would wipe
        the routing values."""
        self._create(client, "keepme", {"cohort": "canary"})
        schedule_id = self._stored("keepme").id

        resp = client.put(f"/schedules/{schedule_id}", json={"description": "edited"})

        assert resp.status_code == 200
        assert self._stored("keepme").routing_input == {"cohort": "canary"}


class TestCallIngress:
    """call() validates eagerly and refuses the fast path, which is the
    security-relevant branch: that path never reaches dispatch, so a routing
    value there would be silently discarded."""

    def test_invalid_values_raise_before_any_dispatch(self):
        import asyncio

        from flux.tasks.call import call

        with pytest.raises(RoutingInputError):
            asyncio.run(call._func("some_workflow", routing_input={"a.b": 1}))

    def test_call_declares_the_digest_exclusion(self):
        from flux.tasks.call import call

        assert "routing_input" in call.digest_exclude


class TestIngressAdapters:
    """One adapter per wire shape, shared by every HTTP ingress — the #220
    defect lived in an ingress diverging from the validator, so the mapping
    to 400 and the explicit-clear quirk live in exactly one place."""

    def test_error_maps_to_400_with_the_diagnostic(self):
        from fastapi import HTTPException

        from flux.api.schemas import routing_input_or_400

        with pytest.raises(HTTPException) as excinfo:
            routing_input_or_400({"a.b": 1})

        assert excinfo.value.status_code == 400
        assert "a.b" in str(excinfo.value.detail)

    def test_header_error_maps_to_400(self):
        from fastapi import HTTPException

        from flux.api.schemas import routing_input_header_or_400

        with pytest.raises(HTTPException) as excinfo:
            routing_input_header_or_400("{oops")

        assert excinfo.value.status_code == 400

    def test_explicit_clear_survives_normalisation(self):
        """{} means 'clear the stored value' on schedule updates; the
        validator normalises it to None, which would read as 'leave it
        alone' — the adapter must preserve the distinction."""
        from flux.api.schemas import routing_input_or_400

        assert routing_input_or_400({}) == {}
        assert routing_input_or_400(None) is None
        assert routing_input_or_400({"cohort": "canary"}) == {"cohort": "canary"}
