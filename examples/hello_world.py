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

if __name__ == "__main__":
    runtime = WorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runtime.run("hello_world", "Joe")
    print(json.dumps(ctx, cls=WorkflowContextEncoder))

# # Sample Output:
# {
#     "name": "hello_world",
#     "execution_id": "c96dbbe0d6634e24a4d8de54b6fb1033",
#     "input": "Joe",
#     "output": [
#         "Hello, Joe",
#         "Hello, World"
#     ],
#     "event_history": [
#         {
#             "type": "WorkflowStarted",
#             "id": "hello_world_c96dbbe0d6634e24a4d8de54b6fb1033",
#             "name": "hello_world",
#             "value": "Joe",
#             "time": "2024-09-25T20:58:51.633345"
#         },
#         {
#             "type": "ActivityStarted",
#             "id": "say_hello_4710278503638013097",
#             "name": "say_hello",
#             "value": [
#                 "Joe"
#             ],
#             "time": "2024-09-25T20:58:51.633384"
#         },
#         {
#             "type": "ActivityCompleted",
#             "id": "say_hello_4710278503638013097",
#             "name": "say_hello",
#             "value": "Hello, Joe",
#             "time": "2024-09-25T20:58:51.633866"
#         },
#         {
#             "type": "ActivityStarted",
#             "id": "say_hello_3532541308635347825",
#             "name": "say_hello",
#             "value": [
#                 "World"
#             ],
#             "time": "2024-09-25T20:58:51.633892"
#         },
#         {
#             "type": "ActivityCompleted",
#             "id": "say_hello_3532541308635347825",
#             "name": "say_hello",
#             "value": "Hello, World",
#             "time": "2024-09-25T20:58:51.633939"
#         },
#         {
#             "type": "WorkflowCompleted",
#             "id": "hello_world_c96dbbe0d6634e24a4d8de54b6fb1033",
#             "name": "hello_world",
#             "value": [
#                 "Hello, Joe",
#                 "Hello, World"
#             ],
#             "time": "2024-09-25T20:58:51.633959"
#         }
#     ]
# }
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
