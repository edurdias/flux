from __future__ import annotations

from typing import Literal

from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionError
from flux.task import task
from flux.workflow import workflow as workflow_cls


@task.with_options(name="call_workflow_{workflow}")
async def call(workflow: workflow_cls | str, *args, mode: Literal["sync", "async"] = "sync"):
    """Call a workflow via the HTTP API.

    Args:
        workflow: The workflow to call (workflow object or string name).
        *args: Arguments passed to the workflow.
        mode: Execution mode — "sync" waits for completion and returns output,
              "async" submits and returns the execution_id immediately.

    Returns:
        mode="sync": The workflow output.
        mode="async": The execution_id (str).
    """
    from flux.domain.execution_context import ExecutionContext
    from flux.errors import WorkflowNotFoundError

    if isinstance(workflow, workflow_cls):
        workflow = workflow.name

    if mode not in ("sync", "async"):
        raise ValueError(f"mode must be 'sync' or 'async', got: '{mode}'")

    import httpx
    from flux.config import Configuration

    settings = Configuration.get().settings
    server_url = settings.workers.server_url

    try:
        payload = args[0] if len(args) == 1 else args
        url = f"{server_url}/workflows/{workflow}/run/{mode}"

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
            raise WorkflowNotFoundError(name=workflow)
        raise ExecutionError(ex) from ex
    except Exception as ex:
        raise ExecutionError(ex) from ex
