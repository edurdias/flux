from flux import workflow


@workflow.with_options(namespace="billing")
async def process(ctx):
    return {"namespace": "billing", "result": "billing-process-output"}
