from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext
from flux.tasks import pipeline


@task
def multiply_by_two(x):
    return x * 2


@task
def add_three(x):
    return x + 3


@task
def square(x):
    return x * x


@workflow
def pipeline_workflow(ctx: WorkflowExecutionContext[int]):
    if not ctx.input:
        raise TypeError('Input not provided')
    result = yield pipeline([multiply_by_two, add_three, square], ctx.input)
    return result


if __name__ == '__main__':  # pragma: no cover
    ctx = pipeline_workflow.run(5)
    print(ctx.to_json())
