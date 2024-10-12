import httpx

from flux import (
    workflow,
    task,
    WorkflowExecutionContext,
    LocalWorkflowCatalog,
)


@task
def get_repo_info(repo):
    url = f"https://api.github.com/repos/{repo}"
    repo_info = httpx.get(url).json()
    return repo_info


@workflow
def get_stars(ctx: WorkflowExecutionContext[str]):
    repo_info = yield get_repo_info(ctx.input)
    return {ctx.input: repo_info["stargazers_count"]}


@workflow
def subflows_parallel(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input
    responses = yield get_stars.map(repos)
    return {key: value for ctx in responses for key, value in ctx.output.items()}


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = subflows_parallel.run(repositories, LocalWorkflowCatalog(globals()))
    print(ctx.to_json())
