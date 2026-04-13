from flux import workflow


@workflow.with_options(namespace="svc_dynamic")
async def dyn_hello(ctx):
    name = ctx.input or "World"
    return {"message": f"Hello, {name}"}
