from flux import ExecutionContext, workflow


@workflow.with_options(namespace="mcp_dynamic")
async def dyn_mcp_hello(ctx: ExecutionContext[str]):
    """Dynamic MCP test workflow."""
    return {"message": f"Hello, {ctx.input}"}
