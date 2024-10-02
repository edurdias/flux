from flux import activity
from flux.runners import WorkflowRunnerMeta


@activity
def call_workflow(name: str, input: any = None):
    return WorkflowRunnerMeta.current().run_workflow(name, input).output
