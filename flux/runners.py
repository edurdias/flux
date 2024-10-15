from concurrent.futures import ThreadPoolExecutor
import os
from typing import Callable, Self
from types import GeneratorType
from abc import ABC, abstractmethod

from flux.context import WorkflowExecutionContext
from flux.context_managers import ContextManager
from flux.exceptions import ExecutionException
from flux.events import ExecutionEvent, ExecutionEventType
from flux.catalogs import LocalWorkflowCatalog, WorkflowCatalog


class WorkflowRunner(ABC):

    @classmethod
    def current(cls) -> Self:
        if cls._current is None:
            cls._current = WorkflowRunner.default()
        return cls._current

    @abstractmethod
    def run_workflow(self, name: str, input: any) -> WorkflowExecutionContext:
        raise NotImplementedError()

    @abstractmethod
    def process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        step,
    ):
        raise NotImplementedError()

    def default(
        workflow_loader: WorkflowCatalog = LocalWorkflowCatalog(),
        context_manager: ContextManager = ContextManager.default(),
    ) -> Self:
        return LocalWorkflowRunner(workflow_loader, context_manager)


class LocalWorkflowRunner(WorkflowRunner):

    def __init__(
        self,
        workflow_loader: WorkflowCatalog = LocalWorkflowCatalog(),
        context_manager: ContextManager = ContextManager.default(),
    ):
        self.workflow_loader = workflow_loader
        self.context_manager = context_manager

    def run_workflow(self, name: str, input: any = None) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.get(name)
        ctx = WorkflowExecutionContext(name, input, None, [])
        self.context_manager.save(ctx)
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

            ctx = self.__run(gen, ctx)
        except ExecutionException as execution_exception:
            event = gen.throw(execution_exception)
            ctx.events.append(event)
        except StopIteration as ex:
            pass
        except Exception as ex:
            raise

        self.context_manager.save(ctx)
        return ctx

    def __run(
        self, gen: GeneratorType, ctx: WorkflowExecutionContext
    ) -> WorkflowExecutionContext:
        # initialize the generator
        next(gen)

        self._past_events = ctx.events.copy()

        # start workflow
        event = gen.send(None)
        assert (
            event.type == ExecutionEventType.WORKFLOW_STARTED
        ), f"First event should always be {ExecutionEventType.WORKFLOW_STARTED}"

        if self._past_events:
            self._past_events.pop(0)
        else:
            ctx.events.append(event)

        # iterate the workflow
        step = gen.send(None)
        while step is not None:
            value = self.process(ctx, gen, step)
            step = gen.send(value)

        return ctx

    def process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        step,
    ):
        if isinstance(step, GeneratorType):
            value = next(step)
            return self.process(ctx, step, value)

        if (
            isinstance(step, list)
            and step
            and all(isinstance(e, GeneratorType) for e in step)
        ):
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                value = list(executor.map(lambda i: self.process(ctx, gen, i), step))
                return self.process(ctx, gen, value)

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.TASK_STARTED:
                next(gen)
                if self._past_events:
                    past_start = self._past_events.pop(0)

                    assert (
                        past_start.id == step.id
                    ), f"Past start event should have the same id of current event."

                    # skip retries
                    while (
                        self._past_events
                        and self._past_events[0].id == step.id
                        and self._past_events[0].type
                        == ExecutionEventType.TASK_RETRY_STARTED
                    ):
                        self._past_events.pop(0)

                    gen.send([self._past_events[0].value, True])
                    return self.process(ctx, gen, self._past_events[0])

                ctx.events.append(step)
                value = gen.send([None, False])

                if isinstance(value, GeneratorType):
                    try:
                        value = gen.send(self.process(ctx, gen, value))
                    except ExecutionException as ex:
                        value = gen.throw(ex)
                    except StopIteration:
                        pass

                return self.process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_RETRY_STARTED,
                ExecutionEventType.TASK_RETRY_COMPLETED,
                ExecutionEventType.TASK_RETRY_FAILED,
            ):
                ctx.events.append(step)
                value = gen.send(None)
                return self.process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_FALLBACK_STARTED,
                ExecutionEventType.TASK_FALLBACK_COMPLETED,
            ):
                ctx.events.append(step)
                value = gen.send(None)
                return self.process(ctx, gen, value)
            elif step.type == ExecutionEventType.TASK_COMPLETED:
                if self._past_events:
                    past_end = self._past_events.pop(0)
                    return past_end.value
                ctx.events.append(step)
            elif step.type == ExecutionEventType.TASK_FAILED:
                ctx.events.append(step)
                value = gen.send(None)
                return self.process(ctx, gen, value)
            else:
                if self._past_events:
                    self._past_events.pop(0)
                else:
                    ctx.events.append(step)

            self.context_manager.save(ctx)
            return step.value
        return step

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> list[ExecutionEvent]:
        return [
            e
            for e in ctx.events
            if e.type == ExecutionEventType.TASK_COMPLETED
            or e.type == ExecutionEventType.TASK_FAILED
        ]
