import httpx

from flux import task, workflow
from flux.tasks import call_workflow
from flux.catalogs import LocalWorkflowCatalog
from flux.context import WorkflowExecutionContext


@task
def get_repo_info(repo):
    url = f"https://api.github.com/repos/{repo}"
    repo_info = httpx.get(url).json()
    return repo_info


@workflow
def get_stars(ctx: WorkflowExecutionContext[str]):
    repo_info = yield get_repo_info(ctx.input)
    return repo_info["stargazers_count"]


@workflow
def github_stars(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input

    stars = {}
    for repo in repos:
        stars[repo] = yield call_workflow(get_stars, repo)
    return stars


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = github_stars.run(repositories, LocalWorkflowCatalog(globals()))
    print(ctx.to_json())
