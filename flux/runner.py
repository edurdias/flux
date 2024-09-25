from flux.context_managers import ContextManager, InMemoryContextManager
from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent, ExecutionEventType
from flux.exceptions import ExecutionException
from flux.loaders import LocalFunctionWorkflowLoader, WorkflowLoader


from types import GeneratorType
from typing import Callable, List


class WorkflowRunner:

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

                # replay past events if necessary or initiate the workflow
                past_events = self._get_past_events(ctx)

                event: None
                if len(past_events) > 0:
                    event = self._replay_events(generator, past_events)
                else:
                    event = generator.send(None)
                    ctx.event_history.append(event)

                # resume playing the workflow
                while True:
                    if event.type == ExecutionEventType.ACTIVITY_STARTED:
                        next(generator)
                        event = generator.send([None, False])
                    elif event.type == ExecutionEventType.WORKFLOW_PAUSED:
                        ctx.event_history.append(event)
                        break
                    else:
                        event = generator.send(event.value)
                    ctx.event_history.append(event)

            except ExecutionException as execution_exception:
                generator.throw(execution_exception)
            except StopIteration:
                pass
            finally:
                self.context_manager.save_context(ctx)

        return ctx

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> List[ExecutionEvent]:
        return [
            e
            for e in ctx.event_history
            if e.type == ExecutionEventType.ACTIVITY_COMPLETED
            or e.type == ExecutionEventType.ACTIVITY_FAILED
        ]

    def _replay_events(
        self,
        generator: GeneratorType,
        past_events: List[ExecutionEvent],
    ) -> ExecutionEvent:
        while len(past_events) > 0:
            event = generator.send(None)
            if event.type == ExecutionEventType.ACTIVITY_STARTED:
                replay = next(e for e in past_events if e.name == event.name)
                if replay:
                    next(generator)
                    event = generator.send([replay.value, True])
                    past_events.remove(replay)
        return event