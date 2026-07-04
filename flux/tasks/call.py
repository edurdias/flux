from __future__ import annotations

from typing import Literal

from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionError
from flux.task import task
from flux.workflow import workflow as workflow_cls


async def _call_in_process(workflow: workflow_cls, args: tuple):
    """Same-worker mesh hop: run a transient sub-workflow in this process.

    No dispatch round-trip, no execution row, no checkpoints — the child
    context is transient with the default no-op checkpoint, so durability
    guards (pause, approvals) still fail loudly. The caller's task event in
    the parent execution is the audit record of the hop; aggregate
    visibility comes from the transient-hop metrics.
    """
    import time

    from flux.domain.execution_context import ExecutionContext

    payload = args[0] if len(args) == 1 else args
    child: ExecutionContext = ExecutionContext(
        workflow_id=f"{workflow.namespace}/{workflow.name}",
        workflow_namespace=workflow.namespace,
        workflow_name=workflow.name,
        input=payload,
    ).mark_transient()

    from flux.observability import get_metrics

    started = time.monotonic()
    result: ExecutionContext = await workflow(child)
    duration = time.monotonic() - started

    outcome = "completed"
    if result.has_failed:
        outcome = "failed"
    elif result.is_paused:
        outcome = "paused"
    m = get_metrics()
    if m:
        m.record_transient_hop(workflow.namespace, workflow.name, outcome, duration)

    if result.has_succeeded:
        return result.output
    if result.has_failed:
        error = result.output
        raise ExecutionError(
            inner_exception=error if isinstance(error, Exception) else None,
            message=str(error),
        )
    raise ExecutionError(
        message=(
            f"Workflow {workflow.qualified_name} paused during an in-process "
            f"transient call; pause requires durability."
        ),
    )


@task.with_options(name="call_workflow_{workflow}")
async def call(workflow: workflow_cls | str, *args, mode: Literal["sync", "async"] = "sync"):
    """Call a workflow — in-process when possible, otherwise via the HTTP API.

    Args:
        workflow: The workflow to call (workflow object or string name).
        *args: Arguments passed to the workflow.
        mode: Execution mode — "sync" waits for completion and returns output,
              "async" submits and returns the execution_id immediately.

    Returns:
        mode="sync": The workflow output.
        mode="async": The execution_id (str).

    A ``mode="sync"`` call whose target is a **transient workflow object**
    takes the same-worker fast path: the sub-workflow executes in this
    process with no server round-trip and no execution row (disable with
    ``[flux.workers] transient_fast_path = false``). Everything else —
    string references, durable targets, ``mode="async"`` — goes through the
    server, which owns the durable lifecycle and service discovery.
    """
    from flux.domain.execution_context import ExecutionContext
    from flux.errors import WorkflowNotFoundError
    from flux.catalogs import resolve_workflow_ref
    from flux.config import Configuration

    settings = Configuration.get().settings

    if isinstance(workflow, workflow_cls):
        namespace = workflow.namespace
        name = workflow.name
        if (
            mode == "sync"
            and workflow.durability == "transient"
            and settings.workers.transient_fast_path
        ):
            return await _call_in_process(workflow, args)
    else:
        namespace, name = resolve_workflow_ref(workflow)

    if mode not in ("sync", "async"):
        raise ValueError(f"mode must be 'sync' or 'async', got: '{mode}'")

    import httpx

    server_url = settings.workers.server_url

    try:
        payload = args[0] if len(args) == 1 else args
        url = f"{server_url}/workflows/{namespace}/{name}/run/{mode}"

        async with httpx.AsyncClient(timeout=settings.workers.default_timeout) as client:
            if mode == "async":
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["execution_id"]

            url = f"{url}?detailed=true"
            response = await client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            ctx: ExecutionContext = ExecutionContext(
                workflow_id=data["workflow_id"],
                workflow_namespace=data.get("workflow_namespace", "default"),
                workflow_name=data["workflow_name"],
                input=data["input"],
                execution_id=data["execution_id"],
                state=data["state"],
                events=[
                    ExecutionEvent(
                        type=ExecutionEventType(event["type"]),
                        source_id=event["source_id"],
                        name=event["name"],
                        value=event.get("value"),
                    )
                    for event in data["events"]
                ],
                requests=data.get("requests", []),
            )

            if ctx.has_succeeded:
                return ctx.output

            if ctx.has_failed:
                raise ExecutionError(ctx.output)

            if ctx.is_paused:
                raise ExecutionError(
                    message=f"Workflow execution {ctx.workflow_name} was paused, but is not supported for nested calls.",
                )

    except httpx.ConnectError as ex:
        raise ExecutionError(
            message=f"Could not connect to the Flux server at {server_url}.",
        ) from ex
    except httpx.HTTPStatusError as ex:
        if ex.response.status_code == 404:
            raise WorkflowNotFoundError(name=f"{namespace}/{name}")
        raise ExecutionError(ex) from ex
    except Exception as ex:
        raise ExecutionError(ex) from ex
