from typing import Callable, ContextManager, Self
from types import GeneratorType
from abc import ABC, abstractmethod

from flux.context import WorkflowExecutionContext
from flux.context_managers import InMemoryContextManager
from flux.exceptions import ExecutionException
from flux.events import ExecutionEvent, ExecutionEventType
from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog
from flux.utils import call_with_timeout


class WorkflowRunner(ABC):

    @abstractmethod
    def run_workflow(self, name: str, input: any) -> WorkflowExecutionContext:
        raise NotImplementedError()

    @abstractmethod
    def rerun_workflow(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        raise NotImplementedError()

    def default(
        workflow_loader: WorkflowCatalog = LocalWorkflowCatalog(),
        context_manager: ContextManager = InMemoryContextManager(),
    ) -> Self:
        return LocalWorkflowRunner(workflow_loader, context_manager)


class LocalWorkflowRunner(WorkflowRunner):

    def __init__(
        self,
        workflow_loader: WorkflowCatalog = LocalWorkflowCatalog(),
        context_manager: ContextManager = InMemoryContextManager(),
    ):
        self.workflow_loader = workflow_loader
        self.context_manager = context_manager

    def run_workflow(self, name: str, input: any = None) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.load_workflow(name)
        ctx = WorkflowExecutionContext(name, input)
        ctx.runner = self

        self.context_manager.save_context(ctx)

        return call_with_timeout(
            lambda: self._run(workflow, ctx),
            workflow.timeout,
            f"Workflow {ctx.name} execution ({ctx.execution_id}) timed out ({workflow.timeout}s).",
            # lambda e: ctx.events.append(workflow.fail(ctx, e))
        )

    def rerun_workflow(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.load_workflow(name)
        ctx = self.context_manager.get_context(execution_id)

        ctx.runner = self

        return call_with_timeout(
            lambda: self._run(workflow, ctx),
            workflow.timeout,
            f"Workflow {ctx.name} execution ({ctx.execution_id}) timed out ({workflow.timeout}s).",
            lambda e: ctx.events.append(workflow.fail(ctx, e))
        )
        
    def _run(
        self, workflow: Callable, ctx: WorkflowExecutionContext
    ) -> WorkflowExecutionContext:

        if ctx.finished:
            return ctx

        gen = workflow(ctx)
        assert isinstance(
            gen, GeneratorType
        ), f"Function {ctx.name} should be a generator, check if it is decorated by @workflow."
        try:
            # initialize the generator
            next(gen)

            past_events = ctx.events.copy()

            # start workflow
            event = gen.send(None)
            assert (
                event.type == ExecutionEventType.WORKFLOW_STARTED
            ), f"First event should always be {ExecutionEventType.WORKFLOW_STARTED}"

            if past_events:
                past_events.pop(0)
            else:
                ctx.events.append(event)

            # iterate the workflow
            step = gen.send(None)
            while step is not None:
                value = self._process(ctx, gen, past_events, step)
                step = gen.send(value)

        except ExecutionException as execution_exception:
            event = gen.throw(execution_exception)
            ctx.events.append(event)
        except StopIteration:
            pass
        finally:
            self.context_manager.save_context(ctx)

        return ctx

    def _process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        past_events: list[ExecutionEvent],
        step,
    ):
        if isinstance(step, GeneratorType):
            value = next(step)
            return self._process(ctx, step, past_events, value)

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.TASK_STARTED:
                next(gen)
                if past_events:
                    past_start = past_events.pop(0)

                    assert (
                        past_start.id == step.id
                    ), f"Past start event should have the same id of current event."

                    # skip retries
                    while (
                        past_events
                        and past_events[0].id == step.id
                        and past_events[0].type == ExecutionEventType.TASK_RETRY_STARTED
                    ):
                        past_events.pop(0)

                    gen.send([past_events[0].value, True])
                    return self._process(ctx, gen, past_events, past_events[0])

                ctx.events.append(step)
                value = gen.send([None, False])

                if isinstance(value, GeneratorType):
                    try:
                        value = gen.send(self._process(ctx, gen, past_events, value))
                    except ExecutionException as ex:
                        value = gen.throw(ex)
                    except StopIteration:
                        pass

                return self._process(ctx, gen, past_events, value)
            elif step.type in (
                ExecutionEventType.TASK_RETRY_STARTED,
                ExecutionEventType.TASK_RETRY_COMPLETED,
            ):
                ctx.events.append(step)
                value = gen.send(None)
                return self._process(ctx, gen, past_events, value)
            elif step.type in (
                ExecutionEventType.TASK_FALLBACK_STARTED,
                ExecutionEventType.TASK_FALLBACK_COMPLETED,
            ):
                ctx.events.append(step)
                value = gen.send(None)
                return self._process(ctx, gen, past_events, value)
            elif step.type == ExecutionEventType.TASK_COMPLETED:
                if past_events:
                    past_end = past_events.pop(0)
                    return past_end.value
                ctx.events.append(step)
            elif step.type == ExecutionEventType.TASK_FAILED:
                ctx.events.append(step)
                value = gen.send(None)
                return self._process(ctx, gen, past_events, value)
            else:
                if past_events:
                    past_events.pop(0)
                else:
                    ctx.events.append(step)

        self.context_manager.save_context(ctx)
        return step.value

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> list[ExecutionEvent]:
        return [
            e
            for e in ctx.events
            if e.type == ExecutionEventType.TASK_COMPLETED
            or e.type == ExecutionEventType.TASK_FAILED
        ]
