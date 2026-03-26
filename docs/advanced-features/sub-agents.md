# Sub-Agents

Sub-agents let a parent agent delegate work to specialized child agents. Each sub-agent is a regular Flux `agent()` task with a `name` and `description` — the parent sees these descriptions in its system prompt and dispatches via a `delegate` tool.

## Local Sub-Agents

Local sub-agents are `agent()` instances that run in-process. Give each agent a `name` and `description` so the parent knows when to delegate.

```python
from flux import task, workflow, ExecutionContext
from flux.tasks.ai import agent

@task
async def search_web(query: str) -> str:
    """Search the web for information."""
    ...

researcher = agent(
    "You are a thorough research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Deep research using web sources. Delegate when "
    "gathering and synthesizing information from multiple sources.",
    tools=[search_web],
)

reviewer = agent(
    "You are a code review expert.",
    model="openai/gpt-4o",
    name="reviewer",
    description="Code review with security and performance analysis.",
)

manager = agent(
    "You are a senior engineering manager. Coordinate your team.",
    model="openai/gpt-4o",
    agents=[researcher, reviewer],
)

@workflow
async def review_workflow(ctx: ExecutionContext):
    return await manager(f"Review PR #{ctx.input['pr_number']}")
```

The parent agent receives a `delegate` tool. When the LLM calls `delegate(agent="researcher", instruction="...")`, the framework dispatches to the matching sub-agent and returns a `DelegationResult`.

## Workflow Agents

Workflow agents delegate to remote Flux workflows running on a server. Use `workflow_agent()` to create one.

```python
from flux.tasks.ai import agent, workflow_agent

deployer = workflow_agent(
    name="deployer",
    description="Handles deployment pipelines. May pause for approval.",
    workflow="deploy_pipeline",
)

manager = agent(
    "You are an engineering manager.",
    model="openai/gpt-4o",
    agents=[deployer],
)
```

Workflow agents use `FluxClient` to call the remote workflow synchronously. Configure the server connection with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FLUX_SERVER_URL` | `http://localhost:8000` | Flux server URL |

## Delegation Results

Every delegation returns a `DelegationResult` with three possible statuses:

| Status | Meaning |
|--------|---------|
| `completed` | The sub-agent finished successfully |
| `paused` | The workflow paused (e.g. waiting for human approval) |
| `failed` | The sub-agent encountered an error |

```python
# DelegationResult fields
result.status        # "completed", "paused", or "failed"
result.agent         # name of the sub-agent
result.output        # the agent's response or error message
result.execution_id  # (optional) workflow execution ID for resume
```

## Pause and Resume

Workflow agents support pause/resume flows. When a remote workflow pauses, the `DelegationResult` includes an `execution_id`. The parent agent can resume by calling `delegate` again with that `execution_id`.

```python
deployer = workflow_agent(
    name="deployer",
    description="Deploys services. May pause for human approval.",
    workflow="deploy_pipeline",
)

manager = agent(
    "You are a release manager. When a deployment pauses for approval, "
    "review the details and resume with your decision.",
    model="openai/gpt-4o",
    agents=[deployer],
)
```

The LLM sees the paused status and `execution_id` in the delegation result, and can choose to resume the workflow by passing the `execution_id` back to the `delegate` tool.

## Composing with Skills and Memory

Sub-agents compose with all other `agent()` features — tools, skills, memory, and structured output.

```python
from flux.tasks.ai import agent, workflow_agent, SkillCatalog

catalog = SkillCatalog.from_directory("./skills")

researcher = agent(
    "You are a research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Research agent with web access.",
    tools=[search_web],
    skills=catalog,
)

deployer = workflow_agent(
    name="deployer",
    description="Deployment pipeline agent.",
    workflow="deploy_pipeline",
)

manager = agent(
    "You are a senior engineering manager.",
    model="openai/gpt-4o",
    agents=[researcher, deployer],
    skills=catalog,
)
```

## Recursive Sub-Agents

Agents can have their own sub-agents, forming a hierarchy.

```python
analyst = agent(
    "You are a data analyst.",
    model="ollama/llama3.2",
    name="analyst",
    description="Analyzes data and produces reports.",
    agents=[researcher],  # analyst can delegate to researcher
)

manager = agent(
    "You are a project manager.",
    model="openai/gpt-4o",
    agents=[analyst, reviewer],  # manager delegates to analyst and reviewer
)
```

Each agent only sees its direct sub-agents. The manager delegates to the analyst, who can further delegate to the researcher.
