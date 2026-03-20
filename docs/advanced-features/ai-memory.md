# AI Memory

Memory primitives give agents durable, replay-safe conversation history and persistent fact storage. `working_memory` keeps message history within a workflow execution; `long_term_memory` stores facts across executions using a pluggable provider.

## Working Memory

Pass `working_memory()` to `agent()` to enable conversation history across multiple invocations within the same workflow execution:

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import working_memory

chatbot = agent(
    system_prompt="You are a friendly assistant.",
    model="ollama/llama3.2",
    working_memory=working_memory(),
)

@workflow
async def conversation(ctx: ExecutionContext):
    r1 = await chatbot("What is Python?")
    r2 = await chatbot("What about asyncio?")  # knows about the Python context
    return r2
```

Each message is stored as a Flux task event. This means working memory is:

- **Durable** — stored in the event log, not in process memory
- **Replay-safe** — on workflow resume, messages are replayed from events without re-sending LLM requests
- **Zero external dependencies** — no database required

### Windowed Memory

Limit the number of messages passed to the LLM to control context size:

```python
chatbot = agent(
    system_prompt="You are a helpful assistant.",
    model="openai/gpt-4o",
    working_memory=working_memory(window=20),
)
```

With `window=20`, only the 20 most recent messages are included in each LLM call. Earlier messages remain in the event log but are not sent to the model.

You can also limit by approximate token count:

```python
chatbot = agent(
    system_prompt="...",
    model="openai/gpt-4o",
    working_memory=working_memory(max_tokens=4000),
)
```

### Pause and Resume

Working memory survives workflow pause and resume transparently:

```python
from flux.tasks.builtins import pause

chatbot = agent(
    system_prompt="You are a helpful assistant.",
    model="ollama/llama3.2",
    working_memory=working_memory(),
)

@workflow
async def conversation(ctx: ExecutionContext):
    r1 = await chatbot("Hello!")

    user_input = await pause("turn_1")   # workflow pauses here

    # After resume: the previous chatbot call replays from events.
    # The full conversation history is available to the LLM.
    r2 = await chatbot(user_input["message"])
    return r2
```

On resume, the workflow re-runs from the top. Completed tasks replay from the event log — no LLM calls are made. Only the first new `chatbot()` call after the resume point actually contacts the model, with the full prior conversation history.

## Long-term Memory

Long-term memory stores facts that persist across workflow executions. The LLM decides when to store and retrieve facts through tools that are automatically added to the agent.

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import working_memory, long_term_memory, sqlite

assistant = agent(
    system_prompt=(
        "You are a personal assistant. Remember important facts about the user "
        "using your memory tools. Always check memory at the start of a conversation."
    ),
    model="openai/gpt-4o",
    working_memory=working_memory(),
    long_term_memory=long_term_memory(
        provider=sqlite("memory.db"),
        scope="user:123",
    ),
)

@workflow
async def personal_assistant(ctx: ExecutionContext):
    return await assistant(ctx.input["message"])
```

### How Tools Work

When `long_term_memory` is provided, `agent()` automatically adds four tools to the agent's tool list and appends a hint to the system prompt:

| Tool | Description |
|------|-------------|
| `recall_memory(key="")` | Retrieve a specific fact by key, or all facts if key is empty |
| `store_memory(key, value)` | Store a fact under a key |
| `forget_memory(key="")` | Delete a specific fact, or all facts if key is empty |
| `list_memory_keys()` | List all keys currently stored |

The LLM calls these tools autonomously based on the conversation. No explicit calls are needed in workflow code.

### Scoping

Every `long_term_memory` instance has a `scope` that namespaces its facts. The workflow name is automatically injected as an additional dimension — two workflows using the same scope string do not share facts.

Use scopes to separate memory per user, session, resource, or any other boundary:

```python
# Per-user memory
long_term_memory(provider=sqlite("users.db"), scope="user:123")

# Per-session memory
long_term_memory(provider=sqlite("sessions.db"), scope="session:abc")

# Per-resource memory
long_term_memory(provider=sqlite("reviews.db"), scope="pr:456")
```

## Providers

### SQLite

Stores facts in a local SQLite database. Suitable for single-process deployments and development:

```python
from flux.tasks.ai.memory import sqlite

provider = sqlite("memory.db")
```

The database is created automatically on first use. The `memory` table uses `(workflow, scope, key)` as the primary key, so updates overwrite previous values for the same key.

### PostgreSQL

Stores facts in a PostgreSQL database. Suitable for multi-process or distributed deployments:

```python
from flux.tasks.ai.memory import postgresql

provider = postgresql("postgresql://user:password@localhost/mydb")
```

Requires the `psycopg2` package:

```bash
pip install flux-core[postgresql]
```

### In-Memory

Stores facts in process memory with no persistence. Intended for testing:

```python
from flux.tasks.ai.memory import in_memory

provider = in_memory()
```

All facts are lost when the process exits.

### Custom Providers

Any class implementing the `MemoryProvider` protocol works as a provider. Pass an instance directly to `long_term_memory()`:

```python
provider = MyCustomProvider(...)
memory = long_term_memory(provider=provider, scope="user:123")
```

