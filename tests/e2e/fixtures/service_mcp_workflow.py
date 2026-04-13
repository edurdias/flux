from typing import Any

from pydantic import BaseModel

from flux import ExecutionContext, workflow


class GreetInput(BaseModel):
    name: str
    greeting: str = "Hello"


@workflow.with_options(namespace="mcp_test")
async def typed_greet(ctx: ExecutionContext[GreetInput]):
    """Greet someone with a typed input."""
    data = ctx.input
    return {"message": f"{data.greeting}, {data.name}"}


@workflow.with_options(namespace="mcp_test")
async def untyped_add(ctx: ExecutionContext[dict[str, Any]]):
    """Add two numbers."""
    data = ctx.input or {}
    return {"result": data.get("a", 0) + data.get("b", 0)}
