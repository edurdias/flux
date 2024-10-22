from flux import workflow, task, WorkflowExecutionContext


def fibo(n: int):
    if n <= 1:
        return n
    return fibo(n - 1) + fibo(n - 2)


@task.with_options(name="sum_fibo_{iteration}")
def sum_fibo(iteration: int, n: int):
    print(f"Running iteration {iteration}")
    return fibo(n)


@workflow
def fibo_benchmark(ctx: WorkflowExecutionContext[tuple[int, int]]):
    iterations = ctx.input[0]
    n = ctx.input[1]
    results = {}
    for i in range(iterations):
        result = yield sum_fibo(i, n)
        results.update({f"Iteration #{i}": result})
    return results


if __name__ == "__main__":  # pragma: no cover
    ctx = fibo_benchmark.run((10, 33))
    print(ctx.to_json())
