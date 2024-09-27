from functools import wraps

from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.context import WorkflowExecutionContext


def workflow(function):

    @wraps(function)
    def closure(ctx: WorkflowExecutionContext):
        yield
        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_STARTED,
            f"{ctx.name}_{ctx.execution_id}",
            ctx.name,
            ctx.input,
        )
        output = yield from function(ctx)
        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_COMPLETED,
            f"{ctx.name}_{ctx.execution_id}",
            ctx.name,
            output,
        )

    closure.__is_workflow = True
    return closure
