from __future__ import annotations

from typing import Any

import pytest

from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.hooks.selectors import (
    _WORKFLOW_EVENT_STATES,
    events_from_save,
    selector_matches,
    validate_selector,
)


def _ctx(
    *,
    namespace: str,
    name: str,
    execution_id: str,
    state: ExecutionState = ExecutionState.PAUSED,
) -> ExecutionContext:
    return ExecutionContext(
        workflow_id=f"{namespace}/{name}",
        workflow_namespace=namespace,
        workflow_name=name,
        execution_id=execution_id,
        state=state,
    )


def _event(
    event_type: ExecutionEventType,
    *,
    event_id: str,
    name: str,
    value: Any = None,
) -> ExecutionEvent:
    # Mirrors the engine's own convention (see flux/task.py's TASK_STARTED /
    # TASK_AWAITING_APPROVAL construction): source_id is the task call id,
    # duplicated into value["task_call_id"] for the audit payload.
    source_id = value.get("task_call_id", event_id) if isinstance(value, dict) else event_id
    return ExecutionEvent(type=event_type, source_id=source_id, name=name, value=value, id=event_id)


@pytest.mark.parametrize(
    "selector,key,expected",
    [
        ("execution:*", "execution:release:promote:failed", True),
        ("execution:*:*:failed", "execution:release:promote:failed", True),
        ("execution:*:*:failed", "execution:release:promote:completed", False),
        ("execution:release:*:paused", "execution:release:promote:paused", True),
        ("execution:release:*:paused", "execution:ops:promote:paused", False),
        # non-terminal * matches exactly one segment
        ("execution:*:promote:failed", "execution:release:promote:failed", True),
        (
            "task:release:*:promote_prod:awaiting_approval",
            "task:release:pipeline:promote_prod:awaiting_approval",
            True,
        ),
        ("task:release:*:*:rejected", "task:release:pipeline:promote_prod:rejected", True),
        # domains do not cross
        ("execution:*", "task:release:pipeline:promote_prod:rejected", False),
    ],
)
def test_selector_matching(selector, key, expected):
    assert selector_matches(selector, key) is expected


@pytest.mark.parametrize(
    "selector",
    [
        "",
        "workflow:*",  # unknown domain
        "execution",  # no segments
        "execution:a:b",  # too few for the domain
        "execution:a:b:c:d",  # too many
        "task:a:b:c",  # too few for the task domain
        # A terminal `*` covers the remainder of a key, not more of it: the
        # segments before it still have to line up with one, so a prefix
        # longer than the domain ever emits matches nothing at all. Accepting
        # these creates a hook that can never fire, which is worse than a
        # refusal at the door.
        "execution:a:b:c:d:*",
        "task:a:b:c:d:e:*",
    ],
)
def test_invalid_selectors_are_rejected(selector):
    with pytest.raises(ValueError):
        validate_selector(selector)


def test_valid_selectors_are_accepted():
    for selector in (
        "execution:*",
        "task:*",
        "execution:ns:wf:paused",
        "task:ns:wf:task_name:awaiting_approval",
        # Terminal `*` at or inside the domain's width: the prefix still fits
        # a real key, so these fire.
        "execution:ns:*",
        "execution:ns:wf:*",
        "task:ns:wf:task_name:*",
    ):
        validate_selector(selector)


def test_every_workflow_event_announces_a_real_state():
    """A ``WORKFLOW_*`` event the table doesn't know is skipped at runtime --
    deliberately, so a hook derivation never fails a save -- which makes this
    the only place a newly added workflow event gets noticed. The values are
    checked against ``ExecutionState`` too, so a typo can't invent a state no
    selector could ever match."""
    unmapped = [
        member.value
        for member in ExecutionEventType
        if member.value.startswith("WORKFLOW_") and member.value not in _WORKFLOW_EVENT_STATES
    ]
    assert unmapped == []
    assert set(_WORKFLOW_EVENT_STATES.values()) <= {state.value.lower() for state in ExecutionState}


def test_a_workflow_event_keys_on_its_own_state_not_the_contexts():
    """One save can carry several transitions; each is keyed on the event it
    came from, not on where the execution has since landed."""
    ctx = _ctx(
        namespace="release",
        name="pipeline",
        execution_id="exec-1",
        state=ExecutionState.COMPLETED,
    )
    events = [
        _event(ExecutionEventType.WORKFLOW_STARTED, event_id="ev-1", name="pipeline"),
        _event(ExecutionEventType.WORKFLOW_COMPLETED, event_id="ev-2", name="pipeline"),
    ]

    produced = events_from_save(ctx, events)

    assert [e.key for e in produced] == [
        "execution:release:pipeline:running",
        "execution:release:pipeline:completed",
    ]


def test_events_from_save_yields_one_event_per_persisted_event():
    """A save persists a state transition and any new task events; each is a
    separately matchable event, keyed by the persisted event's own id so the
    delivery is idempotent."""
    ctx = _ctx(namespace="release", name="pipeline", execution_id="exec-1")
    events = [
        _event(ExecutionEventType.WORKFLOW_PAUSED, event_id="ev-1", name="pipeline"),
        _event(
            ExecutionEventType.TASK_AWAITING_APPROVAL,
            event_id="ev-2",
            name="promote_prod",
            value={"task_call_id": "call-9", "task_name": "promote_prod"},
        ),
    ]

    produced = events_from_save(ctx, events)

    assert [e.key for e in produced] == [
        "execution:release:pipeline:paused",
        "task:release:pipeline:promote_prod:awaiting_approval",
    ]
    assert [e.event_id for e in produced] == ["ev-1", "ev-2"]
    assert produced[1].task_call_id == "call-9"
