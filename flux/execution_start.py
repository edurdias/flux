"""Creating an execution and putting it in the queue.

Extracted from ``flux.server`` (#264 stage 3). Every path that starts a
workflow -- the API, ``call()``, a hook firing, a schedule coming due --
funnels through here, which is what makes it the right place for the two
side effects that must happen exactly once per run: refreshing a dynamic
workflow's GC clock, and stamping the moment the row entered the queue.

Takes an :class:`~flux.execution_signals.ExecutionSignals` rather than a
server, so nothing in this path needs an HTTP app to exist.
"""

from __future__ import annotations

import time
from typing import Any

from flux.catalogs import WorkflowCatalog
from flux.context_managers import ContextManager
from flux.domain import ExecutionContext
from flux.errors import WorkflowNotFoundError
from flux.execution_signals import ExecutionSignals


def create_execution(
    signals: ExecutionSignals,
    namespace: str,
    workflow_name: str,
    input_data: Any = None,
    version: int | None = None,
    preferred_worker: str | None = None,
    required_worker: str | None = None,
    routing_input: dict | None = None,
    park_ttl: int | None = None,
    name: str | None = None,
) -> ExecutionContext:
    workflow = WorkflowCatalog.create().get(namespace, workflow_name, version)
    if not workflow:
        raise WorkflowNotFoundError(f"Workflow '{namespace}/{workflow_name}' not found")

    # The sticky-routing hint is written in the same transaction as the
    # insert: event-mode dispatch can pick a fresh row up immediately.
    ctx = ContextManager.create().save(
        ExecutionContext(
            workflow_id=workflow.id,
            workflow_namespace=workflow.namespace,
            workflow_name=workflow.name,
            input=input_data,
            requests=workflow.requests,
            name=name,
        ),
        preferred_worker=preferred_worker or None,
        required_worker=required_worker or None,
        routing_input=routing_input or None,
        park_ttl=park_ttl,
        name=name,
    )

    # Every run of a dynamic workflow refreshes its GC clock — this is
    # the single choke point all run paths (API, call(), run_workflow by
    # ref or source) pass through, so a frequently used entry can never
    # be collected just because callers stopped re-registering it.
    from flux._namespace import RESERVED_DYNAMIC_PREFIX

    if workflow.namespace.startswith(RESERVED_DYNAMIC_PREFIX):
        from flux.dynamic_workflows import touch_last_used

        touch_last_used(workflow.namespace, workflow.name)

    signals.stamp_queued(ctx.execution_id, time.monotonic())

    from flux.observability import get_metrics

    m = get_metrics()
    if m:
        m.record_workflow_started(ctx.workflow_namespace, ctx.workflow_name)
        m.record_execution_queued()

    return ctx
