import httpx

from flux import workflow, task, WorkflowExecutionContext, call_workflow


@task
def get_repo_info(repo):
    url = f"https://api.github.com/repos/{repo}"
    repo_info = httpx.get(url).json()
    return repo_info


@workflow
def get_stars_workflow(ctx: WorkflowExecutionContext[str]):
    repo_info = yield get_repo_info(ctx.input)
    return repo_info["stargazers_count"]


@workflow
def subflows(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input

    stars = {}
    for repo in repos:
        stars[repo] = yield call_workflow(get_stars_workflow, repo)
    return stars


if __name__ == "__main__":  # pragma: no cover
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = subflows.run(repositories)
    print(ctx.to_json())
