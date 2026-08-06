# Parallel Tool Execution

When an LLM emits multiple tool calls in a single response, Flux executes them
concurrently. This speeds up workflows where tools are independent — parallel
web searches, concurrent file reads, simultaneous agent delegations.

## How It Works

Most LLM providers support multiple tool calls per response. When the LLM decides
to call 3 tools at once, Flux runs all 3 concurrently instead of waiting for each
to finish before starting the next.

This is automatic — no configuration needed. Any agent with tools benefits.

## The Ordering Contract

Concurrency is safe for reads and dangerous for effects: a turn that emits
`write_file(...)` and `wc -l` on the same file must not race them, or the
wrong result gets checkpointed and then **replays deterministically forever**.
Flux partitions each turn by declared risk:

- Tools declaring `risk="read"` — or declaring nothing and not
  approval-gated — run **concurrently** with their neighbors.
- Every other call is a **barrier**: it runs alone, in the order the model
  emitted it, after everything before it has settled.

```python
@task.with_options(risk="read")
async def read_doc(path: str) -> str: ...

@task.with_options(risk="write")
async def write_file(path: str, content: str) -> str: ...
```

`risk` accepts `"read"`, `"write"`, `"exec"`, and `"external"` — only
`"read"` is concurrent-eligible; the finer non-read levels document what
kind of effect the tool has.

Defaults are compatible: an unannotated tool keeps the legacy concurrent
behavior, **unless** it declares `requires_approval` — a tool the author
gates behind a human is consequential by definition, so it is treated as a
barrier without any annotation.

On models whose [capability record](ai-agents.md#model-capabilities) reports
`parallel_tool_calls=False` (most local Ollama models — many accept the
parallel-call wire format but mis-execute it), every call runs as a barrier
in emission order regardless of declarations.

## Limiting Concurrency

For resource-sensitive tools (shell commands, API calls with rate limits), cap the
number of concurrent executions:

```python
assistant = await agent(
    "You are a research assistant.",
    model="anthropic/claude-sonnet-4-20250514",
    tools=[search_web, read_doc, summarize],
    max_concurrent_tools=3,  # At most 3 tools running at once
)
```

### Values

- `None` (default): unlimited concurrency
- `int`: maximum concurrent tool executions via semaphore
- `1`: sequential execution (same as pre-0.17.0 behavior)

`max_concurrent_tools` bounds *how many* tools run at once; it guarantees
nothing about *order*. Ordering comes from the risk partition above.

## Result Ordering

Results are always returned in the same order as the tool calls, regardless of
which tool finishes first. Each result carries a `tool_call_id` matching the
original request, so the LLM always knows which result belongs to which call.

## Error Handling

If one tool fails, the others still complete. Each tool's error is captured
independently and returned to the LLM as an error message. No tool failure
blocks another tool's execution.

## Examples

### Parallel web searches

The LLM naturally emits multiple search calls when asked a comparative question:

```python
tools = [search_web, analyze_data]
assistant = await agent(
    "You are a research analyst.",
    model="openai/gpt-4o",
    tools=tools,
)
# LLM may emit: search_web("topic A"), search_web("topic B") in one turn
# Both run concurrently
await assistant("Compare approaches to distributed consensus")
```

### Parallel tool calls in practice

When an agent has multiple independent tools, the LLM can call several at once:

```python
assistant = await agent(
    "You are a data analyst.",
    model="anthropic/claude-sonnet-4-20250514",
    tools=[query_users_db, query_orders_db, query_inventory_db],
)
# LLM may emit all 3 queries in one turn — they run concurrently
await assistant("Get a full business snapshot for Q1 2026")
```

### Rate-limited API tools

```python
@task
async def call_api(endpoint: str) -> str:
    """Call an external API."""
    # ...

assistant = await agent(
    "You query multiple APIs.",
    model="openai/gpt-4o",
    tools=[call_api],
    max_concurrent_tools=2,  # Respect API rate limits
)
```
