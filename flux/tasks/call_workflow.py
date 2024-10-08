from flux import task
from typing import Callable
from flux.runners import WorkflowRunner
from flux.workflow import is_workflow


@task(name="call_workflow_$workflow")
def call_workflow(workflow: str | Callable, input: any = None):
    if isinstance(workflow, Callable) and is_workflow(workflow):
        return workflow.run(input).output
    return WorkflowRunner.default().run_workflow(workflow, input).output
