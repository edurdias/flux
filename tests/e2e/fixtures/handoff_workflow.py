from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task
async def record(path: str, tag: str):
    with open(path, "a") as f:
        f.write(tag + "\n")
    return tag


@workflow.with_options(affinity={"role": "handoff"})
async def handoff_workflow(ctx: ExecutionContext):
    path = ctx.input
    await record(path, "a")
    await record(path, "b")
    await pause("gate")
    await record(path, "c")
    return "done"
