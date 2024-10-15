import uuid
import random
from datetime import datetime
from flux.decorators import task, workflow
from flux.runners import WorkflowRunner


@task
def now() -> datetime:
    return datetime.now()


@task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@task
def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@task
def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@task.with_options(name="call_workflow_$workflow")
def call_workflow(workflow: str | workflow, input: any = None):
    if isinstance(workflow, workflow) and workflow.is_workflow(workflow):
        return workflow.run(input).output
    return WorkflowRunner.current().run_workflow(workflow, input).output
