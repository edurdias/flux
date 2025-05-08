from __future__ import annotations

import httpx

from flux import call
from flux import task
from flux import workflow
from flux import WorkflowExecutionContext


@task
async def get_repo_info(repo):
    url = f"https://api.github.com/repos/{repo}"
    repo_info = httpx.get(url).json()
    return repo_info


@workflow
async def get_stars_workflow(ctx: WorkflowExecutionContext[str]):
    repo_info = await get_repo_info(ctx.input)
    return repo_info["stargazers_count"]


@workflow
async def subflows(ctx: WorkflowExecutionContext[list[str]]):
    if not ctx.input:
        raise TypeError("The list of repositories cannot be empty.")

    repos = ctx.input
    stars = {}
    for repo in repos:
        stars[repo] = await call(get_stars_workflow, repo)
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
