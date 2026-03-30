# Parallel Tool Execution

When an LLM emits multiple tool calls in a single response, Flux executes them
concurrently. This speeds up workflows where tools are independent — parallel
web searches, concurrent file reads, simultaneous agent delegations.

## How It Works

Most LLM providers support multiple tool calls per response. When the LLM decides
to call 3 tools at once, Flux runs all 3 concurrently via `asyncio.gather` instead
of waiting for each to finish before starting the next.

This is automatic — no configuration needed. Any agent with tools benefits.

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

### Parallel agent delegations

Combined with sub-agents, the supervisor can delegate to multiple agents at once:

```python
researcher = await agent("You research topics.", model="ollama/llama3.2", name="researcher")
reviewer = await agent("You review content.", model="ollama/llama3.2", name="reviewer")

supervisor = await agent(
    "You coordinate research and review.",
    model="anthropic/claude-sonnet-4-20250514",
    agents=[researcher, reviewer],
)
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
