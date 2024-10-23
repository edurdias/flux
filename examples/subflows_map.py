from __future__ import annotations

from examples.subflows import get_stars_workflow
from flux import workflow
from flux import WorkflowExecutionContext


@workflow
def subflows_map_workflow(ctx: WorkflowExecutionContext[list[str]]):
    if not ctx.input:
        raise TypeError("The list of repositories cannot be empty.")

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
    ctx = subflows_map_workflow.run(repositories)
    print(ctx.to_json())
