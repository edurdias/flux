from __future__ import annotations

import os
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
    ) -> WorkflowExecutionContext:
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
    ) -> WorkflowExecutionContext:
        workflow = self.catalog.get(name)

        ctx = (
            self.context_manager.get(execution_id)
            if execution_id
            else WorkflowExecutionContext(name, input, None, [])
        )

        if ctx.finished:
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
                event.type == ExecutionEventType.WORKFLOW_STARTED
            ), f"First event should always be {ExecutionEventType.WORKFLOW_STARTED}"

            if self._past_events:
                self._past_events.pop(0)
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
        except Exception:
            raise

        self.context_manager.save(ctx)
        return ctx

    def __process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        step: GeneratorType | list | ExecutionEvent | None,
        replay: bool = False,
    ):
        if isinstance(step, GeneratorType):
            try:
                value = next(step)
                return self.__process(ctx, step, value, replay)
            except StopIteration as ex:
                return self.__process(ctx, step, ex.value, replay)

        if isinstance(step, list) and step and all(isinstance(e, GeneratorType) for e in step):
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                value = list(
                    executor.map(
                        lambda i: self.__process(ctx, gen, i),
                        step,
                    ),
                )
                return self.__process(ctx, gen, value)

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.TASK_STARTED:
                task = gen.gi_frame.f_locals["self"]

                next(gen)

                past_events = [e for e in self._past_events if e.source_id == step.source_id]

                if past_events:
                    return self.__process_task_past_events(
                        ctx,
                        gen,
                        task,
                        replay,
                        past_events,
                    )

                ctx.events.append(step)
                value = gen.send([None, False])

                while isinstance(value, GeneratorType):
                    try:
                        value = gen.send(self.__process(ctx, gen, value))
                    except ExecutionError as ex:
                        value = gen.throw(ex)
                    except StopIteration as ex:
                        value = ex.value

                return self.__process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_RETRY_STARTED,
                ExecutionEventType.TASK_RETRY_COMPLETED,
                ExecutionEventType.TASK_RETRY_FAILED,
            ):
                if not replay:
                    ctx.events.append(step)
                value = gen.send(None)
                if value != decorators.END:
                    return self.__process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_FALLBACK_STARTED,
                ExecutionEventType.TASK_FALLBACK_COMPLETED,
            ):
                if not replay:
                    ctx.events.append(step)
                    value = gen.send(None)
                    if value != decorators.END:
                        return self.__process(ctx, gen, value)
            elif step.type == ExecutionEventType.TASK_COMPLETED:
                if not replay:
                    ctx.events.append(step)
                    value = gen.send(None)
                    if value != decorators.END:
                        return self.__process(ctx, gen, value)
            elif step.type == ExecutionEventType.TASK_FAILED:
                if not replay:
                    ctx.events.append(step)
                    value = gen.send(None)
                    if value != decorators.END:
                        return self.__process(ctx, gen, value)
            else:
                if not replay:
                    ctx.events.append(step)
                    value = gen.send(None)
                    if value != decorators.END:
                        return self.__process(ctx, gen, value)

            self.context_manager.save(ctx)
            return step.value

        return step

    def _get_past_event_for(self, event: ExecutionEvent) -> ExecutionEvent:
        assert event == self._past_events[0], "Past event should be the same of current event"

        return self._past_events.pop(0)

    def __process_task_past_events(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        task,
        replay: bool,
        past_events: list[ExecutionEvent],
    ):
        for past_event in past_events:
            self._past_events.remove(past_event)

        paused_events = [e for e in past_events if e.type == ExecutionEventType.WORKFLOW_PAUSED]

        resumed_events = [e for e in past_events if e.type == ExecutionEventType.WORKFLOW_RESUMED]

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
            (
                e
                for e in past_events
                if e.type
                in (
                    ExecutionEventType.TASK_COMPLETED,
                    ExecutionEventType.TASK_FAILED,
                )
            ),
            None,
        )

        gen.send([terminal_event, replay and not task.disable_replay])
        return self.__process(ctx, gen, terminal_event, replay)
