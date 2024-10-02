import json
import httpx

from flux import activity, workflow
from flux.activities import call_workflow
from flux.encoders import WorkflowContextEncoder
from flux.loaders import LocalFunctionWorkflowLoader
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner


@activity
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
        stars[repo] = yield call_workflow("get_stars", repo)
    return stars


if __name__ == "__main__":
    repositories = [
        "python/cpython",
        "microsoft/vscode",
        "localsend/localsend",
        "srush/GPU-Puzzles",
        "hyperknot/openfreemap",
    ]
    runner = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runner.run_workflow("github_stars", repositories)
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
