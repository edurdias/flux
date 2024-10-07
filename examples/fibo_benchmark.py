from flux import task, workflow
from flux.context import WorkflowExecutionContext


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
    input = (10, 33)  # (iterations, number)
    ctx = fibo_benchmark.run(input)
    print(ctx.to_json())
