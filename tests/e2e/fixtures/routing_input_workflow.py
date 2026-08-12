from flux import ExecutionContext, workflow
from flux.routing import label, optional, require, routing_input


@workflow
async def routing_echo(ctx: ExecutionContext):
    """Returns exactly what the worker received.

    The point of the assertion downstream: routing values must be absent from
    this, even though dispatch matched on them.
    """
    return {"seen_input": ctx.input}


@workflow.with_options(
    affinity=require(optional(label("cohort") == routing_input("cohort"))),
)
async def routing_pinned(ctx: ExecutionContext):
    return {"worker": ctx.current_worker, "seen_input": ctx.input}
