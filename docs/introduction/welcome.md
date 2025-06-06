# Welcome to Flux

Welcome to **Flux**, a distributed workflow orchestration engine designed to make building stateful and fault-tolerant workflows in Python simple, intuitive, and powerful.

## What is Flux?

Flux is a Python-based workflow orchestration system that enables you to build complex, reliable applications by breaking them down into manageable tasks and workflows. Whether you're processing data pipelines, orchestrating microservices, automating business processes, or building machine learning workflows, Flux provides the tools and infrastructure you need to create robust, scalable solutions.

At its core, Flux transforms the way you think about building distributed applications by providing:

- **Intuitive Python Programming Model**: Define workflows using familiar Python async/await syntax with decorators
- **Built-in Reliability**: Automatic state persistence, error handling, and recovery mechanisms
- **Flexible Execution Models**: Run workflows locally during development or distribute them across multiple workers in production
- **Comprehensive State Management**: Full visibility and control over workflow execution with pause/resume capabilities

## Our Vision

We believe that building distributed applications shouldn't require mastering complex frameworks or sacrificing code clarity. Flux was created with the vision of making workflow orchestration as natural as writing regular Python code, while providing enterprise-grade reliability and scalability underneath.

Our goal is to enable developers to:
- **Focus on Business Logic**: Spend time solving domain problems, not infrastructure challenges
- **Build with Confidence**: Trust that your workflows will handle failures gracefully and maintain consistency
- **Scale Effortlessly**: Go from prototype to production without rewriting your core logic
- **Debug Effectively**: Understand exactly what happened during workflow execution with comprehensive state tracking

## Why Choose Flux?

### Developer Experience First
Flux prioritizes developer productivity with an intuitive API that feels natural to Python developers. Define workflows using decorators and async/await syntax you already know:

```python
from flux import task, workflow, ExecutionContext

@task
async def process_data(data: str) -> str:
    return data.upper()

@workflow
async def hello_world(ctx: ExecutionContext[str]):
    result = await process_data(ctx.input)
    return f"Hello, {result}!"

# Execute locally
result = hello_world.run("world")
print(result.output)  # "Hello, WORLD!"
```

### Production-Ready Reliability
Every workflow execution is automatically persisted, making your applications resilient to failures:

- **Automatic State Persistence**: Never lose progress due to crashes or restarts
- **Deterministic Replay**: Workflows produce consistent results when replayed
- **Comprehensive Error Handling**: Built-in retry policies, fallback mechanisms, and rollback support
- **Execution Visibility**: Full audit trail of every step in your workflows

### Flexible Architecture
Start simple and scale as needed:

- **Local Development**: Run workflows directly in your Python process for fast iteration
- **Distributed Production**: Deploy server/worker architecture for high availability and scalability
- **Hybrid Execution**: Mix local and distributed execution based on your needs

### Rich Workflow Patterns
Express complex logic with powerful built-in patterns:

- **Parallel Execution**: Run multiple tasks concurrently for better performance
- **Pipeline Processing**: Chain tasks together in sequential processing flows
- **Task Mapping**: Apply operations across collections of data
- **Subworkflows**: Compose large workflows from smaller, reusable components
- **Graph-based Workflows**: Define complex dependencies using directed acyclic graphs

## Getting Started

Ready to build your first workflow? Our [Quick Start guide](../getting-started/installation.md) will have you up and running in minutes. From there, explore our comprehensive [User Guide](../user-guide/task-definition.md) to learn about advanced features and patterns.

Whether you're building data pipelines, orchestrating APIs, automating business processes, or managing machine learning workflows, Flux provides the foundation you need to build reliable, scalable applications with confidence.

Welcome to the future of workflow orchestration in Python!
