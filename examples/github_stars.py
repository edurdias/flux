from __future__ import annotations

import httpx

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def get_stars(repo: str):
    url = f"https://api.github.com/repos/{repo}"
    return httpx.get(url).json()["stargazers_count"]


@workflow
async def github_stars(ctx: ExecutionContext[list[str]]):
    if not ctx.input:
        raise TypeError("The list of repositories cannot be empty.")

    repos = ctx.input
    stars = {}
    for repo in repos:
        stars[repo] = await get_stars(repo)
    return stars


if __name__ == "__main__":  # pragma: no cover
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = github_stars.run(repositories)
    print(ctx.to_json())
