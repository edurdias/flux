# Agent Plans

Agent Plans let an agent create a structured roadmap before starting complex work. When `planning=True`, the agent gets six tools — `create_plan`, `start_step`, `mark_step_done`, `mark_step_failed`, `get_plan`, and `get_ready_steps` — that it uses to organize multi-step tasks with named steps and dependency tracking.

Plans are guidance, not rigid execution. The agent decides when to plan, works through steps using its existing tools, skills, and sub-agents, and can replan at any time if circumstances change.

> **Note:** `agent()` is now async and must be awaited. Create agents inside a workflow or with `asyncio.run`.

## Quick Start

```python
from flux import task, workflow, ExecutionContext
from flux.tasks.ai import agent

@task
async def search_web(query: str) -> str:
    """Search the web for information."""
    return f"Results for: {query}"

@task
async def write_report(content: str) -> str:
    """Write a formatted report."""
    return f"Report: {content}"

@workflow
async def research(ctx: ExecutionContext):
    analyst = await agent(
        "You are a market research analyst. Create a plan for complex research tasks.",
        model="openai/gpt-4o",
        tools=[search_web, write_report],
        planning=True,
    )
    return await analyst(f"Research the competitive landscape for {ctx.input['product']}.")
```

The agent will:
1. Assess the task and decide to create a plan
2. Call `create_plan` with named steps and dependencies
3. Call `start_step` before working on each step
4. Work through each step using `search_web`, `write_report`, etc.
5. Call `mark_step_done` after each step to record results
6. Return a final response when done

## How It Works

### The Six Tools

When `planning=True`, the agent receives:

| Tool | Purpose |
|------|---------|
| `create_plan(steps)` | Create or replace the plan. Each step has a `name`, `description`, and optional `depends_on` list. |
| `start_step(step_name)` | Mark a step as in-progress before beginning work on it. |
| `mark_step_done(step_name, result)` | Mark a step as completed and store its result for dependent steps. |
| `mark_step_failed(step_name, reason)` | Mark a step as failed and store the reason. Failed steps block dependents. |
| `get_plan()` | Return the current plan with all statuses and results. |
| `get_ready_steps()` | Return steps whose dependencies are satisfied and can be started now, with dependency results included. |

### Step Lifecycle

Each step moves through a defined lifecycle:

```
pending → in_progress → completed
                      → failed
```

- **pending**: Created but not yet started.
- **in_progress**: The agent called `start_step`. Only one step can be in-progress at a time.
- **completed**: `mark_step_done` was called with a result. Results are visible to dependent steps.
- **failed**: `mark_step_failed` was called with a reason. Dependents cannot start until the plan is updated.

### Plan Structure

```python
# The LLM calls create_plan with a JSON string:
create_plan(steps='[{"name": "research", "description": "Search for competitor pricing data."}, {"name": "analyze", "description": "Analyze pricing trends.", "depends_on": ["research"]}, {"name": "report", "description": "Write the final report.", "depends_on": ["analyze"]}]')
```

- **Steps are goals, not tool calls.** A step like "Research competitor pricing" may involve multiple tool calls.
- **Dependencies** declare which steps must complete first. The agent respects this ordering.
- **Named results** from completed steps are visible via `get_plan()` and `get_ready_steps()`, so dependent steps can reference prior outputs.

### The LLM Stays in Control

The plan is guidance. The framework tracks state and stores results, but the LLM decides:
- **When to plan** — simple tasks don't need a plan
- **Which step to work on** — it picks the next step based on the plan
- **When to replan** — call `create_plan` again if circumstances change
- **When to abandon** — stop using plan tools and respond directly

### Status Reminder

After each tool call, the agent sees a lightweight one-line reminder:

```
[Plan: 2/5 done. Active: "analyze". Ready: "validate" (from research: ...).]
```

This prevents the agent from losing track of the plan during long sequences of tool calls. When a step has dependency results, they are included inline so the agent has context without calling `get_plan`.

### Plan Continuation

If the LLM stops responding mid-plan (returns no content and no tool calls while steps remain incomplete), the framework automatically nudges it to continue by injecting a continuation prompt with the current plan summary. This prevents models — especially smaller local ones — from silently abandoning incomplete plans.

The nudge is transparent: the agent receives a message like `"Continue working on your plan. [Plan: 1/3 done. Active: \"analyze\".]"` and resumes calling tools normally.

### Replanning

If results change the plan, the agent calls `create_plan` again with updated steps. Completed steps and their results are preserved automatically — only pending and in-progress steps are replaced.

If completed steps are dropped from the new plan, the agent receives a warning:

```json
{
  "warning": "Completed steps dropped (not in new plan): ['old-step']. Their results are lost."
}
```

## Configuration

