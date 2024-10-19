from flux import workflow
from flux.tasks import pause
from flux.context import WorkflowExecutionContext

from examples.hello_world_parallel import diga_ola, say_hello


@workflow
def pause_workflow(ctx: WorkflowExecutionContext[str]):
    yield pause("first_pause")
    hello = yield say_hello(ctx.input)
    yield pause("second_pause")
    ola = yield diga_ola(ctx.input)
    yield pause("third_pause")
    return [hello, ola]


if __name__ == "__main__":

    ctx = pause_workflow.run("Joe")

    while not ctx.finished:  # we could also check for ctx.paused
        ctx = pause_workflow.run(execution_id=ctx.execution_id)

    print(ctx.to_json())
