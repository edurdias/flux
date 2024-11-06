from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from types import GeneratorType
from typing import Any
from typing import cast

import flux.catalogs as catalogs
import flux.decorators as decorators
from flux.config import Configuration
from flux.context import WorkflowExecutionContext
from flux.context_managers import ContextManager
from flux.errors import ExecutionError
from flux.errors import ExecutionPaused
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType


class WorkflowExecutor(ABC):
    _current: WorkflowExecutor | None = None

    @classmethod
    def get(cls, options: dict[str, Any] | None = None) -> WorkflowExecutor:
        if cls._current is None:
            cls._current = cls.create(options)
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
    def with_options(self, options: dict[str, Any] | None = None) -> WorkflowExecutor:
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowExecutor:
        return DefaultWorkflowExecutor(options)


class DefaultWorkflowExecutor(WorkflowExecutor):
    def __init__(self, options: dict[str, Any] | None = None):
        settings = Configuration.get().settings.executor

        self.default_timeout = settings.default_timeout
        self.default_retry_attempts = settings.retry_attempts
        self.default_retry_delay = settings.retry_delay
        self.default_retry_backoff = settings.retry_backoff

        self.catalog = catalogs.WorkflowCatalog.create(options)
        self.context_manager = ContextManager.default()
        self._past_events: list[ExecutionEvent] = []

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

        context = self.context_manager.get(execution_id)
        if not context:
            context = WorkflowExecutionContext(name, input, None, [])
            self.context_manager.save(context)

        if context.finished:
            return context

        return self._execute_workflow(workflow, context, input)

    def _execute_workflow(
        self,
        workflow: decorators.workflow,
        ctx: WorkflowExecutionContext,
        input: Any | None = None,
    ) -> WorkflowExecutionContext:
        workflow_generator = workflow(ctx)
        try:
            self._check_generator_type(ctx, workflow_generator)
            self._check_if_resuming(ctx)
            value = None
            if self._resuming:
                self._replay_workflow_start(workflow_generator)
                input_type = self._replay_iterate(workflow_generator, ctx)
                if input_type:
                    # TODO: validate input against type
                    value = input
            else:
                self._start_workflow(workflow_generator, ctx)
            self._iterate(workflow_generator, ctx, value)
        except ExecutionPaused as ex:
            ctx.events.append(
                ExecutionEvent(
                    ExecutionEventType.WORKFLOW_PAUSED,
                    workflow.id,
                    ctx.name,
                    {"reference": ex.reference},
                ),
            )
        except ExecutionError as ex:
            self._handle_execution_error(workflow_generator, ctx, ex)
        except StopIteration as ex:  # noqa: F841
            pass
        except Exception as ex:  # noqa: F841
            pass
        finally:
            self.context_manager.save(ctx)
        return ctx

    def _check_generator_type(self, ctx: WorkflowExecutionContext, generator: GeneratorType):
        if not isinstance(generator, GeneratorType):
            raise ValueError(f"Function {ctx.name} must be a generator")
        decorator = generator.gi_frame.f_locals["self"]
        if not isinstance(decorator, decorators.workflow):
            raise ValueError(
                f"The decorator {type(decorator).__name__} should be an `workflow` decorator.",
            )

    def _check_if_resuming(self, ctx: WorkflowExecutionContext):
        self._past_events = ctx.events.copy()
        self._resuming = bool(self._past_events)

    def _start_workflow(self, generator: GeneratorType, ctx: WorkflowExecutionContext):
        next(generator)
        event = generator.send(None)
        if not self._is_event(event, ExecutionEventType.WORKFLOW_STARTED):
            raise ValueError(f"First event must be {ExecutionEventType.WORKFLOW_STARTED}")
        ctx.events.append(event)

    def _replay_workflow(self, generator: GeneratorType, ctx: WorkflowExecutionContext):
        self._replay_workflow_start(generator)
        return self._replay_iterate(generator, ctx)

    def _replay_iterate(self, generator: GeneratorType, ctx: WorkflowExecutionContext):
        value = generator.send(None)
        while True:
            try:
                if self._is_task(value):
                    replay = self._replay_task(value, ctx)
                    if replay == decorators.END:
                        return value
                    value = replay
                elif isinstance(value, GeneratorType):
                    replay = self._replay_iterate(value, ctx)
                    if self._is_task(value):
                        replay = self._replay_task(value, ctx)
                        if replay == decorators.END:
                            return value
                        value = replay
                elif isinstance(value, decorators.PauseRequested):
                    if self._resuming:
                        last_pause_event = self._find_last_event(ExecutionEventType.WORKFLOW_PAUSED)
                        if value.reference == last_pause_event.value["reference"]:
                            self._resuming = False
                            ctx.events.append(
                                ExecutionEvent(
                                    ExecutionEventType.WORKFLOW_RESUMED,
                                    last_pause_event.source_id,
                                    ctx.name,
                                    {"reference": last_pause_event.value["reference"]},
                                ),
                            )
                            return value.input_type
                value = generator.send(value)
            except StopIteration as ex:
                return ex.value if ex.value else value
            except Exception as ex:
                value = generator.throw(ex)

    def _replay_task(
        self,
        task_generator: GeneratorType,
        ctx: WorkflowExecutionContext,
    ) -> Any:
        task = cast(decorators.task, task_generator.gi_frame.f_locals["self"])
        self._replay_task_start(task_generator, ctx)

        task_past_events = [e for e in self._past_events if e.source_id == task.task_id]
        terminal = [
            e
            for e in task_past_events
            if e.type in (ExecutionEventType.TASK_COMPLETED, ExecutionEventType.TASK_FAILED)
        ]

        if terminal:
            return terminal[0].value

        return decorators.END

    def _replay_task_start(self, task_generator: GeneratorType, ctx: WorkflowExecutionContext):
        event = task_generator.send(None)
        if self._is_event(event, ExecutionEventType.TASK_STARTED):
            self._remove_past_event(event)

    def _replay_workflow_start(self, generator):
        next(generator)
        event = generator.send(None)
        if self._is_event(event, ExecutionEventType.WORKFLOW_STARTED):
            self._remove_past_event(event)

    def _remove_past_event(self, past_event: ExecutionEvent):
        self._past_events.remove(past_event)

    def _find_last_event(self, type: ExecutionEventType):
        return next(e for e in reversed(self._past_events) if e.type == type)

    def _iterate(self, generator: GeneratorType, ctx: WorkflowExecutionContext, value: Any = None):
        value = generator.send(value)
        while True:
            try:
                if self._is_task(value):
                    value = self._execute_task(value, ctx)
                elif isinstance(value, GeneratorType):
                    value = self._iterate(value, ctx)
                elif isinstance(value, decorators.PauseRequested):
                    raise ExecutionPaused(value.reference, value.input_type)
                elif isinstance(value, ExecutionEvent):
                    value = self._process_event(ctx, value)
                value = generator.send(value)
            except StopIteration as ex:
                return ex.value if ex.value else value
            except ExecutionPaused:
                raise
            except Exception as ex:
                try:
                    value = generator.throw(ex)
                except Exception as ex:
                    raise

    def _is_task(self, obj: Any) -> bool:
        if isinstance(obj, GeneratorType) and "self" in obj.gi_frame.f_locals:
            task = obj.gi_frame.f_locals["self"]
            return task and isinstance(task, decorators.task)
        return False

    def _execute_task(
        self,
        task_generator: GeneratorType,
        ctx: WorkflowExecutionContext,
    ) -> Any:
        """Execute a task with configured timeout and retry settings."""
        try:
            task_instance = cast(decorators.task, task_generator.gi_frame.f_locals["self"])

            # Use configured defaults if not specified in task
            if task_instance.timeout in (None, 0):
                task_instance.timeout = self.default_timeout

            if task_instance.retry_max_attemps in (None, 0):
                task_instance.retry_max_attemps = self.default_retry_attempts
                task_instance.retry_delay = self.default_retry_delay
                task_instance.retry_backoff = self.default_retry_backoff

            self._execute_task_start(task_generator, ctx)
            self._execute_task_skip_replay(task_generator)
            result = self._iterate(task_generator, ctx)
            return result
        except ExecutionError:
            raise
        except Exception as ex:  # noqa: F841
            pass
        finally:
            self.context_manager.save(ctx)

    def _execute_task_start(self, task_generator: GeneratorType, ctx: WorkflowExecutionContext):
        event = task_generator.send(None)
        if not self._is_event(event, ExecutionEventType.TASK_STARTED):
            raise ValueError(
                f"Event should be {ExecutionEventType.TASK_STARTED} but got {event.type}.",
            )
        ctx.events.append(event)

    def _execute_task_skip_replay(self, task_generator: GeneratorType):
        task_generator.send(None)
        task_generator.send([None, False])

    def _is_event(self, event: Any, expected_type: ExecutionEventType) -> bool:
        return isinstance(event, ExecutionEvent) and event.type == expected_type

    def _process_event(
        self,
        ctx: WorkflowExecutionContext,
        event: ExecutionEvent,
    ) -> ExecutionEvent | None:
        ctx.events.append(event)
        if event.type in (ExecutionEventType.TASK_COMPLETED, ExecutionEventType.WORKFLOW_COMPLETED):
            return event.value
        return None

    def _handle_execution_error(
        self,
        generator: GeneratorType,
        ctx: WorkflowExecutionContext,
        ex: ExecutionError,
    ):
        event = generator.throw(ex)
        if isinstance(event, ExecutionEvent):
            ctx.events.append(event)
