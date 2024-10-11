from typing import Callable
from flux import task, WorkflowRunner


@task.with_options(name="call_workflow_$workflow")
def call_workflow(workflow: str | Callable, input: any = None):
    if isinstance(workflow, Callable) and workflow.is_workflow(workflow):
        return workflow.run(input).output
    return WorkflowRunner.default().run_workflow(workflow, input).output
