import json
import httpx

from flux.activity import activity
from flux.loaders import LocalFunctionWorkflowLoader
from flux.workflow import workflow
from flux.context import WorkflowExecutionContext
from flux.encoders import WorkflowContextEncoder
from flux.runner import WorkflowRunner


@activity
def get_stars(repo: str):
    url = f"https://api.github.com/repos/{repo}"
    count = httpx.get(url).json()["stargazers_count"]
    return count


@workflow
def github_stars(ctx: WorkflowExecutionContext[list[str]]):
    repos = ctx.input
    stars: dict = {}
    for repo in repos:
        stars[repo] = yield from get_stars(repo)
    return stars


if __name__ == "__main__":
    runtime = WorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runtime.run("github_stars", ["python/cpython", "microsoft/vscode"])
    print(json.dumps(ctx, cls=WorkflowContextEncoder))
    
# # Sample Output
# {
#     "name": "github_stars",
#     "execution_id": "0448bda164c84cd2a445551f803dd1bc",
#     "input": [
#         "python/cpython",
#         "microsoft/vscode"
#     ],
#     "output": {
#         "python/cpython": 62529,
#         "microsoft/vscode": 162816
#     },
#     "event_history": [
#         {
#             "type": "WorkflowStarted",
#             "name": "github_stars",
#             "value": [
#                 "python/cpython",
#                 "microsoft/vscode"
#             ],
#             "time": "2024-09-25T18:55:05.328036"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "get_stars",
#             "value": [
#                 "python/cpython"
#             ],
#             "time": "2024-09-25T18:55:10.151758"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "get_stars",
#             "value": 62529,
#             "time": "2024-09-25T18:55:10.408617"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "get_stars",
#             "value": [
#                 "microsoft/vscode"
#             ],
#             "time": "2024-09-25T18:55:10.408664"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "get_stars",
#             "value": 162816,
#             "time": "2024-09-25T18:55:10.648528"
#         },
#         {
#             "type": "WorkflowCompleted",
#             "name": "github_stars",
#             "value": {
#                 "python/cpython": 62529,
#                 "microsoft/vscode": 162816
#             },
#             "time": "2024-09-25T18:55:10.648603"
#         }
#     ]
# }