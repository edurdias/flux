from __future__ import annotations

from datetime import timedelta

from flux import workflow
from flux.context import WorkflowExecutionContext
from flux.tasks import sleep


@workflow
async def sleep_workflow(ctx: WorkflowExecutionContext):
    await sleep(timedelta(seconds=2))
    await sleep(timedelta(seconds=5))
    await sleep(3.5)


if __name__ == "__main__":  # pragma: no cover
    ctx = sleep_workflow.run()
    print(ctx.to_json())
