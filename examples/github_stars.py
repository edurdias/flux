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
#     "execution_id": "523172215fd742aba731819c54a3923a",
#     "input": [
#         "python/cpython",
#         "microsoft/vscode"
#     ],
#     "output": {
#         "python/cpython": 62530,
#         "microsoft/vscode": 162819
#     },
#     "event_history": [
#         {
#             "type": "WorkflowStarted",
#             "id": "github_stars_523172215fd742aba731819c54a3923a",
#             "name": "github_stars",
#             "value": [
#                 "python/cpython",
#                 "microsoft/vscode"
#             ],
#             "time": "2024-09-25T20:54:17.266398"
#         },
#         {
#             "type": "ActivityStarted",
#             "id": "get_stars_6170365351912190828",
#             "name": "get_stars",
#             "value": [
#                 "python/cpython"
#             ],
#             "time": "2024-09-25T20:54:17.266434"
#         },
#         {
#             "type": "ActivityCompleted",
#             "id": "get_stars_6170365351912190828",
#             "name": "get_stars",
#             "value": 62530,
#             "time": "2024-09-25T20:54:17.529040"
#         },
#         {
#             "type": "ActivityStarted",
#             "id": "get_stars_6050774776213323153",
#             "name": "get_stars",
#             "value": [
#                 "microsoft/vscode"
#             ],
#             "time": "2024-09-25T20:54:17.529074"
#         },
#         {
#             "type": "ActivityCompleted",
#             "id": "get_stars_6050774776213323153",
#             "name": "get_stars",
#             "value": 162819,
#             "time": "2024-09-25T20:54:17.780138"
#         },
#         {
#             "type": "WorkflowCompleted",
#             "id": "github_stars_523172215fd742aba731819c54a3923a",
#             "name": "github_stars",
#             "value": {
#                 "python/cpython": 62530,
#                 "microsoft/vscode": 162819
#             },
#             "time": "2024-09-25T20:54:17.780169"
#         }
#     ]
# }
