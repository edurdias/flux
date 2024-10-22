from examples.subflows import get_stars_workflow
from flux import workflow, WorkflowExecutionContext


@workflow
def subflows_parallel(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input
    responses = yield get_stars_workflow.map(repos)
    return {rc.input: rc.output for rc in responses}


if __name__ == "__main__":  # pragma: no cover
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = subflows_parallel.run(repositories)
    print(ctx.to_json())
