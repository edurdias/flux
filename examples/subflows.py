from __future__ import annotations

import httpx

from flux import call
from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def get_repo_info(repo):
    url = f"https://api.github.com/repos/{repo}"
    response = httpx.get(url)
    response.raise_for_status()  # Raise an exception for 4XX/5XX responses
    repo_info = response.json()
    return repo_info


@workflow
async def get_stars_workflow(ctx: ExecutionContext[str]):
    try:
        repo_info = await get_repo_info(ctx.input)
        return repo_info["stargazers_count"]
    except Exception as e:
        # Handle any exceptions that might occur during API call or data processing
        raise Exception(f"Failed to get stars for {ctx.input}: {str(e)}") from e


@workflow
async def subflows(ctx: ExecutionContext[list[str]]):
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
