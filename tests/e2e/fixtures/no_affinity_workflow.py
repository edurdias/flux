from flux import workflow


@workflow
async def no_affinity_task(ctx):
    return {"result": "no_affinity_task_done"}
