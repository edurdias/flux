from typing import Callable
from types import GeneratorType
from abc import ABC, ABCMeta, abstractmethod

from flux.exceptions import ExecutionException
from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent, ExecutionEventType
from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog
from flux.context_managers import ContextManager, InMemoryContextManager

class WorkflowRunnerMeta(ABCMeta):
    _instance = None

    def __call__(cls, *args, **kwargs):
        meta = cls.__class__
        if meta._instance is None:
            meta._instance = super(WorkflowRunnerMeta, cls).__call__(*args, **kwargs)
        return meta._instance

    @classmethod
    def current(cls):
        return cls._instance


class WorkflowRunner(ABC, metaclass=WorkflowRunnerMeta):

    @abstractmethod
    def run_workflow(self, name: str, input: any) -> WorkflowExecutionContext:
        raise NotImplementedError()

    @abstractmethod
    def rerun_workflow(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        raise NotImplementedError()


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
        self.context_manager.save_context(ctx)
        return self._run(workflow, ctx)

    def rerun_workflow(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.load_workflow(name)
        ctx = self.context_manager.get_context(execution_id)
        return self._run(workflow, ctx)

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
            gen.throw(execution_exception)
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
                        and past_events[0].type == ExecutionEventType.TASK_RETRIED
                    ):
                        past_events.pop(0)

                    gen.send([past_events[0].value, True])
                    return self._process(ctx, gen, past_events, past_events[0])

                ctx.events.append(step)
                value = gen.send([None, False])
                return self._process(ctx, gen, past_events, value)
            elif step.type == ExecutionEventType.TASK_RETRIED:
                ctx.events.append(step)
                value = gen.send(None)
                return self._process(ctx, gen, past_events, value)
            elif step.type in (
                ExecutionEventType.TASK_COMPLETED,
                ExecutionEventType.TASK_FAILED,
            ):
                if past_events:
                    past_end = past_events.pop(0)
                    return past_end.value

                ctx.events.append(step)
                return step.value
            else:
                if past_events:
                    past_events.pop(0)
                else:
                    ctx.events.append(step)
        self.context_manager.save_context(ctx)
        return step

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> list[ExecutionEvent]:
        return [
            e
            for e in ctx.events
            if e.type == ExecutionEventType.TASK_COMPLETED
            or e.type == ExecutionEventType.TASK_FAILED
        ]
