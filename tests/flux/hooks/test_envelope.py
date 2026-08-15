"""build_envelope: the exact spec shape, redacted, plus hop accounting.

The envelope is what a hook-started workflow receives as its input. Task 4
(enqueue) stores the whole return value as the delivery row's ``payload``;
Task 6 (drain) re-reads it and overwrites only ``attempt`` before starting
the target workflow -- so the shape here is load-bearing for both.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from flux.hooks.envelope import build_envelope, parent_hop
from flux.hooks.registry import HookIndexEntry
from flux.hooks.selectors import HookEvent
from flux.secret_managers import SecretManager
from flux.security import redaction


@pytest.fixture(autouse=True)
def _clear_redaction_cache():
    """``collect_secret_values`` caches its result for 30s at module scope
    (see ``flux/security/redaction.py``). Earlier tests in this file call
    ``build_envelope`` too, which would otherwise leave a stale (often
    empty) cache behind for the secret-redaction test below -- the same
    pollution ``tests/security/test_redaction.py`` guards against."""
    redaction._cache = None
    yield
    redaction._cache = None


def _entry(name: str = "notify-approvals") -> HookIndexEntry:
    return HookIndexEntry(
        id="hook-1",
        name=name,
        selectors=("task:release:*:promote_prod:awaiting_approval",),
        workflow_ref="ops/notify_slack",
        principal_id="p-1",
        max_attempts=5,
    )


def _task_event() -> HookEvent:
    return HookEvent(
        domain="task",
        key="task:release:pipeline:promote_prod:awaiting_approval",
        execution_id="exec-1",
        workflow_namespace="release",
        workflow_name="promote_prod_pipeline",
        event_id="ev-1",
        type="awaiting_approval",
        task_name="promote_prod",
        task_call_id="call-1",
        value={"reason": "manual gate"},
        occurred_at="2026-08-14T12:00:00+00:00",
    )


def _execution_event() -> HookEvent:
    return HookEvent(
        domain="execution",
        key="execution:release:pipeline:completed",
        execution_id="exec-1",
        workflow_namespace="release",
        workflow_name="promote_prod_pipeline",
        event_id="ev-2",
        type="completed",
        task_name=None,
        task_call_id=None,
        value=None,
        occurred_at="2026-08-14T12:05:00+00:00",
    )


def _event_with_value(value) -> HookEvent:
    return HookEvent(
        domain="execution",
        key="execution:release:pipeline:completed",
        execution_id="exec-1",
        workflow_namespace="release",
        workflow_name="promote_prod_pipeline",
        event_id="ev-3",
        type="completed",
        task_name=None,
        task_call_id=None,
        value=value,
        occurred_at="2026-08-14T12:05:00+00:00",
    )


def test_envelope_carries_the_spec_shape():
    envelope = build_envelope(
        _entry(name="notify-approvals"),
        selector="task:release:*:promote_prod:awaiting_approval",
        event=_task_event(),
        delivery_id="d-1",
        attempt=1,
        hop=0,
    )

    assert envelope["hook"] == "notify-approvals"
    assert envelope["selector"] == "task:release:*:promote_prod:awaiting_approval"
    assert envelope["delivery_id"] == "d-1"
    assert envelope["attempt"] == 1 and envelope["hop"] == 0
    assert envelope["event"]["domain"] == "task"
    assert envelope["event"]["task_name"] == "promote_prod"
    assert envelope["event"]["state"] is None
    assert envelope["event"]["occurred_at"].endswith("Z") or "T" in envelope["event"]["occurred_at"]


def test_execution_domain_state_mirrors_type_and_task_fields_are_null():
    envelope = build_envelope(
        _entry(),
        selector="execution:*",
        event=_execution_event(),
        delivery_id="d-2",
        attempt=1,
        hop=0,
    )

    assert envelope["event"]["domain"] == "execution"
    assert envelope["event"]["state"] == envelope["event"]["type"] == "completed"
    assert envelope["event"]["task_name"] is None
    assert envelope["event"]["task_call_id"] is None


def test_event_key_is_the_persisted_event_id():
    envelope = build_envelope(
        _entry(),
        selector="task:release:*:promote_prod:awaiting_approval",
        event=_task_event(),
        delivery_id="d-1",
        attempt=1,
        hop=0,
    )

    assert envelope["event_key"] == "ev-1"


def test_envelope_is_plain_json_serializable_even_with_an_opaque_value():
    class Opaque:
        def __str__(self):
            return "opaque-marker"

    envelope = build_envelope(
        _entry(),
        selector="execution:*",
        event=_event_with_value(Opaque()),
        delivery_id="d-3",
        attempt=1,
        hop=0,
    )

    dumped = json.dumps(envelope)
    assert "opaque-marker" in dumped


def test_secret_values_are_redacted_in_the_envelope(isolated_db):
    """Redaction happens when the envelope is built, before anything else can
    read it."""
    SecretManager.current().save("api_key", "s3cr3t")
    envelope = build_envelope(
        _entry(),
        selector="execution:*",
        event=_event_with_value({"token": "s3cr3t"}),
        delivery_id="d",
        attempt=1,
        hop=0,
    )

    assert "s3cr3t" not in json.dumps(envelope)


async def test_redaction_still_applies_from_inside_a_running_event_loop(isolated_db):
    """``build_envelope`` stays synchronous, but its real caller
    (``_decide_approval`` -> ``ContextManager.save`` -> the hook enqueue)
    runs on the FastAPI event-loop thread with a loop already active, not on
    a worker thread. ``asyncio_mode = "auto"`` (pyproject.toml) means this
    ``async def`` test itself runs inside a running loop, so calling the
    sync ``build_envelope`` from here -- without any extra setup -- exercises
    the same ``concurrent.futures.ThreadPoolExecutor`` branch of
    ``envelope._redact`` that caller would hit, instead of only the
    no-loop-running branch every other test in this file takes."""
    assert asyncio.get_running_loop() is not None  # sanity: a loop is live here

    SecretManager.current().save("api_key", "s3cr3t-in-loop")
    envelope = build_envelope(
        _entry(),
        selector="execution:*",
        event=_event_with_value({"token": "s3cr3t-in-loop"}),
        delivery_id="d-loop",
        attempt=1,
        hop=0,
    )

    assert "s3cr3t-in-loop" not in json.dumps(envelope)


def test_parent_hop_reads_a_hook_started_execution():
    assert (
        parent_hop({"hook": "h", "delivery_id": "d-1", "event_key": "exec-1:ev-1", "hop": 2}) == 2
    )


@pytest.mark.parametrize("value", [None, "text", {"anything": "else"}, [1, 2]])
def test_parent_hop_of_an_ordinary_execution_is_minus_one(value):
    assert parent_hop(value) == -1


def test_parent_hop_is_defensive_against_a_wrong_typed_hop_key():
    assert parent_hop({"hop": "not-an-int"}) == -1
    assert parent_hop({"hop": None}) == -1


@pytest.mark.parametrize(
    "value",
    [
        {"hop": 3},
        {"hop": 3, "delivery_id": "d-1"},
        {"hop": 3, "event_key": "exec-1:ev-1"},
        {"hop": 3, "delivery_id": 7, "event_key": "exec-1:ev-1"},
    ],
)
def test_an_input_that_merely_carries_hop_is_not_a_hook_started_one(value):
    """A bare ``hop`` key is not the marker. A workflow whose ordinary input
    happens to carry one would otherwise have its first-generation deliveries
    dead-lettered as "hop limit reached", and anyone able to start an
    execution with a chosen input could claim any place in a chain."""
    assert parent_hop(value) == -1


def test_a_forged_negative_hop_buys_no_extra_generations():
    """``{"hop": -1000}`` on a started execution would otherwise mint a chain
    of a thousand generations under the guard."""
    forged = {"delivery_id": "d-1", "event_key": "exec-1:ev-1", "hop": -1000}

    assert parent_hop(forged) == -1
