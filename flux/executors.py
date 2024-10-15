import os

from typing import Callable, Self
from types import GeneratorType
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from flux.context import WorkflowExecutionContext
from flux.context_managers import ContextManager
from flux.exceptions import ExecutionException
from flux.events import ExecutionEvent, ExecutionEventType
from flux.catalogs import WorkflowCatalog


class WorkflowExecutor(ABC):

    _current: Self = None

    @classmethod
    def current(cls, options: dict[str, any] = None) -> Self:
        if cls._current is None:
            cls._current = WorkflowExecutor.create(options)
        return cls._current

    @abstractmethod
    def execute(
        self, name: str, input: any = None, execution_id: str = None
    ) -> WorkflowExecutionContext:
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, any] = None) -> Self:
        return DefaultWorkflowExecutor(options)


class Options:

    module: str


class DefaultWorkflowExecutor(WorkflowExecutor):

    def __init__(self, options: Options):
        self.catalog = WorkflowCatalog.create(options)
        self.context_manager = ContextManager.default()

    def execute(
        self, name: str, input: any = None, execution_id: str = None
    ) -> WorkflowExecutionContext:
        workflow = self.catalog.get(name)

        ctx = self.context_manager.get(execution_id) or WorkflowExecutionContext(
            name, input, None, []
        )

        self.context_manager.save(ctx)
        return self._execute(workflow, ctx)

    def _execute(
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
                value = self.__process(
                    ctx, gen, step, replay=len(self._past_events) > 0
                )
                step = gen.send(value)

        except ExecutionException as execution_exception:
            event = gen.throw(execution_exception)
            ctx.events.append(event)
        except StopIteration as ex:
            pass
        except Exception as ex:
            raise

        self.context_manager.save(ctx)
        return ctx

    def __process(
        self,
        ctx: WorkflowExecutionContext,
        gen: GeneratorType,
        step: GeneratorType | list | ExecutionEvent,
        replay: bool = False,
    ):
        if isinstance(step, GeneratorType):
            # if isinstance(step.gi_frame.f_locals['self'], d.workflow):
            value = next(step)
            return self.__process(ctx, step, value)

        if (
            isinstance(step, list)
            and step
            and all(isinstance(e, GeneratorType) for e in step)
        ):
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                value = list(executor.map(lambda i: self.__process(ctx, gen, i), step))
                return self.__process(ctx, gen, value)

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.TASK_STARTED:
                next(gen)

                source_pasts_events = [
                    e for e in self._past_events if e.source_id == step.source_id
                ]

                if source_pasts_events:
                    for spe in source_pasts_events:
                        self._past_events.remove(spe)
                        if spe.type in (
                            ExecutionEventType.TASK_COMPLETED,
                            ExecutionEventType.TASK_FAILED,
                        ):
                            value = gen.send([spe, True])
                            return self.__process(ctx, gen, spe, replay=True)

                ctx.events.append(step)
                value = gen.send([None, False])

                if isinstance(value, GeneratorType):
                    try:
                        value = gen.send(self.__process(ctx, gen, value))
                    except ExecutionException as ex:
                        value = gen.throw(ex)
                    except StopIteration:
                        pass

                return self.__process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_RETRY_STARTED,
                ExecutionEventType.TASK_RETRY_COMPLETED,
                ExecutionEventType.TASK_RETRY_FAILED,
            ):
                if not replay:
                    ctx.events.append(step)
                value = gen.send(None)
                return self.__process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_FALLBACK_STARTED,
                ExecutionEventType.TASK_FALLBACK_COMPLETED,
            ):
                if not replay:
                    ctx.events.append(step)
                value = gen.send(None)
                return self.__process(ctx, gen, value)
            elif step.type == ExecutionEventType.TASK_COMPLETED:
                if not replay:
                    ctx.events.append(step)
            elif step.type == ExecutionEventType.TASK_FAILED:
                if not replay:
                    ctx.events.append(step)
                value = gen.send(None)
                return self.__process(ctx, gen, value)
            else:
                if not replay:
                    ctx.events.append(step)

            self.context_manager.save(ctx)
            return step.value
        return step

    def _get_past_event_for(self, event: ExecutionEvent) -> ExecutionEvent:
        assert (
            event == self._past_events[0]
        ), f"Past event should be the same of current event"

        return self._past_events.pop(0)
