# AI Agents

`agent()` creates a Flux `@task` that calls an LLM. Like any other task, it can be awaited inside a workflow, retried, observed, and composed with other tasks. It handles the agentic loop — tool calling, streaming, structured output, and memory — so you can focus on the prompt and the tools.

## Quick Start

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent

@workflow
async def my_workflow(ctx: ExecutionContext):
    assistant = await agent(
        "You are a helpful assistant.",
        model="ollama/llama3.2",
    )
    return await assistant("What is the capital of France?")
```

`agent()` is an async function that returns a Flux `@task`. Call it with an instruction string and, optionally, a `context` string for additional background.

## Supported Providers

| Provider | Model Format | Required Env Var | SDK Package |
|----------|-------------|-----------------|-------------|
| Ollama | `ollama/llama3` | (none, local) | `ollama` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` | `openai` |
| Anthropic | `anthropic/claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` | `anthropic` |
| Google Gemini | `google/gemini-2.5-flash` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `google-genai` |

Install all AI provider SDKs at once:

```bash
pip install flux-core[ai]
```

Or install only the provider you need:

```bash
pip install ollama           # for Ollama models
pip install openai           # for OpenAI models
pip install anthropic        # for Anthropic models
pip install google-genai     # for Google Gemini models
```

## Quick Start by Provider

### Ollama

Ollama runs locally. No API key required. Start the Ollama server before running your workflow.

```python
assistant = await agent(
    "You are a helpful assistant.",
    model="ollama/llama3.2",
)
```

### OpenAI

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

assistant = await agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
)
```

### Anthropic

```python
import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

assistant = await agent(
    "You are a helpful assistant.",
    model="anthropic/claude-sonnet-4-20250514",
)
```

### Google Gemini

```python
import os
os.environ["GOOGLE_API_KEY"] = "AIza..."  # or GEMINI_API_KEY

assistant = await agent(
    "You are a helpful assistant.",
    model="google/gemini-2.5-flash",
)
```

## Parameters

```python
async def agent(
    system_prompt: str,
    *,
    model: str,
    name: str | None = None,
    tools: list[task] | None = None,
    skills: SkillCatalog | None = None,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    max_tool_calls: int = 10,
    max_tokens: int = 4096,
    stream: bool = True,
) -> task:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `system_prompt` | `str` | required | The system prompt defining the agent's identity and behavior. |
| `model` | `str` | required | Provider and model in `"provider/model_name"` format. |
| `name` | `str \| None` | `None` | Task name used in events and traces. Defaults to `"agent_{provider}_{model}"`. |
| `tools` | `list[task] \| None` | `None` | Flux `@task` functions the agent can call as tools. |
| `skills` | `SkillCatalog \| None` | `None` | Skill catalog providing reusable instruction bundles the LLM can activate. |
| `response_format` | `type[BaseModel] \| None` | `None` | Pydantic model class for structured JSON output. Disables streaming. |
| `working_memory` | `WorkingMemory \| None` | `None` | Conversation history across invocations within the same workflow execution. |
| `long_term_memory` | `LongTermMemory \| None` | `None` | Persistent fact storage across workflow executions. |
| `max_tool_calls` | `int` | `10` | Maximum tool call iterations before forcing a final answer. |
| `max_tokens` | `int` | `4096` | Maximum tokens in the LLM response. Used by Anthropic and Google; ignored by Ollama and OpenAI. |
| `stream` | `bool` | `True` | Enable streaming responses. Automatically disabled when `response_format` is set. |

The returned task has the signature:

```python
async def agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
```

- `instruction` — the user message sent to the LLM.
- `context` — optional background information prepended to the instruction.

## Tool Calling

Pass any Flux `@task` function via the `tools` parameter. The agent calls them autonomously during the agentic loop.

```python
from flux import task, workflow, ExecutionContext
from flux.tasks.ai import agent

@task
async def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny, 22°C in {city}"

@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results."""
    ...

@workflow
async def my_workflow(ctx: ExecutionContext):
    assistant = await agent(
        "You are a helpful assistant with access to real-time data.",
        model="openai/gpt-4o",
        tools=[get_weather, search_web],
    )
    return await assistant("What's the weather like in Tokyo?")
```

Each tool call is recorded as a Flux task event, so tool invocations are fully observable, retryable, and replay-safe.

### Tool Call Limit

