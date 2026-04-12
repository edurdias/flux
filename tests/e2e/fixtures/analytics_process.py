from flux import workflow


@workflow.with_options(namespace="analytics")
async def process(ctx):
    return {"namespace": "analytics", "result": "analytics-process-output"}
