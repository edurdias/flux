# Sub-Agents

Sub-agents let a parent agent delegate work to specialized child agents. Pass `agents=[...]` to `agent()` and the framework injects agent descriptions into the system prompt, creates a `delegate` tool, and dispatches to the right agent at runtime. All delegation responses are wrapped in a `DelegationResult` with a uniform status envelope.

There are two types of sub-agents:

- **Local agents** — `agent()` instances that run in-process, sharing the same workflow execution
- **Workflow agents** — remote Flux workflows called via `FluxClient`, supporting pause/resume flows

## Quick Start

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
    description="Deep research using web sources.",
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

## How It Works

When `agents` is passed to `agent()`, three things happen at construction time:

1. **Validation** — each agent is checked for `name`, `description`, and callability. Names follow the same rules as skill names (lowercase, no consecutive hyphens, 1-64 chars).
2. **System prompt injection** — agent descriptions are appended to the system prompt so the LLM knows what agents are available and how delegation works.
3. **`delegate` tool creation** — a `@task` is created that dispatches to agents by name and returns a `DelegationResult`.

At runtime, the flow looks like:

```
1. System prompt includes:
   "Available agents:
    - researcher: Deep research using web sources.
    - reviewer: Code review with security and performance analysis."

2. User instruction: "Review PR #42"

3. LLM decides to delegate, calls:
   delegate(agent="researcher", instruction="Research context for PR #42")

4. Framework dispatches to the researcher agent, which runs its own
   LLM + tool loop (search_web, etc.)

5. DelegationResult returned:
   {"agent": "researcher", "status": "completed", "output": "..."}

6. LLM may delegate again or produce final answer
```

## Local Sub-Agents

Local sub-agents are regular `agent()` tasks. They run in the same process and workflow execution as the parent. Give each a `name` and `description`:

```python
researcher = agent(
    "You are a thorough research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Deep research using web sources. Delegate when "
    "gathering and synthesizing information from multiple sources.",
    tools=[search_web],
)
```

The `description` is critical — it tells the parent LLM *when* to delegate. Write it from the parent's perspective: "Delegate when X is needed."

## Workflow Agents

Workflow agents delegate to remote Flux workflows running on a server. Use `workflow_agent()` to create one:

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

Workflow agents use `FluxClient` to call the remote workflow synchronously (`run_workflow_sync` / `resume_execution_sync`). The target workflow must be registered on the server.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FLUX_SERVER_URL` | `http://localhost:8000` | Flux server URL (via `flux.config.Configuration`) |

## The `delegate` Tool

The `delegate` tool is a `@task` with the following parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent` | `str` | Name of the agent to delegate to |
| `instruction` | `str` | Natural language description of what to do |
| `input` | `str \| None` | Data the agent needs (JSON string or plain text) |
| `expected_output` | `str \| None` | Description of the desired response format |
| `execution_id` | `str \| None` | Resume a previously paused agent |

The tool always returns a dict with `agent`, `status`, `output`, and optionally `execution_id`.

## Delegation Results

Every delegation returns a `DelegationResult` with three possible statuses:

| Status | Meaning |
|--------|---------|
| `completed` | The sub-agent finished successfully |
| `paused` | The workflow paused (e.g. waiting for human approval) |
| `failed` | The sub-agent encountered an error |

```python
result.status        # "completed", "paused", or "failed"
result.agent         # name of the sub-agent
result.output        # the agent's response or error message
result.execution_id  # (optional) workflow execution ID for resume
```

### Error Handling

Delegation errors never propagate as exceptions. Instead, they are returned as `DelegationResult(status="failed")`:

- **Unknown agent name** — returns a failed result listing available agents
- **Agent raises an exception** — caught and returned as a failed result with the error message
- **`PauseRequested`** — caught and mapped to a paused result

This means the parent LLM always sees a structured response and can decide how to proceed (retry, try a different agent, report the error).

## Pause and Resume

Workflow agents support pause/resume flows. When a remote workflow pauses, the `DelegationResult` includes an `execution_id`. The parent LLM sees this and can resume by calling `delegate` again with the same agent name and `execution_id`:

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

The pause/resume semantics are explained in the system prompt preamble, so the LLM knows to pass `execution_id` when resuming.

Local agents can also trigger pause via Flux's `PauseRequested` mechanism. The delegate tool catches it and returns a paused `DelegationResult`.

## Recursive Sub-Agents

Agents can have their own sub-agents, forming a hierarchy:

```python
researcher = agent(
    "You are a research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Gathers information from web sources.",
    tools=[search_web],
)

