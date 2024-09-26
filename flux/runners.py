from flux.context_managers import ContextManager, InMemoryContextManager
from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent, ExecutionEventType
from flux.exceptions import ExecutionException
from flux.loaders import LocalFunctionWorkflowLoader, WorkflowLoader


from types import GeneratorType
from typing import Callable, List
from abc import ABC, abstractmethod


class WorkflowRunner(ABC):

    @abstractmethod
    def run(self, name: str, input: any) -> WorkflowExecutionContext:
        raise NotImplementedError()

    @abstractmethod
    def rerun(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        raise NotImplementedError()


class LocalWorkflowRunner(WorkflowRunner):

    workflow_loader: WorkflowLoader
    context_manager: ContextManager

    def __init__(
        self,
        workflow_loader: WorkflowLoader = LocalFunctionWorkflowLoader(),
        context_manager: ContextManager = InMemoryContextManager(),
    ):
        self.workflow_loader = workflow_loader
        self.context_manager = context_manager

    def run(self, name: str, input: any) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.load_workflow(name)
        ctx = WorkflowExecutionContext(name, input)
        self.context_manager.save_context(ctx)
        return self._run(workflow, ctx)

    def rerun(self, name: str, execution_id: str) -> WorkflowExecutionContext:
        workflow = self.workflow_loader.load_workflow(name)
        ctx = self.context_manager.get_context(execution_id)
        return self._run(workflow, ctx)

    def _run(
        self, workflow: Callable, ctx: WorkflowExecutionContext
    ) -> WorkflowExecutionContext:

        if ctx.is_finished():
            return ctx

        generator = workflow(ctx)
        if isinstance(generator, GeneratorType):
            try:
                # initialize the generator
                next(generator)

                # start workflow
                event = generator.send(None)
                assert event.type == ExecutionEventType.WORKFLOW_STARTED
                ctx.event_history.append(event)

                # iterate the workflow
                step = generator.send(None)
                while step is not None:
                    step = generator.send(self._process(ctx, generator, step))

            except ExecutionException as execution_exception:
                generator.throw(execution_exception)
            except StopIteration:
                pass
            finally:
                self.context_manager.save_context(ctx)

        return ctx

    def _process(self, ctx: WorkflowExecutionContext, generator: GeneratorType, step):
        if isinstance(step, GeneratorType):
            return self._process(ctx, step, next(step))

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.ACTIVITY_STARTED:
                next(generator)
                ctx.event_history.append(step)
                return self._process(ctx, generator, generator.send([None, False]))
            elif step.type in (
                ExecutionEventType.ACTIVITY_COMPLETED,
                ExecutionEventType.ACTIVITY_FAILED,
            ):
                ctx.event_history.append(step)
                return step.value
            else:
                ctx.event_history.append(step)
        return step

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> List[ExecutionEvent]:
        return [
            e
            for e in ctx.event_history
            if e.type == ExecutionEventType.ACTIVITY_COMPLETED
            or e.type == ExecutionEventType.ACTIVITY_FAILED
        ]

    # def _replay_events(
    #     self,
    #     generator: GeneratorType,
    #     past_events: List[ExecutionEvent],
    # ) -> ExecutionEvent:
    #     while len(past_events) > 0:
    #         event = generator.send(None)
    #         if event.type == ExecutionEventType.ACTIVITY_STARTED:
    #             replay = next(e for e in past_events if e.name == event.name)
    #             if replay:
    #                 next(generator)
    #                 event = generator.send([replay.value, True])
    #                 past_events.remove(replay)
    #     return event
