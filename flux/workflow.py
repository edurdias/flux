from functools import wraps
from typing import Callable

from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.context import WorkflowExecutionContext
from flux.exceptions import ExecutionException
from flux.runners import LocalWorkflowRunner


def workflow(fn: Callable = None, timeout: int = 0):

    def _workflow(func: Callable):

        @wraps(func)
        def closure(ctx: WorkflowExecutionContext):
            qualified_name = f"{ctx.name}_{ctx.execution_id}"

            yield
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_STARTED,
                qualified_name,
                ctx.name,
                ctx.input,
            )
            try:

                if func.__code__.co_argcount == 1:
                    output = yield from func(ctx)
                output = yield from func()

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
            except TimeoutError as ex:
                yield ExecutionEvent(
                    ExecutionEventType.WORKFLOW_FAILED,
                    qualified_name,
                    ctx.name,
                    ex,
                )
            except Exception as ex:
                # TODO: add retry support
                raise

        def run(
            input: any = None,
            catalog: WorkflowCatalog = LocalWorkflowCatalog({func.__name__: closure}),
        ) -> WorkflowExecutionContext:
            runner = LocalWorkflowRunner(catalog)
            return runner.run_workflow(func.__name__, input)

        def rerun(
            execution_id: str,
            catalog: WorkflowCatalog = LocalWorkflowCatalog({func.__name__: closure}),
        ) -> WorkflowExecutionContext:
            runner = LocalWorkflowRunner(catalog)
            return runner.rerun_workflow(func.__name__, execution_id)

        closure.__is_workflow = True
        closure.run = run
        closure.rerun = rerun

        return closure

    return _workflow if fn is None else _workflow(fn)