Both SQLite and PostgreSQL providers require initialization before first use. Flux calls `initialize()` automatically when the agent first accesses memory.

## Shared Memory

Pass the same `long_term_memory` instance to multiple agents to give them a shared fact store:

```python
from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import long_term_memory, sqlite

shared = long_term_memory(provider=sqlite("review.db"), scope="pr:456")

reviewer = agent(
    system_prompt=(
        "You are a code reviewer. Store your findings using store_memory."
    ),
    model="openai/gpt-4o",
    long_term_memory=shared,
)

summarizer = agent(
    system_prompt=(
        "Use recall_memory and list_memory_keys to read the reviewer's findings, "
        "then write a concise summary."
    ),
    model="openai/gpt-4o",
    long_term_memory=shared,
)

@workflow
async def code_review(ctx: ExecutionContext):
    await reviewer(f"Review this code:\n\n{ctx.input['code']}")
    return await summarizer("Summarize the review findings.")
```

Both agents see the same keys and values within the `pr:456` scope. The reviewer writes findings; the summarizer reads them.

## Provider Protocol

Implement the `MemoryProvider` protocol to create a custom backend:

```python
from typing import Any, Protocol

class MemoryProvider(Protocol):
    async def memorize(self, workflow: str, scope: str, key: str, value: Any) -> None: ...
    async def recall(self, workflow: str, scope: str, key: str | None = None) -> Any: ...
    async def forget(self, workflow: str, scope: str, key: str | None = None) -> None: ...
    async def keys(self, workflow: str, scope: str) -> list[str]: ...
    async def scopes(self, workflow: str) -> list[str]: ...
```

The `workflow` parameter is injected automatically by `LongTermMemory` — provider implementations receive it but never need to inject it themselves.

Method semantics:

| Method | Behavior |
|--------|----------|
| `memorize` | Upsert: insert or overwrite the value for `(workflow, scope, key)` |
| `recall(key=None)` | Return the value for a specific key, or a dict of all key-value pairs if `key` is `None` |
| `forget(key=None)` | Delete a specific key, or all keys in the scope if `key` is `None` |
| `keys` | Return all keys for the given `(workflow, scope)` |
| `scopes` | Return all distinct scopes for the given workflow |

Optionally implement `async def initialize(self) -> None` if the provider needs setup (e.g., creating tables). Flux calls `initialize()` before the first memory access.

### Example: Redis Provider

```python
import json
from typing import Any

import redis.asyncio as redis


class RedisMemoryProvider:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client: redis.Redis | None = None

    async def initialize(self) -> None:
        self._client = redis.from_url(self._url)

    def _key(self, workflow: str, scope: str, key: str) -> str:
        return f"flux:memory:{workflow}:{scope}:{key}"

    def _pattern(self, workflow: str, scope: str) -> str:
        return f"flux:memory:{workflow}:{scope}:*"

    async def memorize(self, workflow: str, scope: str, key: str, value: Any) -> None:
        await self._client.set(self._key(workflow, scope, key), json.dumps(value))

    async def recall(self, workflow: str, scope: str, key: str | None = None) -> Any:
        if key is not None:
            raw = await self._client.get(self._key(workflow, scope, key))
            return json.loads(raw) if raw else None
        keys = await self._client.keys(self._pattern(workflow, scope))
        result = {}
        for k in keys:
            raw = await self._client.get(k)
            short_key = k.decode().split(":")[-1]
            result[short_key] = json.loads(raw)
        return result

    async def forget(self, workflow: str, scope: str, key: str | None = None) -> None:
        if key is not None:
            await self._client.delete(self._key(workflow, scope, key))
        else:
            keys = await self._client.keys(self._pattern(workflow, scope))
            if keys:
                await self._client.delete(*keys)

    async def keys(self, workflow: str, scope: str) -> list[str]:
        pattern = self._pattern(workflow, scope)
        all_keys = await self._client.keys(pattern)
        return [k.decode().split(":")[-1] for k in all_keys]

    async def scopes(self, workflow: str) -> list[str]:
        pattern = f"flux:memory:{workflow}:*"
        all_keys = await self._client.keys(pattern)
        scopes = set()
        for k in all_keys:
            parts = k.decode().split(":")
            if len(parts) >= 4:
                scopes.add(parts[3])
        return list(scopes)
```

Use the custom provider like any built-in:

```python
provider = RedisMemoryProvider("redis://localhost:6379")
memory = long_term_memory(provider=provider, scope="user:123")
```

## `working_memory()` Reference

```python
def working_memory(
    window: int | None = None,      # limit to the N most recent messages
    max_tokens: int | None = None,  # limit by approximate token count
) -> WorkingMemory:
```

## `long_term_memory()` Reference

```python
def long_term_memory(
    provider,       # MemoryProvider instance (sqlite, postgresql, in_memory, or custom)
    scope: str,     # namespace for facts (e.g. "user:123", "session:abc")
) -> LongTermMemory:
```

## `agent()` Memory Parameters

```python
agent(
    system_prompt: str,
    *,
    model: str,
    working_memory: WorkingMemory | None = None,    # conversation history
    long_term_memory: LongTermMemory | None = None, # persistent fact storage
    ...
)
```

Both parameters are optional and independent. Use either, both, or neither depending on the use case.
