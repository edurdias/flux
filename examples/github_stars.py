import os
import json
import httpx
import multiprocessing as mp

from flux import activity, workflow
from flux.loaders import LocalFunctionWorkflowLoader
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner
from flux.encoders import WorkflowContextEncoder


@activity
def get_stars(repo: str):
    return _get_stars(repo)


def _get_stars(repo: str):
    url = f"https://api.github.com/repos/{repo}"
    return httpx.get(url).json()["stargazers_count"]


@activity
def get_all_stars(repos: list[str]):
    with mp.Pool(os.cpu_count()) as pool:
        stars = pool.map(_get_stars, repos)
        return {r: s for r, s in zip(repos, stars)}


@workflow
def github_stars(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input
    
    # single activity with parallel requests
    result = yield from get_all_stars(repos)
    return result

    # sequential execution of multiple activities
    stars: dict = {}
    for repo in repos:
        stars[repo] = yield from get_stars(repo)
    return stars


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
        "kestra-io/kestra",
    ]
    runtime = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runtime.run("github_stars", repositories)
    print(json.dumps(ctx, cls=WorkflowContextEncoder))
