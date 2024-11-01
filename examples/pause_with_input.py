from __future__ import annotations

from examples.parallel_tasks import say_hello
from flux import workflow
from flux.decorators import pause


@workflow
def pause_with_input_workflow():
    name = yield pause("name", wait_for_input=str)
    message = yield say_hello(name)
    return message


if __name__ == "__main__":  # pragma: no cover
    ctx = pause_with_input_workflow.run()
    print(ctx.to_json())

    pause_with_input_workflow.run(input="Joe", execution_id=ctx.execution_id)
    print(ctx.to_json())