The `max_tool_calls` parameter (default `10`) caps the number of tool call iterations per agent invocation. When the limit is reached, the agent is forced to produce a final answer with whatever it has gathered.

```python
assistant = await agent(
    "You are a research assistant.",
    model="openai/gpt-4o",
    tools=[search_web],
    max_tool_calls=20,
)
```

## Structured Output

Pass a Pydantic model class to `response_format` to get typed, structured output instead of a plain string.

```python
from pydantic import BaseModel
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent

class Sentiment(BaseModel):
    label: str        # "positive", "negative", or "neutral"
    confidence: float # 0.0 to 1.0
    reason: str

@workflow
async def analyze(ctx: ExecutionContext):
    classifier = await agent(
        "You are a sentiment analysis assistant. Respond only with structured JSON.",
        model="openai/gpt-4o",
        response_format=Sentiment,
    )
    result: Sentiment = await classifier(ctx.input["text"])
    return {"label": result.label, "confidence": result.confidence}
```

When `response_format` is set:
- The return type changes from `str` to the Pydantic model instance.
- Streaming is automatically disabled (`stream=True` is ignored).

## Streaming

By default, `stream=True` and the agent emits progress events as the LLM produces tokens. These are surfaced as Flux task progress events and can be consumed by the workflow engine for real-time feedback.

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent

@workflow
async def my_workflow(ctx: ExecutionContext):
    assistant = await agent(
        "You are a helpful assistant.",
        model="anthropic/claude-sonnet-4-20250514",
        stream=True,  # default, can be omitted
    )
    return await assistant("Explain the theory of relativity.")
```

To disable streaming:

```python
assistant = await agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
    stream=False,
)
```

Streaming is automatically disabled when `response_format` is provided, regardless of the `stream` setting.

See [Task Progress](task-progress.md) for how to consume progress events from workflows.

## Memory

### Working Memory

Pass `working_memory()` to maintain conversation history across multiple agent calls within the same workflow execution:

```python
from flux.tasks.ai.memory import working_memory

@workflow
async def conversation(ctx: ExecutionContext):
    chatbot = await agent(
        "You are a helpful assistant.",
        model="openai/gpt-4o",
        working_memory=working_memory(),
    )
    r1 = await chatbot("What is Python?")
    r2 = await chatbot("What about asyncio?")  # aware of the Python context
    return r2
```

### Long-term Memory

Pass `long_term_memory()` to persist facts across workflow executions:

```python
from flux.tasks.ai.memory import long_term_memory, sqlite

assistant = await agent(
    "You are a personal assistant. Remember important facts about the user.",
    model="openai/gpt-4o",
    long_term_memory=long_term_memory(
        provider=sqlite("memory.db"),
        scope="user:123",
    ),
)
```

See [AI Memory](ai-memory.md) for the full memory reference including windowed memory, pause/resume behavior, PostgreSQL and custom providers, and shared memory across multiple agents.

## Skills

Pass a `SkillCatalog` to give the agent reusable instruction bundles it can discover and activate on demand:

```python
from flux.tasks.ai import agent, SkillCatalog

catalog = SkillCatalog.from_directory("./skills")

assistant = await agent(
    "You are a research assistant.",
    model="openai/gpt-4o",
    tools=[search_web],
    skills=catalog,
)
```

See [Agent Skills](agent-skills.md) for the full skills reference including `SKILL.md` authoring, Python-defined skills, and multi-skill stacking.

## Task Options

Because `agent()` returns a Flux `@task`, you can chain `.with_options()` to configure retry, timeout, and other task-level settings:

```python
assistant = (await agent(
    "You are a helpful assistant.",
    model="openai/gpt-4o",
    tools=[search_web],
)).with_options(
    retry_max_attempts=3,
    timeout=120,
)
```

## `agent()` Reference

```python
from flux.tasks.ai import agent

await agent(
    system_prompt: str,
    *,
    model: str,
    name: str | None = None,
    tools: list[task] | None = None,
    skills: SkillCatalog | None = None,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    max_tool_calls: int = 10,
    max_tokens: int = 4096,
    stream: bool = True,
) -> task
```

## Reasoning Models

For models that support chain-of-thought reasoning (Qwen3, DeepSeek-R1,
Claude extended thinking, OpenAI o-series, Gemini thinking), see
[Reasoning Models](reasoning-models.md).
