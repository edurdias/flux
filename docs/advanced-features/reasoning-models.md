# Reasoning Models

Flux supports reasoning/chain-of-thought models that show their thinking process
alongside tool calling. When enabled, the model's reasoning traces are captured,
passed through on subsequent turns, and stored in working memory.

## Quick Start

```python
from flux.tasks.ai import agent, system_tools
from flux.tasks.ai.memory import working_memory

assistant = await agent(
    "You are a helpful assistant.",
    model="ollama/qwen3",
    tools=system_tools(workspace="/path/to/project"),
    working_memory=working_memory(),
    reasoning_effort="high",  # "low", "medium", "high"
)

answer = await assistant("Analyze the codebase")
```

## How It Works

When `reasoning_effort` is set, the model produces a reasoning trace before
its response. Flux automatically:

1. **Captures** the reasoning from the model response
2. **Passes it back** on subsequent turns (required by Anthropic, OpenAI, Gemini)
3. **Stores it** in working memory as a `thinking` role for pause/resume persistence

## Provider Support

| Provider | Reasoning Field | Effort Mapping | Passback Required |
|---|---|---|---|
| Ollama | `message.thinking` | `think=True` for all levels | No |
| Anthropic | `thinking` content blocks | Adaptive thinking with effort level | Yes (with signature) |
| OpenAI | `message.reasoning_content` | `reasoning_effort` parameter | Yes (where available) |
| Gemini | `part.thought=True` parts | `thinking_budget` in tokens | Yes |

## Working Memory

Reasoning is stored as `thinking` role messages in working memory, positioned
before the associated assistant response or tool call. This preserves the
reasoning context across pause/resume cycles.

```
user       → "Find all Python files"
thinking   → {"text": "I should use find_files...", "opaque": {...}}
tool_call  → {"calls": [...]}
tool_result → {"call_id": "...", "output": "..."}
thinking   → {"text": "Now I need to read each...", "opaque": {...}}
assistant  → "I found 3 Python files..."
```

## Configuration

`reasoning_effort` accepts:

- `None` — reasoning disabled (default)
- `"low"` — minimal reasoning
- `"medium"` — balanced reasoning
- `"high"` — deep reasoning

The mapping to provider-specific settings is handled internally.
Non-reasoning models silently ignore the parameter.

## Supported Models

| Provider | Models |
|---|---|
| Ollama | Qwen3, DeepSeek-R1, QwQ, Gemma 3 |
| Anthropic | Claude Sonnet 4, Claude Opus 4 (adaptive thinking) |
| OpenAI | o1, o3, o4-mini (via compatible endpoints) |
| Gemini | Gemini 2.5 Flash, Gemini 2.5 Pro |
