from flux import workflow


@workflow.with_options(affinity={"role": "harness"})
async def affinity_task(ctx):
    return {"result": "affinity_task_done"}
