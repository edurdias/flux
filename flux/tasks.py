import uuid
import random
from datetime import datetime

import flux.decorators as d
from flux.executors import WorkflowExecutor


@d.task
def now() -> datetime:
    return datetime.now()


@d.task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@d.task
def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@d.task
def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@d.task.with_options(name="call_workflow_$workflow")
def call_workflow(workflow: str | d.workflow, input: any = None):
    name = workflow.name if isinstance(workflow, d.workflow) else str(workflow)
    return WorkflowExecutor.current().execute(name, input).output
