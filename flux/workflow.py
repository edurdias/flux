from functools import wraps

from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.context import WorkflowExecutionContext
from flux.exceptions import ExecutionException
from flux.runners import LocalWorkflowRunner


# TODO: add retry support
def workflow(function):

    @wraps(function)
    def closure(ctx: WorkflowExecutionContext):
        yield
        workflow_name = f"{ctx.name}_{ctx.execution_id}"
        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_STARTED,
            workflow_name,
            ctx.name,
            ctx.input,
        )
        try:
            output = yield from function(ctx)
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_COMPLETED,
                workflow_name,
                ctx.name,
                output,
            )
        except ExecutionException as ex:
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_FAILED,
                workflow_name,
                ctx.name,
                ex,
            )

    def run(
        input: any = None,
        catalog: WorkflowCatalog = LocalWorkflowCatalog({function.__name__: closure}),
    ) -> WorkflowExecutionContext:
        runner = LocalWorkflowRunner(catalog)
        ctx = runner.run_workflow(function.__name__, input)
        return ctx

    closure.__is_workflow = True
    closure.run = run

    return closure
