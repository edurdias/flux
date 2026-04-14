# Worker Affinity

Worker affinity lets you target workflows to specific workers based on capability labels. Workers can declare labels (like `role=harness`, `browser=true`), and workflows can declare affinity requirements to route tasks to workers with matching labels.

## What is Worker Affinity

Worker affinity is a routing mechanism:

1. A **worker** declares immutable capability labels when it starts
2. A **workflow** declares affinity constraints using `@workflow.with_options`
3. When a workflow runs, the system only dispatches to workers whose labels match all affinity requirements

This is different from resource requests: labels describe *capability* (what kind of tools or environment), while resources describe *capacity* (CPU, memory, GPU).

## Starting a Worker with Labels

Labels are declared when a worker starts and cannot be changed without restarting. Start a worker with labels using the `--label` flag:

```bash
flux start worker --label role=harness --label env=sandbox --label browser=true
```

Multiple `--label` flags declare multiple labels. Label keys and values are strings.

View a worker's labels:

```bash
flux worker list
```

This shows each worker's labels in the output.

## Declaring Workflow Affinity

Use `affinity` in `@workflow.with_options` to specify required worker labels:

```python
from flux import workflow, ExecutionContext

@workflow.with_options(affinity={"role": "harness", "browser": "true"})
async def my_agent(ctx: ExecutionContext):
    # This workflow only runs on workers with both
    # role=harness AND browser=true
    return "Running on a harness worker with browser tools"
```

The `affinity` dict is a mapping of label keys to label values. All keys and values are strings.

## Matching Semantics

Affinity matching follows these rules:

- A worker matches affinity if it has **all** labels specified in the affinity dict
- Extra labels on the worker are ignored
- A worker with `role=harness, env=sandbox, browser=true` matches affinity `{"role": "harness", "browser": "true"}`
- A worker with `role=harness` does **not** match affinity `{"role": "harness", "browser": "true"}`
- No affinity constraint = any worker (no filtering)

If no worker matches the affinity requirements when a workflow is dispatched, the workflow cannot run and remains pending until a matching worker appears.

## Labels vs Resource Requests

Labels and resource requests are independent mechanisms:

| Aspect | Labels | Resources |
|--------|--------|-----------|
| **Purpose** | Capability (what kind) | Capacity (how much) |
| **Examples** | `role`, `env`, `browser`, `gpu_model` | CPU cores, memory, GPU count |
| **Matching** | All labels in affinity must match | All resources must be available |
| **Immutability** | Immutable; requires restart to change | Can be dynamic per task |

A dispatch checks both: the worker must match the affinity labels **and** have sufficient resources.

## Resume Behavior

When a paused workflow resumes:

1. The system **prefers** the original worker that executed it before
2. If the original worker is unavailable (offline, removed), the system falls back to any worker matching the affinity labels
3. Resume always respects affinity constraints — a worker without the required labels cannot pick up a resumed workflow

This ensures workflows can reconnect to the same worker context when possible, improving task continuity.

## Example: Pinning to a Sandbox Harness Worker

Here's a practical example: an AI agent that needs browser tools and a sandboxed environment.

First, start a specialized worker:

```bash
flux start worker --label role=harness --label env=sandbox --label browser=true
```

Then declare a workflow that targets it:

```python
from flux import workflow, ExecutionContext, task

@task
async def check_website(url: str) -> str:
    """Use browser tools to visit a URL."""
    # Browser tools available only on harness workers
    return f"Checked {url}"

@workflow.with_options(affinity={"role": "harness", "env": "sandbox", "browser": "true"})
async def ai_researcher(ctx: ExecutionContext[str]):
    url = ctx.input
    result = await check_website(url)
    return result

# This workflow will only dispatch to the worker started above
ctx = ai_researcher.run("https://example.com")
```

Without the affinity constraint, the workflow might run on a generic worker without browser tools and fail.
