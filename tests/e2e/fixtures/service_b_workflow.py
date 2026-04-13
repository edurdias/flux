from flux import workflow


@workflow.with_options(namespace="svc_other")
async def multiply(ctx):
    data = ctx.input or {}
    a = data.get("a", 0)
    b = data.get("b", 0)
    return {"result": a * b}
