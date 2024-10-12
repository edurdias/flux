
from flux import task, WorkflowRunner, workflow


@task.with_options(name="call_workflow_$workflow")
def call_workflow(workflow: str | workflow, input: any = None):
    if isinstance(workflow, workflow) and workflow.is_workflow(workflow):
        return workflow.run(input).output
    return WorkflowRunner.default().run_workflow(workflow, input).output