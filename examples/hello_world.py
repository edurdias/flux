import json

from flux.activity import activity
from flux.loaders import LocalFunctionWorkflowLoader
from flux.workflow import workflow
from flux.context import WorkflowExecutionContext
from flux.encoders import WorkflowContextEncoder
from flux.runner import WorkflowRunner


@activity
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    hello_you = yield from say_hello(ctx.input)
    hello_world = yield from say_hello("World")
    return [hello_you, hello_world]


runtime = WorkflowRunner(LocalFunctionWorkflowLoader(globals()))
ctx = runtime.run("hello_world", "Joe")

print(json.dumps(ctx, cls=WorkflowContextEncoder))

# # Sample Output:
# {
#     "name": "hello_world",
#     "execution_id": "1534a61cc7654c6798477d12eaaf5ad7",
#     "input": "Joe",
#     "output": [
#         "Hello, Joe",
#         "Hello, World"
#     ],
#     "event_history": [
#         {
#             "type": "WorkflowStarted",
#             "name": "hello_world",
#             "value": "Joe",
#             "time": "2024-09-25T18:32:32.607174"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "say_hello",
#             "value": [
#                 "Joe"
#             ],
#             "time": "2024-09-25T18:32:32.607201"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "say_hello",
#             "value": "Hello, Joe",
#             "time": "2024-09-25T18:32:32.607643"
#         },
#         {
#             "type": "ActivityStarted",
#             "name": "say_hello",
#             "value": [
#                 "World"
#             ],
#             "time": "2024-09-25T18:32:32.607664"
#         },
#         {
#             "type": "ActivityCompleted",
#             "name": "say_hello",
#             "value": "Hello, World",
#             "time": "2024-09-25T18:32:32.607686"
#         },
#         {
#             "type": "WorkflowCompleted",
#             "name": "hello_world",
#             "value": [
#                 "Hello, Joe",
#                 "Hello, World"
#             ],
#             "time": "2024-09-25T18:32:32.607703"
#         }
#     ]
# }