```python
analyst = await agent(
    "You are a market research analyst.",
    model="openai/gpt-4o",
    tools=[search_web, write_report],
    planning=True,
    max_plan_steps=15,
    strict_dependencies=True,
    approve_plan=True,
    max_tool_calls=30,
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `planning` | `False` | Enable planning tools. |
| `max_plan_steps` | `20` | Maximum steps allowed in a plan. |
| `strict_dependencies` | `False` | When `True`, `start_step` returns an error if dependencies are not completed. When `False`, it warns but proceeds. |
| `approve_plan` | `False` | When `True`, `create_plan` pauses the workflow for human review before activating the plan. |
| `max_tool_calls` | `10` | Plan tools count against this limit. Increase for planning agents (e.g., `30`). |

### Dependency Enforcement

By default (`strict_dependencies=False`), starting a step with unmet dependencies produces a warning but succeeds:

```json
{
  "name": "analyze",
  "status": "in_progress",
  "warning": "Step 'analyze' has unsatisfied dependencies: ['research']. Proceeding anyway."
}
```

With `strict_dependencies=True`, the same call returns an error and the step stays pending:

```json
{
  "error": "Step 'analyze' has unsatisfied dependencies: ['research']. Complete them first."
}
```

## Plan Approval

When `approve_plan=True`, `create_plan` pauses the workflow and waits for a human to review the plan before it becomes active. Use Flux's resume mechanism to provide a decision.

```python
# Resume with the original plan (approve as-is)
workflow.resume(execution_id, plan_dict)

# Resume with a modified plan (change steps before activation)
workflow.resume(execution_id, {
    "steps": [
        {"name": "research", "description": "Revised research scope."},
        {"name": "report", "description": "Write the report.", "depends_on": ["research"]},
    ]
})

# Reject the plan entirely
workflow.resume(execution_id, {"rejected": True})
```

When rejected, `create_plan` returns `{"error": "Plan was rejected during review."}` and the agent must reconsider.

## LTM Persistence

When `long_term_memory` is provided alongside `planning=True`, plans are automatically saved after every state change and restored when the agent is next initialized. This allows plans to survive process restarts.

```python
from flux.tasks.ai.memory import LongTermMemory
from flux.tasks.ai.memory.providers import InMemoryProvider, SqlAlchemyProvider

# No LTM — plan exists only for the duration of the current agent() call (ephemeral)
analyst = await agent("...", model="openai/gpt-4o", planning=True)

# InMemoryProvider — plan survives multiple calls within the same process
mem = LongTermMemory(InMemoryProvider(), scope="analyst")
analyst = await agent("...", model="openai/gpt-4o", planning=True, long_term_memory=mem)

# SqlAlchemyProvider — plan persists across process restarts
engine_url = "sqlite:///agent_memory.db"
mem = LongTermMemory(SqlAlchemyProvider(engine_url), scope="analyst")
analyst = await agent("...", model="openai/gpt-4o", planning=True, long_term_memory=mem)
```

The three persistence tiers:

| Tier | Provider | Survives restart? |
|------|----------|-------------------|
| Ephemeral | _(none)_ | No — plan is lost when agent() returns |
| Process lifetime | `InMemoryProvider` | No — plan lives as long as the process |
| Persistent | `SqlAlchemyProvider` | Yes — plan is saved to a database |

## Planning with Sub-Agents

Planning composes with sub-agents. A manager agent can create a plan and delegate steps to specialist agents:

```python
@workflow
async def managed_research(ctx: ExecutionContext):
    researcher = await agent(
        "You are a research specialist.",
        model="ollama/llama3.2",
        name="researcher",
        description="Deep research using web sources.",
        tools=[search_web],
    )

    manager = await agent(
        "You are a project manager. Plan complex tasks and delegate to your team.",
        model="openai/gpt-4o",
        agents=[researcher],
        planning=True,
    )

    return await manager(ctx.input["task"])
```

The manager creates a plan where steps use the `delegate` tool to assign work to sub-agents.

## When to Use Planning

**Good fit:**
- Multi-step research or analysis tasks
- Workflows with data dependencies between steps
- Tasks that benefit from upfront organization
- Long-running tasks where failure recovery is important

**Not needed:**
- Simple question-answering
- Single tool call tasks
- Purely exploratory work where the next step is unpredictable

## Data Structures

For programmatic access (e.g., in tests):

```python
from flux.tasks.ai import AgentPlan, AgentStep

step = AgentStep(name="research", description="Research the topic.")
plan = AgentPlan(steps=[step])

# Query by status
plan.completed_steps()
plan.pending_steps()
plan.in_progress_steps()
plan.failed_steps()

# Check readiness
plan.ready_steps()                    # steps with satisfied dependencies
plan.dependencies_satisfied(step)     # True/False
plan.dependency_results(step)         # dict of dep_name -> result

# Active step
plan.active_step()                    # the in_progress step, or None

# Summary (shown in status reminder)
plan.summary()

# Serialization
plan.to_dict()
AgentPlan.from_dict(data)
```
