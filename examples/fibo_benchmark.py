import json

import pandas as pd
import numpy as np

from flux import task, workflow
from flux.encoders import WorkflowContextEncoder
from flux.loaders import LocalFunctionWorkflowLoader
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner


def fibo(n: int):
    if n <= 1:
        return n
    else:
        return fibo(n - 1) + fibo(n - 2)


@task(name="sum_fibo_$iteration")
def sum_fibo(iteration: int, n: int):
    return fibo(n)


@workflow
def fibo_benchmark(ctx: WorkflowExecutionContext[tuple[int, int]]):
    iterations = ctx.input[0]
    n = ctx.input[1]
    for i in range(iterations):
        yield sum_fibo(i, n)


if __name__ == "__main__":
    runner = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    input = (10, 33) #(iterations, number)
    ctx = runner.run_workflow("fibo_benchmark", input)
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
