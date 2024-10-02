from flux import activity
from flux.runners import WorkflowRunnerMeta


@activity(name="call_workflow_$name")
def call_workflow(name: str, input: any = None):
    return WorkflowRunnerMeta.current().run_workflow(name, input).output
