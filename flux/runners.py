from flux.context_managers import ContextManager, InMemoryContextManager
from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent, ExecutionEventType
from flux.exceptions import ExecutionException
from flux.loaders import LocalFunctionWorkflowLoader, WorkflowLoader

from types import GeneratorType
from typing import Callable, List
from abc import ABC, ABCMeta, abstractmethod

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
        workflow_loader: WorkflowLoader = LocalFunctionWorkflowLoader(),
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
        assert isinstance(gen, GeneratorType)
        try:
            # initialize the generator
            next(gen)

            # start workflow
            event = gen.send(None)
            assert event.type == ExecutionEventType.WORKFLOW_STARTED
            ctx.event_history.append(event)

            # iterate the workflow
            step = gen.send(None)
            while step is not None:
                value = self._process(ctx, gen, step)
                step = gen.send(value)

        except ExecutionException as execution_exception:
            gen.throw(execution_exception)
        except StopIteration:
            pass
        finally:
            self.context_manager.save_context(ctx)

        return ctx

    def _process(self, ctx: WorkflowExecutionContext, gen: GeneratorType, step):
        if isinstance(step, GeneratorType):
            value = next(step)
            return self._process(ctx, step, value)

        if isinstance(step, ExecutionEvent):
            if step.type == ExecutionEventType.TASK_STARTED:
                next(gen)
                ctx.event_history.append(step)
                value = gen.send([None, False])
                return self._process(ctx, gen, value)
            elif step.type == ExecutionEventType.TASK_RETRIED:
                ctx.event_history.append(step)
                value = gen.send(None)
                return self._process(ctx, gen, value)
            elif step.type in (
                ExecutionEventType.TASK_COMPLETED,
                ExecutionEventType.TASK_FAILED,
            ):
                ctx.event_history.append(step)
                return step.value
            else:
                ctx.event_history.append(step)
        self.context_manager.save_context(ctx)
        return step

    def _get_past_events(self, ctx: WorkflowExecutionContext) -> List[ExecutionEvent]:
        return [
            e
            for e in ctx.event_history
            if e.type == ExecutionEventType.TASK_COMPLETED
            or e.type == ExecutionEventType.TASK_FAILED
        ]
