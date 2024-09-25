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
#     "execution_id": "2276630b1c774ef1b06625cc55d0e499",
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
#             "time": "2024-09-25T19:02:31.617734"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "get_stars",
#             "value": [
#                 "python/cpython"
#             ],
#             "time": "2024-09-25T19:02:31.617766"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "get_stars",
#             "value": 62529,
#             "time": "2024-09-25T19:02:31.800384"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "get_stars",
#             "value": [
#                 "microsoft/vscode"
#             ],
#             "time": "2024-09-25T19:02:31.800413"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "get_stars",
#             "value": 162816,
#             "time": "2024-09-25T19:02:31.977314"
#         },
#         {
#             "type": "WorkflowCompleted",
#             "name": "github_stars",
#             "value": {
#                 "python/cpython": 62529,
#                 "microsoft/vscode": 162816
#             },
#             "time": "2024-09-25T19:02:31.977418"
#         }
#     ]
# }