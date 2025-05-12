from __future__ import annotations

from datetime import timedelta

from flux import workflow
from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.tasks import pause
from flux.tasks import sleep


@task
async def proces_data():
    # Simulate some data processing
    await sleep(timedelta(seconds=2))
    return "Data processed"


@workflow
async def pause_workflow(ctx: WorkflowExecutionContext):
    result = await proces_data()
    await pause("wait_for_approval")
    return result + " and approved"


if __name__ == "__main__":  # pragma: no cover
    ctx = pause_workflow.run()
    ctx = pause_workflow.run(execution_id=ctx.execution_id)
    print(ctx.to_json())
