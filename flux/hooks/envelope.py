"""The delivery envelope: what a hook-started workflow receives as input.

``build_envelope`` is the single producer of the shape in the spec's
"Envelope" section. Task 4 (enqueue) stores its whole return value verbatim
as the delivery row's ``payload`` (a JSON column); Task 6 (drain) re-reads
that stored payload and overwrites only ``attempt`` before starting the
target workflow. That round-trip is why the return value must be a plain,
JSON-round-trippable dict -- no dataclasses, datetimes, or enums surviving
inside it -- rather than something that merely serializes once.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any

from flux.hooks.registry import HookIndexEntry
from flux.hooks.selectors import HookEvent


def _json_safe(value: Any) -> Any:
    """Round-trip ``value`` through JSON, coercing anything that can't
    serialize to its ``str()`` at whatever depth it occurs.

    ``event.value`` is whatever a task or workflow recorded -- an arbitrary
    Python object, not necessarily JSON-shaped. A delivery must never fail
    to build over it, so unrepresentable pieces degrade to their string form
    instead of raising.
    """
    return json.loads(json.dumps(value, default=str))


def _redact(envelope: dict) -> dict:
    """Run the redaction module's async entry point from this sync function.

    ``build_envelope`` must stay synchronous -- its signature and both call
    sites (a sync-looking enqueue path today, an async checkpoint path in
    practice) are fixed by the Task 4/6 contract -- but the only redaction
    entry point is async, since it decrypts secrets from the store to build
    the scrub list. A caller with no loop running gets ``asyncio.run``
    directly; a caller already inside one (the checkpoint path, since
    ``ExecutionContext.checkpoint()`` is async and calls into the sync save
    path directly on the same thread) gets the coroutine run to completion
    on a dedicated loop in a worker thread, since nesting a loop inside a
    running one raises.
    """
    from flux.security.redaction import redact_response

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(redact_response(envelope))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, redact_response(envelope)).result()


def build_envelope(
    hook: HookIndexEntry,
    selector: str,
    event: HookEvent,
    *,
    delivery_id: str,
    attempt: int,
    hop: int,
) -> dict:
    """Build the redacted delivery envelope a hook-started workflow receives.

    ``state`` mirrors ``type`` for the ``execution`` domain (a state
    transition *is* the event) and is null for ``task`` (task events already
    carry their own ``type`` distinct from any execution state).
    """
    envelope = {
        "hook": hook.name,
        "selector": selector,
        "delivery_id": delivery_id,
        # The delivery row's key, verbatim: the target dedupes on what it
        # reads here, so the two must be the same string.
        "event_key": event.delivery_key,
        "attempt": attempt,
        "hop": hop,
        "event": {
            "domain": event.domain,
            "type": event.type,
            "execution_id": event.execution_id,
            "workflow_namespace": event.workflow_namespace,
            "workflow_name": event.workflow_name,
            "task_name": event.task_name,
            "task_call_id": event.task_call_id,
            "state": event.type if event.domain == "execution" else None,
            "value": event.value,
            "occurred_at": event.occurred_at,
        },
    }

    return _redact(_json_safe(envelope))


# The keys every envelope this module builds carries, and that nothing but
# a delivery has a reason to. A bare `hop` is not enough of a marker: it is
# a plausible key in an ordinary workflow's input, and treating it as one
# lets anyone who can start an execution with a chosen input claim any place
# in a chain -- in either direction.
_ENVELOPE_MARKERS = ("delivery_id", "event_key")


def parent_hop(execution_input: Any) -> int:
    """The ``hop`` of a hook-started execution's input, else ``-1``.

    A first-generation delivery then computes ``parent_hop(...) + 1 == 0``.
    ``execution_input`` can be any type an ordinary workflow was started
    with, so this must never raise -- and an input that does not carry the
    whole envelope shape is an ordinary execution however its keys are
    named. The result is floored at ``-1`` because ``hop`` arrives from
    stored input a caller may have chosen: a negative one is a forgery
    buying generations under the drain's guard, not a chain with room left.
    """
    if not isinstance(execution_input, dict):
        return -1
    if not all(isinstance(execution_input.get(key), str) for key in _ENVELOPE_MARKERS):
        return -1
    hop = execution_input.get("hop")
    if not isinstance(hop, int) or isinstance(hop, bool):
        return -1
    return max(hop, -1)
