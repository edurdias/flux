# Flux
Flux is a distributed workflow orchestration engine to build stateful and fault-tolerant workflows.

## Getting started

1. Install the latest version of Flux

```sh
pip install flux-core
```

> Flux requires Python 3.12 or later.

2. Create your first Workflow

```python
from flux import task, workflow, WorkflowExecutionContext

@task
def say_hello(name: str):
    return f"Hello, {name}"

@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))
if __name__ == "__main__":
    ctx = hello_world.run("Joe")
    print(ctx.to_json())
```

> For more examples, checkout the `examples` folder.

3. Execute the workflow locally

```sh
flux exec hello_world.py hello_world "Joe"
```

4. If you prefer via API

```sh
flux start examples

curl --location 'localhost:8000/hello_world' \
--header 'Content-Type: application/json' \
--data '"Joe"'
```

## Features

- High-Performance
- Fault-Tolerance
- Durable Execution
- Parallelism

## Next Steps

- Checkout the tutoarials and examples
- Learn how to configure and deploy Flux on different environments



