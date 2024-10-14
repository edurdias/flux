import httpx

from flux import (
    workflow,
    task,
    WorkflowExecutionContext,
    LocalWorkflowCatalog,
)


@task
def get_stars(repo: str):
    url = f"https://api.github.com/repos/{repo}"
    repo_info = httpx.get(url).json()
    return repo_info["stargazers_count"]


@workflow
def task_parallel(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input
    stars = yield get_stars.map(repos)
    return dict(zip(repos, stars))


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = task_parallel.run(repositories, LocalWorkflowCatalog(globals()))
    print(ctx.to_json())
