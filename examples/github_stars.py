import json
import httpx

from flux import task, workflow
from flux.encoders import WorkflowContextEncoder
from flux.context import WorkflowExecutionContext


@task
def get_stars(repo: str):
    url = f"https://api.github.com/repos/{repo}"
    return httpx.get(url).json()["stargazers_count"]


@workflow
def github_stars(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input

    stars = {}
    for repo in repos:
        stars[repo] = yield get_stars(repo)
    return stars


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    ctx = github_stars.run(repositories)
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))