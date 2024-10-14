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
    return httpx.get(url).json()


@task
def get_stars(repo: str):
    repo_info = yield get_repo_info(repo)
    return repo_info["stargazers_count"]


@workflow
def task_parallell_nested(ctx: WorkflowExecutionContext[list[str]]):
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
    ctx = task_parallell_nested.run(repositories, LocalWorkflowCatalog(globals()))
    print(ctx.to_json())
