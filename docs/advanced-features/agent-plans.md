# Agent Plans

Agent Plans let an agent create a structured roadmap before starting complex work. When `planning=True`, the agent gets three tools — `create_plan`, `mark_step_done`, and `get_plan` — that it uses to organize multi-step tasks with named steps and dependency tracking.

Plans are guidance, not rigid execution. The agent decides when to plan, works through steps using its existing tools, skills, and sub-agents, and can replan at any time if circumstances change.

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

analyst = agent(
    "You are a market research analyst. Create a plan for complex research tasks.",
    model="openai/gpt-4o",
    tools=[search_web, write_report],
    planning=True,
)

@workflow
async def research(ctx: ExecutionContext):
    return await analyst(f"Research the competitive landscape for {ctx.input['product']}.")
```

The agent will:
1. Assess the task and decide to create a plan
2. Call `create_plan` with named steps and dependencies
3. Work through each step using `search_web`, `write_report`, etc.
4. Call `mark_step_done` after each step to record results
5. Return a final response when done

## How It Works

### The Three Tools

When `planning=True`, the agent receives:

| Tool | Purpose |
|------|---------|
| `create_plan(steps)` | Create or replace the plan. Each step has a `name`, `description`, and optional `depends_on` list. |
| `mark_step_done(step_name, result)` | Mark a step as completed and store its result for dependent steps. |
| `get_plan()` | Return the current plan with all statuses and results. |

### Plan Structure

```python
# The LLM calls create_plan with steps like:
create_plan(steps=[
    {"name": "research", "description": "Search for competitor pricing data."},
    {"name": "analyze", "description": "Analyze pricing trends.", "depends_on": ["research"]},
    {"name": "report", "description": "Write the final report.", "depends_on": ["analyze"]},
])
```

- **Steps are goals, not tool calls.** A step like "Research competitor pricing" may involve multiple tool calls.
- **Dependencies** declare which steps must complete first. The agent respects this ordering.
- **Named results** from completed steps are visible via `get_plan()`, so dependent steps can reference prior outputs.

### The LLM Stays in Control

The plan is guidance. The framework tracks state and stores results, but the LLM decides:
- **When to plan** — simple tasks don't need a plan
- **Which step to work on** — it picks the next step based on the plan
- **When to replan** — call `create_plan` again if circumstances change
- **When to abandon** — stop using plan tools and respond directly

### Status Reminder

After each tool call, the agent sees a lightweight one-line reminder:

```
[Plan: 2/5 steps completed. Ready: "analyze", "validate"]
```

This prevents the agent from losing track of the plan during long sequences of tool calls.

### Replanning

If results change the plan, the agent calls `create_plan` again with updated steps. Completed steps and their results are preserved automatically — only pending steps are replaced.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `planning` | `False` | Enable planning tools. |
| `max_tool_calls` | `10` | Plan tools count against this limit. Increase for planning agents (e.g., `30`). |

## Planning with Sub-Agents

Planning composes with sub-agents. A manager agent can create a plan and delegate steps to specialist agents:

```python
researcher = agent(
    "You are a research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Deep research using web sources.",
    tools=[search_web],
)

manager = agent(
    "You are a project manager. Plan complex tasks and delegate to your team.",
    model="openai/gpt-4o",
    agents=[researcher],
    planning=True,
)
```

The manager creates a plan where steps use the `delegate` tool to assign work to sub-agents.

## When to Use Planning

**Good fit:**
- Multi-step research or analysis tasks
- Workflows with data dependencies between steps
- Tasks that benefit from upfront organization

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

# Check status
plan.completed_steps()
plan.pending_steps()
plan.dependencies_satisfied(step)
plan.summary()
```
