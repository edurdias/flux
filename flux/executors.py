from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor
from types import GeneratorType
from typing import Any
from typing import Callable

import flux.catalogs as catalogs
import flux.decorators as decorators
from flux.context import WorkflowExecutionContext
from flux.context_managers import ContextManager
from flux.errors import ExecutionError
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType


class WorkflowExecutor(ABC):
    _current: WorkflowExecutor | None = None

    @classmethod
    def current(cls, options: dict[str, Any] | None = None) -> WorkflowExecutor:
        if cls._current is None:
            cls._current = WorkflowExecutor.create(options)
        return cls._current.with_options(options)

    @abstractmethod
    def execute(
        self,
        name: str,
        input: Any | None = None,
        execution_id: str | None = None,
        force_replay: bool = False,
    ) -> WorkflowExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def with_options(
        self,
        options: dict[str, Any] | None = None,
    ) -> WorkflowExecutor:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowExecutor:
        return DefaultWorkflowExecutor(options)


class DefaultWorkflowExecutor(WorkflowExecutor):
    def __init__(self, options: dict[str, Any] | None = None):
        self.catalog: catalogs.WorkflowCatalog = catalogs.WorkflowCatalog.create(
            options,
        )
        self.context_manager: ContextManager = ContextManager.default()

    def with_options(self, options: dict[str, Any] | None = None) -> WorkflowExecutor:
        self.catalog = catalogs.WorkflowCatalog.create(options)
        return self

    def execute(
        self,
        name: str,
        input: Any | None = None,
        execution_id: str | None = None,
        force_replay: bool = False,
    ) -> WorkflowExecutionContext:
        workflow = self.catalog.get(name)

        ctx = (
            self.context_manager.get(execution_id)
            if execution_id
            else WorkflowExecutionContext(name, input, None, [])
        )

        if ctx.finished and not force_replay:
            return ctx

        self.context_manager.save(ctx)
        return self._execute(workflow, ctx)

    def _execute(
        self,
        workflow: Callable,
        ctx: WorkflowExecutionContext,
    ) -> WorkflowExecutionContext:
        gen = workflow(ctx)
        assert isinstance(
            gen,
            GeneratorType,
        ), f"Function {ctx.name} should be a generator, check if it is decorated by @workflow."

        try:
            # initialize the generator
            next(gen)
            self._past_events = ctx.events.copy()

            # always start workflow
            event = gen.send(None)
            assert (
                isinstance(event, ExecutionEvent)
                and event.type == ExecutionEventType.WORKFLOW_STARTED
            ), f"First event should always be {ExecutionEventType.WORKFLOW_STARTED}"

            if self._past_events:
                past_start_event = next(
                    e
                    for e in self._past_events
                    if e.type == ExecutionEventType.WORKFLOW_STARTED
                    and e.source_id == event.source_id
                    and e.name == event.name
                )
                self._past_events.remove(past_start_event)
            else:
                ctx.events.append(event)

            # iterate the workflow
            step = gen.send(None)
            while step is not None:
                should_replay = len(self._past_events) > 0
                value = self.__process(ctx, gen, step, replay=should_replay)
                step = gen.send(value)

        except ExecutionError as execution_exception:
            event = gen.throw(execution_exception)
            if isinstance(event, ExecutionEvent):
                ctx.events.append(event)
        except StopIteration:
            pass

        self.context_manager.save(ctx)
        return ctx

    def __process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        step: GeneratorType | list[Any] | ExecutionEvent | None | Any,
        replay: bool = False,
    ) -> Any:
        """
        Process a workflow step and handle its execution flow.

        Args:
            ctx: The workflow execution context
            gen: The generator managing the workflow
            step: The current step to process
            replay: Whether this is a replay of a previous execution

        Returns:
            The processed result of the step
        """
        if isinstance(step, GeneratorType):
            return self._handle_generator_step(ctx, step, replay)

        if isinstance(step, list) and step and all(isinstance(e, GeneratorType) for e in step):
            return self._handle_parallel_steps(ctx, gen, step, replay)

        if isinstance(step, ExecutionEvent):
            return self._handle_event_step(ctx, gen, step, replay)

        return step

    def _handle_generator_step(
        self,
        ctx: WorkflowExecutionContext,
        step: GeneratorType,
        replay: bool,
    ) -> Any:
        """Handle processing of a generator step."""
        try:
            value = next(step)
            return self.__process(ctx, step, value, replay)
        except StopIteration as ex:
            return self.__process(ctx, step, ex.value, replay)

    def _handle_parallel_steps(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        steps: list[GeneratorType],
        replay: bool,
    ) -> Any:
        """Handle processing of parallel steps using ThreadPoolExecutor."""
        with ThreadPoolExecutor() as executor:
            value = list(
                executor.map(
                    lambda step: self.__process(ctx, gen, step, replay),
                    steps,
                ),
            )
            return self.__process(ctx, gen, value, replay)

    def _handle_event_step(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        event: ExecutionEvent,
        replay: bool,
    ) -> Any:
        """Handle processing of an event step."""
        if event.type == ExecutionEventType.TASK_STARTED:
            return self._handle_task_start(ctx, gen, event, replay)

        if not replay:
            ctx.events.append(event)
        value = gen.send(None)
        if value != decorators.END:
            return self.__process(ctx, gen, value)
        self.context_manager.save(ctx)

        instance = gen.gi_frame.f_locals["self"]
        if type(instance) in [decorators.workflow, decorators.task]:
            return instance.output_storage.get(event.value)

        return event.value

    def _handle_task_start(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        event: ExecutionEvent,
        replay: bool,
    ) -> Any:
        """Handle task start event processing."""
        next(gen)

        past_events = [e for e in self._past_events if e.source_id == event.source_id]

        if past_events:
            return self.__process_task_past_events(ctx, gen, replay, past_events)

        ctx.events.append(event)
        value = gen.send([None, False])

        while isinstance(value, GeneratorType):
            try:
                value = gen.send(self.__process(ctx, gen, value))
            except ExecutionError as ex:
                value = gen.throw(ex)
            except StopIteration as ex:
                value = ex.value
            except Exception as ex:
                print(ex)

        return self.__process(ctx, gen, value)

    def __process_task_past_events(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        replay: bool,
        past_events: list[ExecutionEvent],
    ):
        for past_event in past_events:
            self._past_events.remove(past_event)

        paused_events = sorted(
            [e for e in past_events if e.type == ExecutionEventType.WORKFLOW_PAUSED],
            key=lambda e: e.time,
        )

        resumed_events = sorted(
            [e for e in past_events if e.type == ExecutionEventType.WORKFLOW_RESUMED],
            key=lambda e: e.time,
        )

        if len(paused_events) > len(resumed_events):
            latest_pause_event = paused_events[-1]
            ctx.events.append(
                ExecutionEvent(
                    ExecutionEventType.WORKFLOW_RESUMED,
                    latest_pause_event.source_id,
                    latest_pause_event.name,
                    latest_pause_event.value,
                ),
            )

        terminal_event = next(
            e
            for e in past_events
            if e.type
            in (
                ExecutionEventType.TASK_COMPLETED,
                ExecutionEventType.TASK_FAILED,
            )
        )

        gen.send([terminal_event.value, replay])
        return self.__process(ctx, gen, terminal_event, replay)
