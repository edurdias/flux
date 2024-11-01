from __future__ import annotations

from examples.parallel_tasks import say_hello
from flux import pause
from flux import workflow
from flux.context import WorkflowExecutionContext


@workflow
def pause_workflow(ctx: WorkflowExecutionContext[str]):
    yield pause("pausing_here")  # reference should be unique
    message = yield say_hello(ctx.input)
    return message


if __name__ == "__main__":  # pragma: no cover
    ctx = pause_workflow.run("Joe")
    while not ctx.finished:  # we could also check for ctx.paused
        ctx = pause_workflow.run(execution_id=ctx.execution_id)
        print(ctx.to_json())
