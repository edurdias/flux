from functools import wraps
from typing import Callable

from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.context import WorkflowExecutionContext
from flux.exceptions import ExecutionException
from flux.runners import LocalWorkflowRunner


def workflow(function: Callable):

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
            if function.__code__.co_argcount > 0:
                output = yield from function(ctx)
            else:
                output = yield from function()
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
                ex.inner_exception,
            )
        except Exception as ex:
            # TODO: add retry support
            raise

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
