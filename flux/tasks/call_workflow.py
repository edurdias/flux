from flux import task
from flux.runners import WorkflowRunnerMeta


@task(name="call_workflow_$name")
def call_workflow(name: str, input: any = None):
    return WorkflowRunnerMeta.current().run_workflow(name, input).output