analyst = agent(
    "You are a data analyst.",
    model="ollama/llama3.2",
    name="analyst",
    description="Analyzes data and produces reports.",
    agents=[researcher],
)

manager = agent(
    "You are a project manager.",
    model="openai/gpt-4o",
    agents=[analyst, reviewer],
)
```

Each agent only sees its direct sub-agents. The manager delegates to the analyst, who can further delegate to the researcher. This keeps each agent's context focused.

## Composing with Other Features

Sub-agents compose with all other `agent()` features:

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
    working_memory=working_memory(),
)
```

The order of feature injection is: skills, memory, then agents. All three inject into the system prompt and add tools.

## Event Tracking

Delegation appears in the Flux event log as nested task events:

```
TASK_STARTED    manager           {"instruction": "Review PR #42"}
TASK_STARTED    delegate          {"agent": "researcher", "instruction": "..."}
TASK_STARTED    researcher        {"instruction": "..."}
TASK_STARTED    search_web        {"query": "PR #42 context"}
TASK_COMPLETED  search_web        "Results: ..."
TASK_COMPLETED  researcher        "Research findings: ..."
TASK_COMPLETED  delegate          {"agent": "researcher", "status": "completed", ...}
TASK_COMPLETED  manager           "Final review: ..."
```

The `delegate` call is a regular `@task` — it gets full observability (events, OpenTelemetry spans) for free.

## Validation

### Agent Name Rules

Agent names follow the same rules as skill names:

- Lowercase letters, numbers, and hyphens only
- Must not start or end with a hyphen
- Must not contain consecutive hyphens (`--`)
- Maximum 64 characters

Invalid names raise `AgentValidationError` at construction time.

### Construction-Time Checks

`build_delegate()` validates all agents when the parent is constructed:

- Each agent must be callable
- Each agent must have a non-empty `name` attribute
- Each agent must have a non-empty `description` attribute
- Duplicate names raise `AgentValidationError`

```python
# Missing description
agent("...", model="ollama/llama3", agents=[broken_agent])
# → AgentValidationError: Sub-agent 'broken' must have a non-empty description attribute.

# Duplicate names
agent("...", model="ollama/llama3", agents=[agent_a, agent_a])
# → AgentValidationError: Duplicate agent name: 'researcher'
```

## API Reference

### `agent()` parameters for sub-agents

```python
agent(
    system_prompt: str,
    *,
    model: str,
    name: str | None = None,
    description: str | None = None,  # used when this agent is a sub-agent
    agents: list | None = None,      # sub-agents this agent can delegate to
    ...
) -> task
```

### `workflow_agent()`

```python
def workflow_agent(
    name: str,           # agent name (validated)
    description: str,    # description for the parent's system prompt
    workflow: str,       # name of the remote Flux workflow
) -> task
```

### `DelegationResult`

```python
@dataclass
class DelegationResult:
    agent: str
    status: Literal["completed", "paused", "failed"]
    output: Any
    execution_id: str | None = None

    def to_dict(self) -> dict: ...
```

### Error Classes

| Class | Base | When |
|-------|------|------|
| `AgentValidationError` | `ValueError` | Invalid agent name, missing attributes, duplicate names |
| `AgentNotFoundError` | `ExecutionError` | Reserved for infrastructure use |

## Examples

See `examples/ai/` for runnable examples:

- `sub_agents_local.py` — two local sub-agents coordinated by a parent
- `sub_agents_workflow.py` — workflow agent with remote delegation
- `sub_agents_mixed.py` — local agents + workflow agents on the same parent
- `deploy_pipeline.py` — target workflow for the workflow agent example
