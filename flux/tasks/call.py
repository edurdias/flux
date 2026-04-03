from __future__ import annotations

from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError
from flux.task import task
from flux.workflow import workflow as workflow_cls


@task.with_options(name="call_workflow_{workflow}")
async def call(workflow: workflow_cls | str, *args, mode: str = "sync"):
    """Call a workflow directly or via the HTTP API in sync or async mode.

    Args:
        workflow: The workflow to call, can be either:
            - A workflow object: Will be called directly
            - A string: Will be called via the HTTP API
        mode: Execution mode, either "sync" (default) or "async".
            In async mode, returns the execution_id immediately without waiting.

    Raises:
        WorkflowNotFoundError: If the workflow does not exist (when using string name)
        ExecutionError: If the workflow execution fails

    Returns:
        Any: In sync mode, the output of the workflow execution.
             In async mode, the execution_id string.
    """
    from flux.errors import WorkflowNotFoundError
    from flux.domain.execution_context import ExecutionContext

    if isinstance(workflow, workflow_cls):
        workflow = workflow.name

    import httpx
    from flux.config import Configuration

    settings = Configuration.get().settings
    server_url = settings.workers.server_url

    try:
        payload = args[0] if len(args) == 1 else args

        if mode == "async":
            url = f"{server_url}/workflows/{workflow}/run/async"
            with httpx.Client(timeout=settings.workers.default_timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["execution_id"]

        url = f"{server_url}/workflows/{workflow}/run/sync?detailed=true"
        with httpx.Client(timeout=settings.workers.default_timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            ctx: ExecutionContext = ExecutionContext(
                workflow_id=data["workflow_id"],
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

            # TODO: add support for paused workflows
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
