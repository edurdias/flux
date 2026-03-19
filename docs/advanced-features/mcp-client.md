# MCP Client

The `mcp()` primitive connects workflows to external [Model Context Protocol](https://modelcontextprotocol.io) servers. Each discovered MCP tool becomes a Flux `@task` with retry, timeout, caching, event tracking, and pause/resume support.

## Basic Usage

```python
from flux import workflow, ExecutionContext
from flux.tasks.mcp import mcp

@workflow
async def my_workflow(ctx: ExecutionContext):
    async with mcp("http://localhost:8080/mcp", name="server") as client:
        tools = await client.discover()
        result = await tools.list_workflows()
        return result
```

`mcp()` returns a lazy async context manager:

- `__aenter__` stores config but does **not** connect
- `discover()` connects lazily and returns a `ToolSet`
- `__aexit__` closes any open connection

No connection is made during event replay, which aligns with Flux's event-sourcing model.

## Tool Discovery

`discover()` is a Flux `@task` that connects to the MCP server, lists available tools, and returns a `ToolSet`:

```python
async with mcp("http://localhost:8080/mcp", name="flux") as client:
    tools = await client.discover()

    # Attribute access
    result = await tools.get_weather(city="London")

    # Iteration
    for tool in tools:
        print(tool.name)

    # Length
    print(f"Found {len(tools)} tools")
```

Tool schemas are serialized into the Flux event log. On workflow resume, `discover()` replays from events without reconnecting.

### Rediscovery

If the MCP server's tool list may have changed (e.g., after a long pause), call `rediscover()`:

```python
tools = await client.rediscover()
```

Each `rediscover()` call is a separate Flux task with a deterministic name for replay safety.

## Task Options

### Global Defaults

Pass default task options at the client level. All discovered tools inherit them:

```python
async with mcp(
    "http://localhost:8080/mcp",
    name="server",
    retry_max_attempts=3,
    retry_delay=1,
    timeout=30,
) as client:
    tools = await client.discover()
    # All tools get retry=3, timeout=30
```

### Per-Tool Overrides

Override options on individual tools using `with_options()`:

```python
tools = await client.discover()

# This tool needs more time
long_running = tools.execute_workflow_sync.with_options(timeout=120)
result = await long_running(workflow_name="heavy_job", input_data="{}")
```

## Connection Modes

### Session-Scoped (default)

One connection shared across all tool calls within the `async with` block:

```python
async with mcp("url", connection="session") as client:
    tools = await client.discover()
    await tools.tool_a()  # reuses connection
    await tools.tool_b()  # reuses connection
```

### Per-Call

Each tool call opens and closes its own connection:

```python
async with mcp("url", connection="per-call") as client:
    tools = await client.discover()
    await tools.tool_a()  # opens, calls, closes
    await tools.tool_b()  # opens, calls, closes
```

Use per-call mode for long-lived workflows with infrequent MCP calls.

## Authentication

### Bearer Token

```python
from flux.tasks.mcp import mcp, bearer

# Static token
async with mcp("url", auth=bearer("my-token")) as client: ...

# From Flux secret store (resolved at connection time)
async with mcp("url", auth=bearer(secret="MCP_API_KEY")) as client: ...

# From a callable (sync or async)
async with mcp("url", auth=bearer(provider=get_fresh_token)) as client: ...
```

Tokens are resolved at connection time, not at `mcp()` creation time. After a pause lasting hours, the lazy reconnect fetches a fresh token.

### OAuth 2.1

```python
from flux.tasks.mcp import mcp, oauth

async with mcp(
    "https://api.example.com/mcp",
    auth=oauth(scopes=["read", "write"], client_name="My App"),
) as client:
    tools = await client.discover()
```

OAuth is delegated to FastMCP, which handles server discovery, PKCE, token exchange, and automatic refresh.

## Agent Integration

MCP tools work directly with the `agent()` primitive:

```python
from flux.tasks.ai import agent
from flux.tasks.mcp import mcp

async with mcp("http://localhost:8080/mcp", name="flux") as client:
    tools = await client.discover()

    # Pass all tools
    assistant = agent(
        "You are a helpful assistant.",
        model="ollama/llama3.2",
        tools=list(tools),
    )

    # Or a subset
    assistant = agent(
        "You are a workflow manager.",
        model="ollama/llama3.2",
        tools=[tools.list_workflows, tools.get_workflow_details],
    )

    response = await assistant("What workflows are available?")
```

The agent inspects each tool's signature and docstring to build LLM tool schemas automatically.

## Multi-Server

Use one `mcp()` per server. The workflow handles orchestration:

```python
async with mcp("http://server-a:8080/mcp", name="a") as a:
    async with mcp("http://server-b:8081/mcp", name="b") as b:
        a_tools = await a.discover()
        b_tools = await b.discover()

        result_a = await a_tools.some_tool()
        result_b = await b_tools.other_tool()
```

Tool names are prefixed with the server name (`mcp_a_some_tool`, `mcp_b_other_tool`) to avoid collisions in the event log.

## Pause/Resume

MCP tools work naturally with Flux's pause/resume:

```python
from flux.tasks import pause

async with mcp("http://localhost:8080/mcp", name="flux") as client:
    tools = await client.discover()

    available = await tools.list_workflows()
    user_input = await pause("choose_workflow", output=available)

    # After resume: discover() and list_workflows() replay from events
    # Only this call actually connects to the MCP server
    result = await tools.execute_workflow_sync(
        workflow_name=user_input["workflow_name"],
        input_data="{}",
    )
```

On resume, the workflow re-runs from the top. Completed tasks replay from events — no MCP connection is needed. The first new tool call triggers a lazy reconnect.

## Event Tracking

All MCP operations appear in the Flux event log:

```
TASK_STARTED    mcp_flux_discover        {}
TASK_COMPLETED  mcp_flux_discover        {schemas: [...]}
TASK_STARTED    mcp_flux_list_workflows  {}
TASK_COMPLETED  mcp_flux_list_workflows  {success: true, workflows: [...]}
```

When OpenTelemetry is enabled, MCP tool spans include `mcp.server.url`, `mcp.tool.name`, and `mcp.connection.mode` attributes.

## Error Handling

MCP tool errors are raised as `ToolExecutionError`, which extends Flux's `ExecutionError`. Retry, fallback, and rollback apply automatically:

```python
from flux.tasks.mcp import mcp

async with mcp("url", retry_max_attempts=3, timeout=30) as client:
    tools = await client.discover()
    # If the MCP server returns an error or the connection drops,
    # Flux retries the tool call up to 3 times
    result = await tools.some_tool(arg="value")
```

Connection errors (timeouts, connectivity) discard the stale connection. On retry, a fresh connection is established with re-resolved auth.

## `mcp()` Reference

```python
def mcp(
    server: str | FastMCP,           # URL or FastMCP instance (for testing)
    *,
    auth=None,                       # bearer(...) or oauth(...)
    name: str | None = None,         # server name (defaults to hostname)
    connection: str = "session",     # "session" or "per-call"
    connect_timeout: int = 10,       # MCP handshake timeout (seconds)
    retry_max_attempts: int = 0,     # default retries for all tools
    retry_delay: int = 1,            # initial retry delay (seconds)
    retry_backoff: int = 2,          # retry backoff multiplier
    timeout: int = 0,                # default task timeout for all tools
    cache: bool = False,             # enable result caching
) -> MCPClient:
```

## Testing

Use FastMCP's in-memory transport for tests:

```python
from fastmcp import FastMCP
from flux.tasks.mcp import mcp

server = FastMCP("test")

@server.tool()
def get_weather(city: str) -> str:
    return f"Sunny in {city}"

async with mcp(server, name="test") as client:
    tools = await client.discover()
    result = await tools.get_weather(city="London")
    assert "London" in result
```
