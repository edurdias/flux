from functools import wraps
from typing import Callable

from flux.context_managers import ContextManager, InMemoryContextManager
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.runners import WorkflowRunner
from flux.exceptions import ExecutionException
from flux.context import WorkflowExecutionContext
from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog


def workflow(fn: Callable = None):

    def _workflow(func: Callable):

        @wraps(func)
        def closure(ctx: WorkflowExecutionContext):
            qualified_name = _get_qualified_name(ctx)

            yield
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_STARTED,
                qualified_name,
                ctx.name,
                ctx.input,
            )
            try:

                output = yield from (
                    func(ctx) if func.__code__.co_argcount == 1 else func()
                )

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

        def _get_qualified_name(ctx: WorkflowExecutionContext) -> str:
            return f"{ctx.name}_{ctx.execution_id}"

        def run(
            input: any = None,
            catalog: WorkflowCatalog = LocalWorkflowCatalog({func.__name__: closure}),
            context_manager: ContextManager = InMemoryContextManager(),
        ) -> WorkflowExecutionContext:
            runner = WorkflowRunner.default(catalog, context_manager)
            return runner.run_workflow(func.__name__, input)

        def rerun(
            execution_id: str,
            catalog: WorkflowCatalog = LocalWorkflowCatalog({func.__name__: closure}),
            context_manager: ContextManager = InMemoryContextManager(),
        ) -> WorkflowExecutionContext:
            runner = WorkflowRunner.default(catalog, context_manager)
            return runner.rerun_workflow(func.__name__, execution_id)

        closure.__is_workflow = True
        closure.run = run
        closure.rerun = rerun

        return closure

    return _workflow if fn is None else _workflow(fn)


def is_workflow(workflow: Callable):
    return workflow.__is_workflow
