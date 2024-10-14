from flux import workflow, task, WorkflowExecutionContext


def fibo(n: int):
    if n <= 1:
        return n
    return fibo(n - 1) + fibo(n - 2)


@task.with_options(name="sum_fibo_$iteration")
def sum_fibo(iteration: int, n: int):
    print(f"Running iteration {iteration}")
    return fibo(n)


@workflow
def fibo_benchmark(ctx: WorkflowExecutionContext[tuple[int, int]]):
    iterations = ctx.input[0]
    n = ctx.input[1]
    yield sum_fibo.map([[i, n] for i in range(iterations)])


if __name__ == "__main__":
    ctx = fibo_benchmark.run((10, 33))
    print(ctx.to_json())
