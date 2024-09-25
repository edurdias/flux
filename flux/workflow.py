from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.context import WorkflowExecutionContext


def workflow(function):

    def closure(ctx: WorkflowExecutionContext):
        yield
        yield ExecutionEvent(ExecutionEventType.WORKFLOW_STARTED, ctx.name, ctx.input)
        output = yield from function(ctx)
        yield ExecutionEvent(ExecutionEventType.WORKFLOW_COMPLETED, ctx.name, output)

    closure.__is_workflow = True
    return closure