from functools import wraps
from typing import overload

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
        qualified_name = f"{ctx.name}_{ctx.execution_id}"
        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_STARTED,
            qualified_name,
            ctx.name,
            ctx.input,
        )
        try:
            output = yield from function(ctx)
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_COMPLETED,
                qualified_name,
                ctx.name,
                output,
            )
        except ExecutionException as ex:
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_FAILED,
                qualified_name,
                ctx.name,
                ex,
            )

    def run(
        input: any = None,
        catalog: WorkflowCatalog = LocalWorkflowCatalog({function.__name__: closure}),
    ) -> WorkflowExecutionContext:
        runner = LocalWorkflowRunner(catalog)
        return runner.run_workflow(function.__name__, input)

    def rerun(
        execution_id: str,
        catalog: WorkflowCatalog = LocalWorkflowCatalog({function.__name__: closure}),
    ) -> WorkflowExecutionContext:
        runner = LocalWorkflowRunner(catalog)
        return runner.rerun_workflow(function.__name__, execution_id)

    closure.__is_workflow = True
    closure.run = run
    closure.rerun = rerun

    return closure
