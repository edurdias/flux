from flux import workflow


@workflow.with_options(namespace="svc_test")
async def greet(ctx):
    name = ctx.input or "World"
    return {"message": f"Hello, {name}"}


@workflow.with_options(namespace="svc_test")
async def add(ctx):
    data = ctx.input or {}
    a = data.get("a", 0)
    b = data.get("b", 0)
    return {"result": a + b}
